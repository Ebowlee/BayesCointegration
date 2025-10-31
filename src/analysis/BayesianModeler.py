# region imports
from AlgorithmImports import *
import numpy as np
import pymc as pm
from typing import Dict, List
from collections import defaultdict
from src.analysis.PairData import PairData
# endregion


class BayesianModeler:
    """贝叶斯建模器 - 单一联合贝叶斯模型"""

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
        self.bayesian_priors = module_config['bayesian_priors']
        self.joint_config = self.bayesian_priors['joint_single_stage']
        self.historical_posteriors = {}  # 历史后验管理


    # ===== 主流程方法 =====

    def modeling_procedure(self, cointegrated_pairs: List[Dict], pair_data_dict: Dict) -> List[Dict]:
        """
        执行贝叶斯建模流程 - 对所有协整对进行参数估计

        Args:
            cointegrated_pairs: 配对列表
            pair_data_dict: {pair_key: PairData} 预构建的PairData对象字典

        Returns:
            List[Dict]: 建模结果列表
        """
        self._cleanup_historical_posteriors()

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
        """单个配对的建模流程"""
        try:
            pair_key = (pair['symbol1'], pair['symbol2'])
            pair_data = pair_data_dict[pair_key]

            # 先验选择
            prior_params, prior_type = self._select_prior(pair_data.pair_key)

            # 单一联合建模
            posterior_stats = self._fit_joint_model(pair_data, prior_params)

            # 构建结果
            result = self._build_result(pair, pair_data, prior_type, posterior_stats)

            return result

        except Exception as e:
            self.algorithm.Debug(f"[BayesianModeler] 建模失败: {str(e)}")
            return None


    # ===== 先验选择 =====

    def _select_prior(self, pair_key: tuple) -> tuple:
        """
        先验选择策略（二级体系）
        - Level 1: 历史后验先验（强信息）
        - Level 2: 完全无信息先验（默认）
        """
        if self._has_valid_historical_posterior(pair_key):
            return self._create_historical_prior(pair_key), 'historical_posterior'

        return self._create_uninformed_prior(), 'uninformed'


    def _has_valid_historical_posterior(self, pair_key: tuple) -> bool:
        """检查是否存在有效的历史后验"""
        if pair_key not in self.historical_posteriors:
            return False

        validity_days = self.bayesian_priors['informed'].get('validity_days', 60)
        days_old = (self.algorithm.UtcTime - self.historical_posteriors[pair_key]['update_time']).days

        return days_old <= validity_days


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
        """创建完全无信息先验"""
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


    # ===== 单一联合贝叶斯模型 =====

    def _fit_joint_model(self, pair_data: PairData, prior_params: Dict) -> Dict:
        """
        单一联合贝叶斯模型 - β, α, ρ同时估计

        核心设计:
        - 直接建模y_t，绕过PyMC observed约束
        - 数学变换: y_t = α(1-ρ) + β(x_t - ρx_{t-1}) + ρy_{t-1} + η_t
        - β, α使用先验（历史后验或无信息）
        - ρ使用Uniform(0, 1)先验确保平稳性

        模型假设:
        - 原始残差ε_t = y_t - α - βx_t 服从AR(1)过程
        - ε_t = ρε_{t-1} + η_t, 其中η_t ~ N(0, σ²), ρ ∈ (0,1)
        - 半衰期: half_life = -ln(2) / ln(ρ)

        Args:
            pair_data: PairData对象
            prior_params: 先验参数字典

        Returns:
            Dict: 后验统计量（包含rho_mean, rho_std, half_life_mean等）
        """
        try:
            # 提取数据
            y_data = pair_data.log_prices1
            x_data = pair_data.log_prices2

            # 构建AR(1)数据（时间维度-1）
            y_curr = y_data[1:]
            y_lag = y_data[:-1]
            x_curr = x_data[1:]
            x_lag = x_data[:-1]

            with pm.Model():
                # 协整参数（使用先验,放宽2.5倍以允许数据纠偏）
                beta = pm.Normal('beta', mu=prior_params['beta_mu'], sigma=prior_params['beta_sigma'] * 2.5)
                alpha = pm.Normal('alpha', mu=prior_params['alpha_mu'], sigma=prior_params['alpha_sigma'] * 2.5)

                # AR(1)参数（使用rho确保平稳性）
                # rho ∈ (0,1) 确保平稳性,直接用于计算半衰期和均值回归速度
                rho = pm.Uniform('rho', lower=0.01, upper=0.99)
                sigma_eta = pm.HalfNormal('sigma_eta', sigma=self.joint_config['sigma_ar'])

                # 派生量(半衰期后验分布,用于直接提取统计量)
                half_life = pm.Deterministic('half_life', -pm.math.log(2) / pm.math.log(rho))

                # 构建mu（数学变换后的期望）
                # 推导: ε_t = ρε_{t-1} + η_t
                #      y_t - α - βx_t = ρ(y_{t-1} - α - βx_{t-1}) + η_t
                #      y_t = α(1-ρ) + β(x_t - ρx_{t-1}) + ρy_{t-1} + η_t
                mu = alpha * (1 - rho) + beta * (x_curr - rho * x_lag) + rho * y_lag

                # Likelihood
                likelihood = pm.Normal('y_obs', mu=mu, sigma=sigma_eta, observed=y_curr)

                # MCMC采样
                trace = pm.sample(
                    draws=self.joint_config['mcmc_draws'],
                    tune=self.joint_config['mcmc_warmup'],
                    chains=self.mcmc_chains,
                    return_inferencedata=False,
                    progressbar=False
                )

            # 提取后验样本（原始MCMC样本，供PairSelector按需计算）
            rho_samples = trace['rho'].flatten()

            # β后验统计
            beta_mean = float(np.mean(trace['beta']))
            beta_std = float(np.std(trace['beta']))

            # α后验统计
            alpha_mean = float(np.mean(trace['alpha']))
            alpha_std = float(np.std(trace['alpha']))

            # σ后验统计(创新噪声η)
            sigma_mean = float(np.mean(trace['sigma_eta']))
            sigma_std = float(np.std(trace['sigma_eta']))

            # v7.5.6: 计算spread (协整残差,供PairSelector评分使用)
            spread = y_data - beta_mean * x_data

            # 构建后验统计字典
            stats = {
                # 协整参数
                'alpha_mean': alpha_mean,
                'alpha_std': alpha_std,
                'beta_mean': beta_mean,
                'beta_std': beta_std,
                'sigma_mean': sigma_mean,
                'sigma_std': sigma_std,
                # AR(1)参数（v7.5.5: 仅保留原始MCMC样本,供PairSelector按需计算）
                'rho_samples': rho_samples,
                # v7.5.6: 协整残差 (供PairSelector计算RRS使用)
                'spread': spread,
                # 元信息
                'method': 'joint_bayesian',
                'update_time': self.algorithm.UtcTime
            }

            # 保存到历史后验
            self.historical_posteriors[pair_data.pair_key] = stats.copy()

            return stats

        except Exception as e:
            # 建模失败时返回默认值
            self.algorithm.Debug(f"[BayesianModeler] 联合建模失败: {str(e)}")
            return {
                'alpha_mean': 0.0,
                'alpha_std': 0.0,
                'beta_mean': 1.0,
                'beta_std': 0.0,
                'sigma_mean': 0.01,
                'sigma_std': 0.0,
                'rho_samples': np.array([0.5]),  # v7.5.5: 降级默认值
                'spread': np.array([0.0]),  # v7.5.6: 降级默认值
                'method': 'joint_bayesian_failed',
                'update_time': self.algorithm.UtcTime
            }


    # ===== 结果处理 =====

    def _build_result(self, pair_info: Dict, pair_data: PairData,
                     prior_type: str, posterior_stats: Dict) -> Dict:
        """构建建模结果字典"""
        result = {
            'symbol1': pair_data.symbol1,
            'symbol2': pair_data.symbol2,
            'industry_group': pair_info['industry_group'],
            'modeling_type': prior_type,
            'modeling_time': self.algorithm.Time,
            **posterior_stats
        }

        # 字段映射: PairSelector期望residual_std (实际为sigma_mean)
        result['residual_std'] = posterior_stats['sigma_mean']

        return result


    def _log_statistics(self, statistics: dict):
        """输出建模统计信息"""
        successful = statistics.get('successful', 0)
        failed = statistics.get('failed', 0)
        historical_posterior = statistics.get('historical_posterior_modeling', 0)
        uninformed = statistics.get('uninformed_modeling', 0)

        self.algorithm.Debug(
            f"[BayesianModeler] 建模完成: 成功{successful}对, 失败{failed}对 "
            f"(历史后验{historical_posterior}对, 无信息先验{uninformed}对)"
        )


    # ===== 辅助方法 =====

    def _cleanup_historical_posteriors(self):
        """
        清理过期的历史后验记录
        清理规则：删除超过 2 * lookback_days 天的记录
        """
        if not self.historical_posteriors:
            return

        current_time = self.algorithm.UtcTime
        expired_threshold = 2 * self.lookback_days

        pairs_to_remove = []
        for pair_key, posterior in self.historical_posteriors.items():
            if (current_time - posterior['update_time']).days > expired_threshold:
                pairs_to_remove.append(pair_key)

        for pair_key in pairs_to_remove:
            del self.historical_posteriors[pair_key]

        if pairs_to_remove:
            self.algorithm.Debug(
                f"[BayesianModeler] 清理了{len(pairs_to_remove)}个过期的历史后验记录"
            )
