# region imports
from AlgorithmImports import *
import numpy as np
import pymc as pm
from typing import Dict, List, Tuple
from collections import defaultdict
from src.analysis.PairData import PairData
# endregion


class BayesianModeler:
    """贝叶斯建模器 - 使用MCMC方法估计配对交易参数"""

    def __init__(self, algorithm, shared_config: dict, module_config: dict):
        """
        初始化贝叶斯建模器

        Args:
            algorithm: QCAlgorithm实例
            shared_config: 共享配置(analysis_shared)
            module_config: 模块配置(bayesian_modeler)
        """
        self.algorithm = algorithm
        self.lookback_days = shared_config['lookback_days']
        self.mcmc_warmup_samples = module_config['mcmc_warmup_samples']
        self.mcmc_posterior_samples = module_config['mcmc_posterior_samples']
        self.mcmc_chains = module_config['mcmc_chains']
        self.bayesian_priors = module_config['bayesian_priors']  # 贝叶斯先验配置(无信息/信息先验)
        self.historical_posteriors = {}  # 内部管理历史后验,实现动态更新


    def _cleanup_historical_posteriors(self):
        """
        清理过期的历史后验记录，避免内存无限增长
        清理规则：删除超过 2 * lookback_days 天的记录
        """
        if not self.historical_posteriors:
            return

        current_time = self.algorithm.UtcTime
        expired_threshold = 2 * self.lookback_days  # 双倍lookback作为过期阈值

        # 找出需要清理的配对
        pairs_to_remove = []
        for pair_key, posterior in self.historical_posteriors.items():
            if (current_time - posterior['update_time']).days > expired_threshold:
                pairs_to_remove.append(pair_key)

        # 执行清理
        for pair_key in pairs_to_remove:
            del self.historical_posteriors[pair_key]

        # 记录清理情况
        if pairs_to_remove:
            self.algorithm.Debug(
                f"[BayesianModeler] 清理了{len(pairs_to_remove)}个过期的历史后验记录"
            )


    def modeling_procedure(self, cointegrated_pairs: List[Dict], pair_data_dict: Dict) -> List[Dict]:
        """
        执行贝叶斯建模流程 - 对所有协整对进行参数估计（重构版）

        Args:
            cointegrated_pairs: 配对列表
            pair_data_dict: {pair_key: PairData} 预构建的PairData对象字典（从PairSelector传入）

        Returns:
            List[Dict]: 建模结果列表，每个元素包含配对的完整模型参数

        性能优化:
        - 复用PairSelector构建的PairData对象，避免重复对数转换
        """
        # 清理过期的历史后验
        self._cleanup_historical_posteriors()

        # 里面的元素是字典结构，每一个元素都是一个协整对的信息信息，包括 pair_id, 后验，行业分类等
        modeling_results = []

        statistics = defaultdict(int, total_pairs=len(cointegrated_pairs))

        for pair in cointegrated_pairs:
            result = self._model_single_pair(pair, pair_data_dict)
            if result:
                modeling_results.append(result)
                statistics['successful'] += 1
                statistics[f"{result['modeling_type']}_modeling"] += 1
            else:
                statistics['failed'] += 1

        self._log_statistics(dict(statistics))

        return modeling_results


    def _model_single_pair(self, pair: Dict, pair_data_dict: Dict) -> Dict:
        """单个配对的建模流程（重构版）"""
        try:
            # Step 1: 数据层（从pair_data_dict获取预构建的PairData对象）
            pair_key = (pair['symbol1'], pair['symbol2'])
            pair_data = pair_data_dict[pair_key]

            # Step 2: 先验层（三级策略）
            prior_params, prior_type = self._select_prior(pair, pair_data.pair_key)

            # Step 3: 建模层
            trace = self._sample_posterior(pair_data, prior_params)

            # Step 4: 后验层
            posterior_stats = self._extract_posterior_stats(trace, pair_data)

            # Step 5: 结果构建
            result = self._build_result(pair, pair_data, prior_type, posterior_stats)

            return result

        except Exception as e:
            self.algorithm.Debug(f"[BayesianModeler] 建模失败: {str(e)}")
            return None


    # ===== 先验选择系统 (三级策略) =====

    def _select_prior(self, pair_info: Dict, pair_key: tuple) -> tuple:
        """先验选择策略（三级体系）"""
        # Level 1: 历史后验先验（强信息）
        if self._has_valid_historical_posterior(pair_key):
            return self._create_historical_prior(pair_key), 'historical_posterior'

        # Level 2: OLS弱信息先验（使用PairSelector的OLS结果）
        if pair_info.get('ols_beta') is not None and pair_info.get('ols_alpha') is not None:
            return self._create_ols_prior(pair_info), 'ols_informed'

        # Level 3: 完全无信息先验（降级方案）
        self.algorithm.Debug(f"[BayesianModeler] {pair_key} 使用完全无信息先验 (原因: 无历史后验且OLS失败)")
        return self._create_uninformed_prior(), 'uninformed'


    def _has_valid_historical_posterior(self, pair_key: tuple) -> bool:
        """检查是否存在有效的历史后验"""
        if pair_key not in self.historical_posteriors:
            return False

        validity_days = self.bayesian_priors['informed'].get('validity_days', 60)
        days_old = (self.algorithm.UtcTime - self.historical_posteriors[pair_key]['update_time']).days

        return days_old <= validity_days


    def _create_ols_prior(self, pair_info: Dict) -> Dict:
        """创建OLS弱信息先验（关键优化）"""
        config = self.bayesian_priors['uninformed']

        return {
            'alpha_mu': pair_info['ols_alpha'],
            'alpha_sigma': config['alpha_sigma'],
            'beta_mu': pair_info['ols_beta'],
            'beta_sigma': config['beta_sigma'],
            'sigma_sigma': config['sigma_sigma'],
            'tune': self.mcmc_warmup_samples,
            'draws': self.mcmc_posterior_samples,
        }


    def _create_historical_prior(self, pair_key: tuple) -> Dict:
        """创建历史后验先验"""
        config = self.bayesian_priors['informed']
        historical = self.historical_posteriors[pair_key]

        sigma_prior = max(
            historical['sigma_std'] * config['sigma_multiplier'],
            historical['sigma_mean'] * 1.0
        )

        return {
            'alpha_mu': historical['alpha_mean'],
            'alpha_sigma': historical['alpha_std'],
            'beta_mu': historical['beta_mean'],
            'beta_sigma': historical['beta_std'],
            'sigma_sigma': sigma_prior,
            'tune': int(self.mcmc_warmup_samples * config['sample_reduction_factor']),
            'draws': int(self.mcmc_posterior_samples * config['sample_reduction_factor']),
        }


    def _create_uninformed_prior(self) -> Dict:
        """创建完全无信息先验（降级方案）"""
        config = self.bayesian_priors['uninformed']

        return {
            'alpha_mu': 0,
            'alpha_sigma': config['alpha_sigma'],
            'beta_mu': 1,
            'beta_sigma': config['beta_sigma'],
            'sigma_sigma': config['sigma_sigma'],
            'tune': self.mcmc_warmup_samples,
            'draws': self.mcmc_posterior_samples,
        }


    # ===== 建模执行系统 =====


    def _sample_posterior(self, pair_data: PairData, prior_params: Dict):
        """执行MCMC采样"""
        x_data = pair_data.log_prices2
        y_data = pair_data.log_prices1

        with pm.Model() as model:
            alpha = pm.Normal('alpha', mu=prior_params['alpha_mu'], sigma=prior_params['alpha_sigma'])
            beta = pm.Normal('beta', mu=prior_params['beta_mu'], sigma=prior_params['beta_sigma'])
            sigma = pm.HalfNormal('sigma', sigma=prior_params['sigma_sigma'])

            mu = alpha + beta * x_data
            likelihood = pm.Normal('y', mu=mu, sigma=sigma, observed=y_data)
            residuals = pm.Deterministic('residuals', y_data - mu)

            trace = pm.sample(
                draws=prior_params['draws'],
                tune=prior_params['tune'],
                chains=self.mcmc_chains,
                return_inferencedata=False,
                progressbar=False
            )

        return trace


    def _extract_posterior_stats(self, trace, pair_data: PairData) -> Dict:
        """提取后验统计量并保存到历史记录"""
        stats = {
            'alpha_mean': float(np.mean(trace['alpha'])),
            'alpha_std': float(np.std(trace['alpha'])),
            'beta_mean': float(np.mean(trace['beta'])),
            'beta_std': float(np.std(trace['beta'])),
            'sigma_mean': float(np.mean(trace['sigma'])),
            'sigma_std': float(np.std(trace['sigma'])),
            'residual_mean': float(np.mean(trace['residuals'])),
            'residual_std': float(np.std(trace['residuals'])),
            'update_time': self.algorithm.UtcTime
        }

        self.historical_posteriors[pair_data.pair_key] = stats.copy()

        return stats


    def _build_result(self, pair_info: Dict, pair_data: PairData,
                     prior_type: str, posterior_stats: Dict) -> Dict:
        """构建建模结果字典"""
        return {
            'symbol1': pair_data.symbol1,
            'symbol2': pair_data.symbol2,
            'industry_group': pair_info['industry_group'],
            'quality_score': pair_info['quality_score'],
            'modeling_type': prior_type,
            'modeling_time': self.algorithm.Time,
            **posterior_stats
        }


    def _log_statistics(self, statistics: dict):
        """输出建模统计信息（重构版）"""
        successful = statistics.get('successful', 0)
        failed = statistics.get('failed', 0)
        ols_informed = statistics.get('ols_informed_modeling', 0)
        historical_posterior = statistics.get('historical_posterior_modeling', 0)
        uninformed = statistics.get('uninformed_modeling', 0)

        self.algorithm.Debug(
            f"[BayesianModeler] 建模完成: 成功{successful}对, 失败{failed}对 "
            f"(OLS弱信息{ols_informed}对, 历史后验{historical_posterior}对, 完全无信息{uninformed}对)"
        )
