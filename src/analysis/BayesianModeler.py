# region imports
from AlgorithmImports import *
import numpy as np
import pymc as pm
from typing import Dict, List, Tuple
from collections import defaultdict
# endregion


class BayesianModeler:
    """贝叶斯建模器 - 使用MCMC方法估计配对交易参数"""

    def __init__(self, algorithm, config: dict):
        self.algorithm = algorithm
        self.mcmc_warmup_samples = config['mcmc_warmup_samples']
        self.mcmc_posterior_samples = config['mcmc_posterior_samples']
        self.mcmc_chains = config['mcmc_chains']
        self.lookback_days = config['lookback_days']
        self.bayesian_priors = config['bayesian_priors']                # 贝叶斯先验配置（无信息/信息先验）
        self.historical_posteriors = {}                                 # 内部管理历史后验，实现动态更新


    def _cleanup_historical_posteriors(self):
        """
        清理过期的历史后验记录，避免内存无限增长
        清理规则：删除超过 2 * lookback_days 天的记录
        """
        if not self.historical_posteriors:
            return

        current_time = self.algorithm.Time
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
                f"[BayesianModeler] 清理了{len(pairs_to_remove)}个过期的历史后验记录", 2
            )


    def modeling_procedure(self, cointegrated_pairs: List[Dict], clean_data: Dict) -> List[Dict]:
        """
        执行贝叶斯建模流程 - 对所有协整对进行参数估计

        Returns:
            List[Dict]: 建模结果列表，每个元素包含配对的完整模型参数
        """
        # 清理过期的历史后验
        self._cleanup_historical_posteriors()

        # 里面的元素是字典结构，每一个元素都是一个协整对的信息信息，包括 pair_id, 后验，行业分类等
        modeling_results = []
        
        statistics = defaultdict(int, total_pairs=len(cointegrated_pairs))

        for pair in cointegrated_pairs:
            result = self._model_single_pair(pair, clean_data)
            if result:
                modeling_results.append(result)
                statistics['successful'] += 1
                statistics[f"{result['modeling_type']}_modeling"] += 1
            else:
                statistics['failed'] += 1

        self._log_statistics(dict(statistics))

        return modeling_results


    def _model_single_pair(self, pair: Dict, clean_data: Dict) -> Dict:
        """处理单个配对的建模"""
        try:
            # 符号顺序已在CointegrationAnalyzer中规范化
            symbol1, symbol2 = pair['symbol1'], pair['symbol2']

            # 提取价格数据
            prices1 = clean_data[symbol1]['close'].values
            prices2 = clean_data[symbol2]['close'].values

            # 先验数据：构造或者将历史后验作为先验输入
            prior_params = self._setup_prior_params(symbol1, symbol2)

            # 执行贝叶斯建模
            trace, x_data, y_data = self._build_and_sample_model(prices1, prices2, prior_params)

            # 提取并保存后验统计量（合并函数：同时返回和保存）
            posterior_stats = self._extract_and_save_posterior_stats(trace, x_data, y_data, symbol1, symbol2)

            # 构建结果（核心字段优先，最后合并统计量）
            result = {
                'symbol1': symbol1,
                'symbol2': symbol2,
                'sector': pair['sector'],
                'quality_score': pair['quality_score'],  # 必须存在，由 PairSelector 提供
                'modeling_type': prior_params['prior_type'],  # 使用实际的先验类型
                'modeling_time': self.algorithm.Time,
                **posterior_stats  # 展开后验统计量字典
            }

            return result

        except Exception as e:
            self.algorithm.Debug(f"[BayesianModeler] 建模失败 {symbol1.Value}&{symbol2.Value}: {str(e)}", 2)
            return None


    def _setup_prior_params(self, symbol1: Symbol, symbol2: Symbol) -> Dict:
        """
        无信息的情况下构造先验参数
        有信息的情况下获取后验数据作为先验参数
        注意：只返回参数值，不创建分布对象
        """
        pair_key = (symbol1, symbol2)

        if (pair_key not in self.historical_posteriors or
        (self.algorithm.Time - self.historical_posteriors[pair_key]['update_time']).days > self.lookback_days):
            # 无信息先验
            uninformed = self.bayesian_priors['uninformed']
            params = {
                'alpha_mu': 0,
                'alpha_sigma': uninformed['alpha_sigma'],
                'beta_mu': 1,
                'beta_sigma': uninformed['beta_sigma'],
                'sigma_sigma': uninformed['sigma_sigma'],
                'tune': self.mcmc_warmup_samples,
                'draws': self.mcmc_posterior_samples,
                'prior_type': 'full'  # 完全建模
            }
        else:
            # 信息先验（使用历史后验）
            informed = self.bayesian_priors['informed']
            reduction_factor = informed['sample_reduction_factor']
            sigma_multiplier = informed['sigma_multiplier']

            # 从历史后验中提取参数
            historical = self.historical_posteriors[pair_key]

            # sigma使用历史均值和标准差信息，通过放大系数避免过度收缩
            sigma_prior = max(historical['sigma_std'] * sigma_multiplier, historical['sigma_mean'] * 0.5)

            params = {
                'alpha_mu': historical['alpha_mean'],
                'alpha_sigma': historical['alpha_std'],
                'beta_mu': historical['beta_mean'],
                'beta_sigma': historical['beta_std'],
                'sigma_sigma': sigma_prior,
                'tune': int(self.mcmc_warmup_samples * reduction_factor),
                'draws': int(self.mcmc_posterior_samples * reduction_factor),
                'prior_type': 'dynamic'  # 动态更新
            }

        return params


    def _build_and_sample_model(self, prices1: np.ndarray, prices2: np.ndarray, prior_params: Dict):
        """
        构建贝叶斯线性回归模型并执行MCMC采样
        模型: log(price1) = alpha + beta * log(price2) + epsilon
        """
        # 对数转换
        x_data = np.log(prices2)
        y_data = np.log(prices1)

        # 提取采样参数
        tune = prior_params['tune']
        draws = prior_params['draws']

        with pm.Model() as model:
            # 在模型上下文中创建先验分布
            alpha = pm.Normal('alpha',
                            mu=prior_params['alpha_mu'],
                            sigma=prior_params['alpha_sigma'])

            beta = pm.Normal('beta',
                           mu=prior_params['beta_mu'],
                           sigma=prior_params['beta_sigma'])

            sigma = pm.HalfNormal('sigma',
                                sigma=prior_params['sigma_sigma'])

            # 定义线性关系
            mu = alpha + beta * x_data

            # 定义似然函数
            likelihood = pm.Normal('y', mu=mu, sigma=sigma, observed=y_data)

            # 计算残差
            residuals = pm.Deterministic('residuals', y_data - mu)

            # MCMC采样
            trace = pm.sample(
                draws=draws,
                tune=tune,
                chains=self.mcmc_chains,
                return_inferencedata=False,
                progressbar=False
            )

        return trace, x_data, y_data


    def _extract_and_save_posterior_stats(self, trace, x_data, y_data, symbol1: Symbol, symbol2: Symbol) -> Dict:
        """
        提取后验统计量并保存到历史记录

        Returns:
            Dict: 包含所有后验统计量的字典，可直接用于构建结果
        """
        # 提取后验样本
        alpha_samples = trace['alpha']
        beta_samples = trace['beta']
        sigma_samples = trace['sigma']
        residuals_samples = trace['residuals']

        # 计算统计量
        stats = {
            # 参数统计
            'alpha_mean': float(np.mean(alpha_samples)),
            'alpha_std': float(np.std(alpha_samples)),
            'beta_mean': float(np.mean(beta_samples)),
            'beta_std': float(np.std(beta_samples)),
            'sigma_mean': float(np.mean(sigma_samples)),
            'sigma_std': float(np.std(sigma_samples)),

            # 残差统计（用于Z-score信号生成）
            'residual_mean': float(np.mean(residuals_samples)),  # 理论上接近0
            'residual_std': float(np.std(residuals_samples)),

            # 更新时间
            'update_time': self.algorithm.Time
        }

        # 保存到历史后验（供未来作为先验使用）
        pair_key = (symbol1, symbol2)
        self.historical_posteriors[pair_key] = stats.copy()  # 保存副本

        return stats  # 返回统计量供立即使用


    def _log_statistics(self, statistics: dict):
        """
        输出建模统计信息
        """
        successful = statistics.get('successful', 0)
        failed = statistics.get('failed', 0)
        full = statistics.get('full_modeling', 0)
        dynamic = statistics.get('dynamic_modeling', 0)

        self.algorithm.Debug(
            f"[BayesianModeler] 建模完成: 成功{successful}对, 失败{failed}对 "
            f"(完全建模{full}对, 动态更新{dynamic}对)", 2
        )