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

    # 类常量 (v7.31.3): 协整参数先验放宽倍数
    PRIOR_RELAXATION_FACTOR = 2.5

    def __init__(self, algorithm, analysis_config, bayesian_config):
        """
        初始化贝叶斯建模器

        Args:
            algorithm: QCAlgorithm实例
            analysis_config: AnalysisConfig dataclass实例
            bayesian_config: BayesianModelerConfig dataclass实例
        """
        self.algorithm = algorithm
        self.lookback_days = analysis_config.lookback_days
        self.mcmc_chains = bayesian_config.mcmc_chains
        self.uninformed_prior = bayesian_config.uninformed
        self.informed_prior = bayesian_config.informed
        self.joint_config = bayesian_config.joint_single_stage
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

        # v7.28.4: 贝叶斯建模日志总是打印（核心环节，确保可见性）
        success_count = statistics['successful']
        fail_count = statistics['failed']
        total_count = len(cointegrated_pairs)

        # 总是打印（无论成功/失败）
        self.algorithm.Debug(
            f"[贝叶斯建模] 输入{total_count}对 → "
            f"成功{success_count}对 → 失败{fail_count}对",
            level=1
        )

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
            if self.algorithm.debug_mode:
                self.algorithm.Debug(f"[BayesianModeler] 建模失败: {str(e)}")
            return None


    # ===== 先验选择 =====

    def _beta_moment_matching(self, mean: float, var: float, config) -> tuple:
        """
        Beta分布矩匹配 (v7.5.20)

        Args:
            mean: 后验均值 (ρ̄)
            var: 后验方差 (Var(ρ))
            config: InformedPriorConfig dataclass实例

        Returns:
            (alpha, beta): Beta分布参数
        """
        # 温度化放宽
        var_prior = var * config.rho_variance_multiplier

        # 安全夹紧 (防止var超过m(1-m)导致无解)
        max_var = config.rho_variance_safety * mean * (1 - mean)
        var_safe = min(var_prior, max_var)

        # 数值稳定性: var太小会导致A+B极大
        var_safe = max(var_safe, 1e-6)

        # 矩匹配
        A_plus_B = mean * (1 - mean) / var_safe - 1
        A = mean * A_plus_B
        B = (1 - mean) * A_plus_B

        # 安全检查
        if A <= 0 or B <= 0:
            if self.algorithm.debug_mode:
                self.algorithm.Debug(f"[BayesianModeler] Beta矩匹配失败 (A={A:.3f}, B={B:.3f}), 降级到Beta(2,2)")
            return (2.0, 2.0)

        return (A, B)

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

        validity_days = self.informed_prior.validity_days
        days_old = (self.algorithm.UtcTime - self.historical_posteriors[pair_key]['update_time']).days

        return days_old <= validity_days


    def _create_historical_prior(self, pair_key: tuple) -> Dict:
        """创建历史后验先验 (v7.5.20: 添加ρ/σ_η历史先验)"""
        config = self.informed_prior
        historical = self.historical_posteriors[pair_key]

        sigma_prior = max(
            historical['sigma_std'] * config.sigma_multiplier,
            historical['sigma_mean'] * 1.0
        )

        # v7.5.20: ρ的Beta先验 (矩匹配)
        rho_alpha, rho_beta = self._beta_moment_matching(
            mean=historical['rho_mean'],
            var=historical['rho_std'] ** 2,
            config=config
        )

        # v7.5.20: σ_η的HalfNormal先验 (温度化放宽)
        sigma_eta_prior = historical['sigma_std'] * config.sigma_eta_multiplier

        return {
            # 协整参数
            'alpha_mu': historical['alpha_mean'],
            'alpha_sigma': historical['alpha_std'],
            'beta_mu': historical['beta_mean'],
            'beta_sigma': historical['beta_std'],
            'sigma_sigma': sigma_prior,
            # AR(1)参数 (v7.5.20新增)
            'rho_alpha': rho_alpha,
            'rho_beta': rho_beta,
            'sigma_eta_prior': sigma_eta_prior,
            # MCMC配置
            'tune': self.joint_config.mcmc_warmup,
            'draws': self.joint_config.mcmc_draws,
        }


    def _create_uninformed_prior(self) -> Dict:
        """创建完全无信息先验 (v7.5.20: 添加ρ/σ_η无信息先验)"""
        config = self.uninformed_prior

        return {
            # 协整参数
            'alpha_mu': 0,
            'alpha_sigma': config.alpha_sigma,
            'beta_mu': 1,
            'beta_sigma': config.beta_sigma,
            'sigma_sigma': config.sigma_sigma,
            # AR(1)参数 (v7.5.20新增)
            'rho_alpha': config.rho_alpha,              # Beta(2,2)
            'rho_beta': config.rho_beta,
            'sigma_eta_prior': self.joint_config.sigma_eta_prior,  # HalfNormal(0.1)
            # MCMC配置
            'tune': self.joint_config.mcmc_warmup,
            'draws': self.joint_config.mcmc_draws,
        }


    # ===== 单一联合贝叶斯模型 =====

    def _fit_joint_model(self, pair_data: PairData, prior_params: Dict) -> Dict:
        """
        单一联合贝叶斯模型 - β, α, ρ同时估计 (v7.31.3: 拆分为3个子方法)

        核心设计:
        - 直接建模y_t，绕过PyMC observed约束
        - 数学变换: y_t = α(1-ρ) + β(x_t - ρx_{t-1}) + ρy_{t-1} + η_t
        - β, α使用先验（历史后验或无信息）
        - ρ使用Beta先验确保平稳性

        Args:
            pair_data: PairData对象
            prior_params: 先验参数字典

        Returns:
            Dict: 后验统计量（包含rho_mean, rho_std等）
        """
        try:
            # 步骤1: 准备AR(1)数据
            y_data, x_data, y_curr, y_lag, x_curr, x_lag = self._prepare_ar1_data(pair_data)

            # 步骤2: 构建PyMC模型并采样
            trace = self._build_pymc_model(prior_params, y_curr, y_lag, x_curr, x_lag)

            # 步骤3: 提取后验统计量
            stats = self._extract_posterior_stats(trace, pair_data, y_data, x_data)

            return stats

        except Exception as e:
            if self.algorithm.debug_mode:
                self.algorithm.Debug(f"[BayesianModeler] 联合建模失败: {str(e)}")
            return self._get_default_stats()


    def _prepare_ar1_data(self, pair_data: PairData):
        """
        准备AR(1)模型数据 (v7.31.3: 从_fit_joint_model拆分)

        Args:
            pair_data: PairData对象

        Returns:
            (y_data, x_data, y_curr, y_lag, x_curr, x_lag): 原始数据和AR(1)数据
        """
        y_data = pair_data.log_prices1
        x_data = pair_data.log_prices2

        # 构建AR(1)数据（时间维度-1）
        y_curr = y_data[1:]
        y_lag = y_data[:-1]
        x_curr = x_data[1:]
        x_lag = x_data[:-1]

        return y_data, x_data, y_curr, y_lag, x_curr, x_lag


    def _build_pymc_model(self, prior_params: Dict, y_curr, y_lag, x_curr, x_lag):
        """
        构建PyMC模型并执行MCMC采样 (v7.31.3: 从_fit_joint_model拆分)

        Args:
            prior_params: 先验参数字典
            y_curr, y_lag: 当前和滞后的y值
            x_curr, x_lag: 当前和滞后的x值

        Returns:
            trace: MCMC采样后的trace对象
        """
        with pm.Model():
            # 协整参数（使用先验,放宽倍数由PRIOR_RELAXATION_FACTOR定义）
            beta = pm.Normal('beta', mu=prior_params['beta_mu'],
                           sigma=prior_params['beta_sigma'] * self.PRIOR_RELAXATION_FACTOR)
            alpha = pm.Normal('alpha', mu=prior_params['alpha_mu'],
                            sigma=prior_params['alpha_sigma'] * self.PRIOR_RELAXATION_FACTOR)

            # AR(1)参数 - ρ ∈ (0,1) 通过Beta分布天然保证平稳性
            rho = pm.Beta('rho', alpha=prior_params['rho_alpha'], beta=prior_params['rho_beta'])
            sigma_eta = pm.HalfNormal('sigma_eta', sigma=prior_params['sigma_eta_prior'])

            # 派生量(半衰期后验分布)
            half_life = pm.Deterministic('half_life', -pm.math.log(2) / pm.math.log(rho))

            # 构建mu（数学变换后的期望）
            # 推导: y_t = α(1-ρ) + β(x_t - ρx_{t-1}) + ρy_{t-1} + η_t
            mu = alpha * (1 - rho) + beta * (x_curr - rho * x_lag) + rho * y_lag

            # Likelihood
            likelihood = pm.Normal('y_obs', mu=mu, sigma=sigma_eta, observed=y_curr)

            # MCMC采样
            trace = pm.sample(
                draws=self.joint_config.mcmc_draws,
                tune=self.joint_config.mcmc_warmup,
                chains=self.mcmc_chains,
                return_inferencedata=False,
                progressbar=False
            )

        return trace


    def _extract_posterior_stats(self, trace, pair_data: PairData, y_data, x_data) -> Dict:
        """
        从MCMC trace提取后验统计量 (v7.31.3: 从_fit_joint_model拆分)

        Args:
            trace: MCMC采样后的trace对象
            pair_data: PairData对象
            y_data, x_data: 原始对数价格数据

        Returns:
            Dict: 后验统计量字典
        """
        # 提取后验样本
        rho_samples = trace['rho'].flatten()

        # 参数后验统计
        beta_mean = float(np.mean(trace['beta']))
        beta_std = float(np.std(trace['beta']))
        alpha_mean = float(np.mean(trace['alpha']))
        alpha_std = float(np.std(trace['alpha']))
        sigma_mean = float(np.mean(trace['sigma_eta']))
        sigma_std = float(np.std(trace['sigma_eta']))
        rho_mean = float(np.mean(rho_samples))
        rho_std = float(np.std(rho_samples))

        # 计算对数空间spread（与Pairs.get_zscore()一致）
        log_spread = y_data - (alpha_mean + beta_mean * x_data)
        residual_mean = float(np.mean(log_spread))
        residual_std_calc = float(np.std(log_spread))

        # 构建后验统计字典
        stats = {
            # 协整参数
            'alpha_mean': alpha_mean,
            'alpha_std': alpha_std,
            'beta_mean': beta_mean,
            'beta_std': beta_std,
            'sigma_mean': sigma_mean,
            'sigma_std': sigma_std,
            # AR(1)参数
            'rho_samples': rho_samples,
            'rho_mean': rho_mean,
            'rho_std': rho_std,
            # 对数空间spread统计量
            'spread': log_spread,
            'residual_mean': residual_mean,
            'residual_std': residual_std_calc,
            # 元信息
            'method': 'joint_bayesian',
            'update_time': self.algorithm.UtcTime
        }

        # 保存到历史后验
        self.historical_posteriors[pair_data.pair_key] = stats.copy()

        return stats


    def _get_default_stats(self) -> Dict:
        """
        获取默认后验统计量（建模失败时使用） (v7.31.3: 从_fit_joint_model拆分)

        Returns:
            Dict: 默认后验统计量字典
        """
        return {
            'alpha_mean': 0.0,
            'alpha_std': 0.0,
            'beta_mean': 1.0,
            'beta_std': 0.0,
            'sigma_mean': 0.01,
            'sigma_std': 0.0,
            'rho_samples': np.array([0.5]),
            'rho_mean': 0.5,
            'rho_std': 0.2,
            'spread': np.array([0.0]),
            'residual_mean': 0.0,
            'residual_std': 0.05,
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

        return result


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

