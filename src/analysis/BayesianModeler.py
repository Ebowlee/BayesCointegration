# region imports
from AlgorithmImports import *
import numpy as np
import pymc as pm
from typing import Dict, List, Tuple
from collections import defaultdict
# endregion


class BayesianModeler:
    """贝叶斯建模器 - 使用MCMC方法估计配对交易参数"""

    def __init__(self, algorithm, config: dict, state=None):
        self.algorithm = algorithm
        self.mcmc_warmup_samples = config['mcmc_warmup_samples']
        self.mcmc_posterior_samples = config['mcmc_posterior_samples']
        self.mcmc_chains = config['mcmc_chains']
        self.lookback_period = config['lookback_period']
        self.state = state  # 现在是可选的，OnData架构不使用

    def model_pairs(self, cointegrated_pairs: List[Dict], clean_data: Dict) -> Dict:
        """对所有协整对进行贝叶斯建模"""
        modeled_pairs = []
        statistics = defaultdict(int, total_pairs=len(cointegrated_pairs))

        for pair in cointegrated_pairs:
            result = self._model_single_pair(pair, clean_data)
            if result:
                modeled_pairs.append(result)
                statistics['successful'] += 1
                statistics[f"{result['modeling_type']}_modeling"] += 1
            else:
                statistics['failed'] += 1

        self._log_statistics(dict(statistics))

        return {
            'modeled_pairs': modeled_pairs,
            'statistics': dict(statistics)
        }

    def _model_single_pair(self, pair: Dict, clean_data: Dict) -> Dict:
        """处理单个配对的建模"""
        try:
            symbol1, symbol2 = pair['symbol1'], pair['symbol2']

            # 验证顺序（防御性编程）
            if symbol1.Value > symbol2.Value:
                self.algorithm.Debug(f"[警告] 配对顺序异常: {symbol1.Value} > {symbol2.Value}", 1)
                # 自动修正顺序
                symbol1, symbol2 = symbol2, symbol1

            # 提取价格数据
            prices1 = clean_data[symbol1]['close'].values
            prices2 = clean_data[symbol2]['close'].values

            # 获取历史后验（动态贝叶斯更新）
            prior_params = self._get_prior_params(symbol1, symbol2)

            # 执行贝叶斯建模
            trace, x_data, y_data = self._build_and_sample_model(prices1, prices2, prior_params)

            # 构建结果
            result = {
                **self._extract_posterior_stats(trace, x_data, y_data),
                'symbol1': symbol1,
                'symbol2': symbol2,
                'sector': pair['sector'],
                'quality_score': pair['quality_score'],  # 必须存在，由 PairSelector 提供
                'modeling_type': 'dynamic' if prior_params else 'full',
                'modeling_time': self.algorithm.Time
            }

            # 保存后验
            self._save_posterior(symbol1, symbol2, result)

            return result

        except Exception:
            return None

    def _get_prior_params(self, symbol1: Symbol, symbol2: Symbol) -> Dict:
        """获取历史后验参数"""
        pair_key = tuple(sorted([symbol1, symbol2]))

        historical_posteriors = self.state.persistent['historical_posteriors'] if self.state else {}
        if pair_key not in historical_posteriors:
            return None

        historical = historical_posteriors[pair_key]
        time_diff = self.algorithm.Time - historical['update_time']

        if time_diff.days > self.lookback_period:
            return None

        return historical

    def _build_and_sample_model(self, prices1: np.ndarray, prices2: np.ndarray,
                                prior_params: Dict = None) -> Tuple[pm.backends.base.MultiTrace, np.ndarray, np.ndarray]:
        """
        构建贝叶斯线性回归模型并执行MCMC采样
        模型: log(price1) = alpha + beta * log(price2) + epsilon
        """
        # 对数转换
        x_data = np.log(prices2)
        y_data = np.log(prices1)

        with pm.Model() as model:
            # 设置先验
            priors = self._setup_priors(prior_params)
            alpha = priors['alpha']
            beta = priors['beta']
            sigma = priors['sigma']
            tune = priors['tune']
            draws = priors['draws']

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

    def _setup_priors(self, prior_params: Dict):
        """设置模型先验"""
        if prior_params is None:
            # 无信息先验（首次建模）
            return {
                'alpha': pm.Normal('alpha', mu=0, sigma=10),
                'beta': pm.Normal('beta', mu=1, sigma=5),
                'sigma': pm.HalfNormal('sigma', sigma=5.0),
                'tune': self.mcmc_warmup_samples,
                'draws': self.mcmc_posterior_samples
            }
        else:
            # 信息先验（动态更新）
            return {
                'alpha': pm.Normal('alpha', mu=prior_params['alpha_mean'], sigma=prior_params['alpha_std']),
                'beta': pm.Normal('beta', mu=prior_params['beta_mean'], sigma=prior_params['beta_std']),
                'sigma': pm.HalfNormal('sigma', sigma=max(prior_params['sigma_mean'] * 1.5, 1.0)),
                'tune': self.mcmc_warmup_samples // 2,
                'draws': self.mcmc_posterior_samples // 2
            }

    def _extract_posterior_stats(self, trace, x_data, y_data) -> Dict:
        """从MCMC采样结果中提取后验统计量"""
        # 提取样本
        samples = {
            'alpha': trace['alpha'].flatten(),
            'beta': trace['beta'].flatten(),
            'sigma': trace['sigma'].flatten(),
            'residuals': trace['residuals'].flatten()
        }

        # 计算统计量
        stats = {f"{key}_mean": float(val.mean()) for key, val in samples.items() if key != 'residuals'}
        stats.update({f"{key}_std": float(val.std()) for key, val in samples.items() if key in ['alpha', 'beta', 'sigma']})
        stats['residual_mean'] = float(samples['residuals'].mean())

        # 计算实际残差标准差
        alpha_mean = stats['alpha_mean']
        beta_mean = stats['beta_mean']
        fitted_values = alpha_mean + beta_mean * x_data
        actual_residuals = y_data - fitted_values

        stats['residual_std'] = float(np.std(actual_residuals))

        # 保存残差序列供后续分析（如半衰期计算）
        stats['residuals_array'] = actual_residuals

        return stats

    def _save_posterior(self, symbol1: Symbol, symbol2: Symbol, stats: Dict):
        """保存后验参数"""
        pair_key = tuple(sorted([symbol1, symbol2]))

        keys_to_save = ['alpha_mean', 'alpha_std', 'beta_mean', 'beta_std', 'sigma_mean', 'sigma_std']
        if self.state:  # 仅在state存在时保存
            self.state.persistent['historical_posteriors'][pair_key] = {
                **{k: stats[k] for k in keys_to_save},
                'update_time': self.algorithm.Time
            }

    def _log_statistics(self, statistics: dict):
        """输出建模统计信息"""
        successful = statistics.get('successful', 0)
        failed = statistics.get('failed', 0)
        full = statistics.get('full_modeling', 0)
        dynamic = statistics.get('dynamic_modeling', 0)

        self.algorithm.Debug(
            f"[AlphaModel.Bayesian] 建模完成: 成功{successful}对, 失败{failed}对 "
            f"(完全建模{full}对, 动态更新{dynamic}对)", 2
        )