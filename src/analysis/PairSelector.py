# region imports
from AlgorithmImports import *
from collections import defaultdict
import numpy as np
from src.analysis.PairData import PairData
# endregion


class PairSelector:
    """配对评估和筛选器 - 负责评估配对质量并筛选最佳配对"""

    def __init__(self, algorithm, shared_config: dict, module_config: dict, blacklist_manager):
        """
        初始化配对选择器

        Args:
            algorithm: QCAlgorithm实例
            shared_config: 共享配置(analysis_shared)
            module_config: 模块配置(pair_selector)
            blacklist_manager: 黑名单管理器实例(v7.7.0重命名)
        """
        self.algorithm = algorithm
        self.blacklist_manager = blacklist_manager

        # 从shared_config读取
        self.lookback_days = shared_config['lookback_days']  # 252天,与BayesianModeler统一

        # 从module_config读取
        self.max_symbol_repeats = module_config['max_symbol_repeats']
        self.max_pairs = module_config['max_pairs']
        self.min_quality_threshold = module_config['min_quality_threshold']
        self.quality_weights = module_config['quality_weights']
        self.scoring_thresholds = module_config['scoring_thresholds']


    # ===== 公共方法 (Public Methods) =====

    def selection_procedure(self, modeling_results):
        """
        执行配对筛选流程 - 基于贝叶斯后验参数评估质量并筛选（v7.5.3统一rho）

        Args:
            modeling_results: BayesianModeler输出的建模结果列表
                每个元素包含: symbol1, symbol2, quality_score, beta_mean, beta_std,
                             rho_mean, rho_std, residual_std, half_life_mean, etc.

        Returns:
            List[Dict]: 筛选后的配对列表（包含二维质量分数: half_life, mean_reversion_certainty）

        设计变更 (v7.5.23):
        - 简化为二维评分系统: half_life (60%), mean_reversion_certainty (40%)
        - 移除维度: beta_stability (与MR重叠50%), residual_quality (预测失败57%)
        - 统一使用rho表示AR(1)系数,半衰期直接从rho计算: -ln(2) / ln(ρ)
        """
        # 步骤1: 评估配对质量（使用贝叶斯后验参数）
        scored_pairs = self.evaluate_quality(modeling_results)

        # 步骤2: 筛选最佳配对
        selected_pairs = self.select_best(scored_pairs)

        return selected_pairs


    def evaluate_quality(self, modeling_results):
        """
        评估配对质量（v7.5.23: 二维评分系统,移除BetaStab维度）

        Args:
            modeling_results: BayesianModeler输出的建模结果列表

        二维评分系统:
        1. Half-life (60%): 均值回归速度 (最独立+预测力最强,准确率57%)
        2. Mean-reversion certainty (40%): AR(1)显著性 (理论核心,预测力中等50%)

        移除维度:
        - Beta Stability: 与MR重叠50%(r=0.71), 所有配对评分0.97-0.99无区分度
        - Residual Quality: 预测失败率57%, 历史拟合≠未来预测

        设计优势:
        - 使用贝叶斯后验参数（比OLS更准确）
        - 统一使用rho表示AR(1)系数
        - 所有指标都有明确的统计意义
        """
        scored_pairs = []

        for model_result in modeling_results:
            symbol1 = model_result['symbol1']
            symbol2 = model_result['symbol2']

            # 二维评分计算 (调用私有方法)
            half_life_score, half_life_days = self._calculate_half_life_score(model_result)
            mean_reversion_score, snr_kappa = self._calculate_mean_reversion_certainty_score(model_result)

            # 综合质量分数（二维加权平均, v7.5.23: 移除BetaStab维度）
            quality_score = (
                self.quality_weights['half_life'] * half_life_score +
                self.quality_weights['mean_reversion_certainty'] * mean_reversion_score
            )

            # v7.8.4: 删除配对评分详情(月度过程日志,入场tag已包含质量分数)

            # 更新质量分数到model_result(保留原有字段)
            model_result['quality_score'] = quality_score
            model_result['half_life_score'] = half_life_score
            model_result['mean_reversion_score'] = mean_reversion_score

            scored_pairs.append(model_result)

        return scored_pairs


    def select_best(self, scored_pairs):
        """
        筛选最佳配对

        流程:
        1. 过滤低于最低分数阈值的配对（质量门槛）
        2. [NEW v7.6.0] 黑名单过滤
        3. 按质量分数排序
        4. 确保单个股票不会出现在过多配对中

        改动 (v7.6.0):
        - 新增第2步：黑名单过滤
        - 其他逻辑保持不变
        """
        # Step 1: 最低质量门槛过滤（严格大于阈值）
        min_threshold = self.min_quality_threshold  # 从config读取
        qualified_pairs = [
            p for p in scored_pairs
            if p['quality_score'] > min_threshold  # 严格大于（不包含等于）
        ]

        # v7.8.4: 删除质量阈值过滤统计(月度过程日志,可从[协整分析]+[PairsManager]推断)

        # Step 2: [v7.6.0 → v7.6.1封装] 黑名单过滤
        qualified_pairs = self._filter_by_blacklist(qualified_pairs)

        # Step 3: 按质量分数排序（从高到低）
        sorted_pairs = sorted(qualified_pairs, key=lambda x: x['quality_score'], reverse=True)

        # Step 4: 单股重复限制（确保单个股票不会出现在过多配对中）
        selected = []
        symbol_counts = defaultdict(int)

        for pair in sorted_pairs:
            symbol1 = pair['symbol1']
            symbol2 = pair['symbol2']

            # 检查单股重复限制
            if (symbol_counts[symbol1] < self.max_symbol_repeats and
                symbol_counts[symbol2] < self.max_symbol_repeats):

                selected.append(pair)
                symbol_counts[symbol1] += 1
                symbol_counts[symbol2] += 1

                # 达到最大配对数
                if len(selected) >= self.max_pairs:
                    break

        return selected


    # ===== 私有评分方法 (Private Scoring Methods) =====

    def _filter_by_blacklist(self, qualified_pairs):
        """
        黑名单过滤 (v7.6.0 → v7.6.1封装)

        将历史表现差的配对过滤掉,避免重复亏损。
        黑名单标准: ≥3笔交易 AND 累计收益率<0%

        Args:
            qualified_pairs: 已通过质量门槛的配对列表

        Returns:
            list: 非黑名单配对列表
        """
        blacklist = self.blacklist_manager.get_blacklist()
        blacklist_rejected = []
        non_blacklist_pairs = []

        for pair in qualified_pairs:
            pair_id = (pair['symbol1'].Value, pair['symbol2'].Value)
            if pair_id in blacklist:
                blacklist_rejected.append(pair_id)
            else:
                non_blacklist_pairs.append(pair)

        # v7.8.4: 删除黑名单过滤详情(月度过程日志,trade_xxx.jsonl已包含历史记录)

        return non_blacklist_pairs


    def _calculate_half_life_score(self, model_result):
        """
        计算半衰期分数 (v7.5.21: 非对称高斯评分,阈值优先设计)

        设计理念:
        - 峰值: 8天 (统计质量+timeout安全性的最优平衡)
        - 核心区间: 5-10天 (评分≥0.75)
        - 可接受区间: 4-12天 (评分≥0.50)
        - 排除区间: <4天或>15天

        配合改良C方案:
        - 入场: [1.2σ, 1.8σ]
        - 出场: 0.3σ
        - Timeout: 30天

        评分标准(基于Timeout约束):
        - 4天: 0.50 (次优,噪音风险)
        - 5天: 0.75 (良好)
        - 6天: 0.90 (优秀)
        - 8天: 1.00 (峰值)
        - 10天: 0.85 (良好)
        - 12天: 0.65 (可接受)
        - 15天: 0.18 (排除)

        Args:
            model_result: BayesianModeler输出的模型结果(包含rho_samples)

        Returns:
            (score, half_life_days): 评分和原始半衰期天数
        """
        try:
            # 从rho_samples按需计算rho_mean
            rho_samples = model_result.get('rho_samples')
            if rho_samples is None or len(rho_samples) == 0:
                return (0, None)

            rho_mean = float(np.mean(rho_samples))

            # rho有效性检查 (均值回归要求: ρ ∈ (0, 1))
            if rho_mean <= 0 or rho_mean >= 1:
                return (0, None)

            # 计算半衰期: half_life = -ln(2) / ln(ρ)
            half_life = -np.log(2) / np.log(rho_mean)

            # 读取阈值
            peak_days = self.scoring_thresholds['half_life']['peak_days']       # 8天
            sigma_left = self.scoring_thresholds['half_life']['sigma_left']     # 3.5
            sigma_right = self.scoring_thresholds['half_life']['sigma_right']   # 4.5
            min_days = self.scoring_thresholds['half_life']['min_days']         # 4天
            decay_start = self.scoring_thresholds['half_life']['decay_start']   # 12天
            decay_rate = self.scoring_thresholds['half_life']['decay_rate']     # 0.6

            # 非对称高斯核心
            if half_life < peak_days:
                # 左侧: 4-8天区间 (σ=3.5保证6天≈0.90)
                score = np.exp(-((half_life - peak_days) ** 2) / (2 * sigma_left ** 2))
            else:
                # 右侧: 8-12天区间 (σ=4.5保证10天≈0.85, 12天≈0.65)
                score = np.exp(-((half_life - peak_days) ** 2) / (2 * sigma_right ** 2))

            # 软截断下界 (4天以下平滑惩罚)
            if half_life < min_days:
                lower_bound = 1 / (1 + np.exp(-3 * (half_life - min_days)))
                score *= lower_bound

            # 远端指数衰减 (12天后快速排除)
            if half_life > decay_start:
                decay = np.exp(-decay_rate * (half_life - decay_start))
                score *= decay

            return (float(score), half_life)

        except Exception as e:
            self.algorithm.Debug(f"[PairSelector] 半衰期计算失败: {e}")
            return (0, None)



    def _calculate_mean_reversion_certainty_score(self, model_result):
        """
        计算均值回归确定性分数（v7.5.5: κ-based SNR，逐样本精确计算）

        核心思路:
        1. 连续时间转换: κ = -ln|ρ|/Δt (频率不变性)
        2. 逐样本转换: κ^(s) = -ln|ρ^(s)|/Δt (贝叶斯一致性)
        3. 精确后验统计: E[κ] = mean(κ^(s)), Std[κ] = std(κ^(s))
        4. SNR计算: SNR_κ = E[κ] / Std[κ] (估计精度)
        5. 逻辑斯蒂归一化: score = 1/(1+exp(a·(b-SNR_κ))) (S曲线)

        数学原理:
        - κ: 连续时间均值回归率 (单位: 1/天)
        - κ越大 → 均值回归越快 → 半衰期越短
        - SNR_κ越高 → κ估计越可靠 → 交易策略越稳健

        Args:
            model_result: BayesianModeler输出（包含rho_samples数组）

        Returns:
            (score, snr_kappa): 评分和κ-based SNR（用于日志）
        """
        try:
            # 提取rho样本
            rho_samples = model_result.get('rho_samples')
            if rho_samples is None:
                # 降级：兼容旧版（无样本数据）
                self.algorithm.Debug("[PairSelector] rho_samples缺失,降级处理")
                return (0.0, 0.0)

            # κ转换（逐样本）
            delta_t = self.scoring_thresholds['mean_reversion_certainty']['time_delta_days']
            kappa_samples = -np.log(np.abs(rho_samples)) / delta_t

            # 精确后验统计
            kappa_mean = np.mean(kappa_samples)
            kappa_std = np.std(kappa_samples)

            # SNR计算
            if kappa_std <= 0:
                return (0.0, 0.0)
            snr_kappa = kappa_mean / kappa_std

            # 上界截断
            max_snr = self.scoring_thresholds['mean_reversion_certainty']['max_snr_kappa']
            snr_kappa = min(snr_kappa, max_snr)

            # 逻辑斯蒂评分
            a = self.scoring_thresholds['mean_reversion_certainty']['logistic_steepness']
            b = self.scoring_thresholds['mean_reversion_certainty']['logistic_midpoint']
            score = 1.0 / (1.0 + np.exp(a * (b - snr_kappa)))

            return (max(0.0, min(1.0, score)), snr_kappa)

        except Exception as e:
            self.algorithm.Debug(f"[PairSelector] κ-based均值回归确定性计算失败: {e}")
            return (0.0, 0.0)
