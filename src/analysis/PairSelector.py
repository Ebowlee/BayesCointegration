# region imports
from AlgorithmImports import *
from collections import defaultdict
import numpy as np
from src.analysis.PairData import PairData
# endregion


class PairSelector:
    """配对评估和筛选器 - 负责评估配对质量并筛选最佳配对"""

    def __init__(self, algorithm, analysis_config, module_config):
        """
        初始化配对选择器 (v7.12.0: 移除blacklist_manager依赖)

        Args:
            algorithm: QCAlgorithm实例
            analysis_config: AnalysisConfig dataclass实例
            module_config: PairSelectorConfig dataclass实例
        """
        self.algorithm = algorithm

        # 从analysis_config读取
        self.lookback_days = analysis_config.lookback_days  # 252天,与BayesianModeler统一

        # 从module_config读取
        # v7.31.0: max_symbol_repeats已迁移到CointegrationAnalyzer
        self.min_quality_threshold = module_config.min_quality_threshold
        self.quality_weights = module_config.quality_weights
        self.scoring_thresholds = module_config.scoring_thresholds


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
            # v7.13.0: _calculate_half_life_score返回三元组 (score, mean, std)
            half_life_score, half_life_days, half_life_std = self._calculate_half_life_score(model_result)
            mean_reversion_score, snr_kappa = self._calculate_mean_reversion_certainty_score(model_result)

            # 综合质量分数（二维加权平均, v7.5.23: 移除BetaStab维度）
            quality_score = (
                self.quality_weights['half_life'] * half_life_score +
                self.quality_weights['mean_reversion_certainty'] * mean_reversion_score
            )


            # 更新质量分数到model_result(保留原有字段)
            model_result['quality_score'] = quality_score
            model_result['half_life_score'] = half_life_score
            model_result['half_life'] = half_life_days  # v7.11.0: 供PairHoldingTimeoutRule使用
            model_result['half_life_std'] = half_life_std  # v7.13.0: 半衰期不确定性
            model_result['mean_reversion_score'] = mean_reversion_score

            scored_pairs.append(model_result)

        return scored_pairs


    def select_best(self, scored_pairs):
        """
        筛选最佳配对 (v7.12.0: 简化逻辑)

        流程:
        1. 过滤低于最低分数阈值的配对（质量门槛）
        2. [v7.12.0] 风险配对过滤 (DRAWDOWN/ANOMALY冷却期检查)
        3. 按质量分数排序
        4. 确保单个股票不会出现在过多配对中

        改动 (v7.12.0):
        - 移除黑名单过滤 (BlacklistManager模块已删除)
        - 新增风险配对过滤 (检查DRAWDOWN/ANOMALY冷却期)
        - 移除max_pairs硬性限制 (改用资金约束自然限制)
        """
        # v7.31.6: 质量分布统计 (在质量筛选之前)
        if scored_pairs:
            # 统计各档位数量
            excellent = sum(1 for p in scored_pairs if p['quality_score'] >= 0.80)
            good = sum(1 for p in scored_pairs if 0.70 <= p['quality_score'] < 0.80)
            pass_grade = sum(1 for p in scored_pairs if 0.60 <= p['quality_score'] < 0.70)
            fail = sum(1 for p in scored_pairs if p['quality_score'] < 0.60)

            # 计算极值
            max_score = max(p['quality_score'] for p in scored_pairs)
            min_score = min(p['quality_score'] for p in scored_pairs)

            self.algorithm.Debug(
                f"[质量分布] 总计{len(scored_pairs)}对 → "
                f"优秀(≥0.80):{excellent}对 | 良好(0.70-0.80):{good}对 | "
                f"及格(0.60-0.70):{pass_grade}对 | 不及格(<0.60):{fail}对 | "
                f"最高:{max_score:.3f} | 最低:{min_score:.3f}",
                level=1
            )

        # Step 1: 最低质量门槛过滤（严格大于阈值）
        min_threshold = self.min_quality_threshold  # 从config读取
        qualified_pairs = [
            p for p in scored_pairs
            if p['quality_score'] > min_threshold  # 严格大于（不包含等于）
        ]

        # v7.30.0: 诊断日志 - 质量筛选
        self.algorithm.Debug(
            f"[质量筛选] 输入{len(scored_pairs)}对 → "
            f"质量阈值>{min_threshold:.2f} → "
            f"通过{len(qualified_pairs)}对 (损失{len(scored_pairs) - len(qualified_pairs)}对)",
            level=1
        )

        # v7.31.0: 删除Step 2风险配对过滤 (冷却期由ExecutionManager统一检查)
        # v7.31.0: 删除Step 4单股重复限制 (已在CointegrationAnalyzer阶段完成)

        # Step 2: 按质量分数排序（从高到低）
        sorted_pairs = sorted(qualified_pairs, key=lambda x: x['quality_score'], reverse=True)

        return sorted_pairs


    # ===== 私有评分方法 (Private Scoring Methods) =====

    def _calculate_half_life_score(self, model_result):
        """
        计算半衰期分数 (v7.31.3: 简化注释,详见CLAUDE.md)

        评分方法: 非对称高斯+软截断+指数衰减
        峰值: 8天 | 核心区间: 5-10天 | 可接受: 4-12天

        Args:
            model_result: BayesianModeler输出（包含rho_samples）

        Returns:
            (score, half_life_mean, half_life_std): 评分和半衰期统计量
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

            # v7.13.0: 计算半衰期分布 (修正Jensen不等式问题)
            # 正确方式: 对每个rho_sample计算half_life,然后求均值和标准差
            # 错误方式 (v7.12.0及之前): half_life = -ln(2) / ln(mean(rho_samples))
            half_life_samples = -np.log(2) / np.log(rho_samples)
            half_life = float(np.mean(half_life_samples))  # 均值
            half_life_std = float(np.std(half_life_samples))  # 标准差

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

            # v7.13.0: 返回三元组 (score, mean, std)
            return (float(score), half_life, half_life_std)

        except Exception as e:
            self.algorithm.Debug(f"[PairSelector] 半衰期计算失败: {e}")
            return (0, None, None)



    def _calculate_mean_reversion_certainty_score(self, model_result):
        """
        计算均值回归确定性分数 (v7.31.3: 简化注释,详见CLAUDE.md)

        核心思路: 通过κ-based SNR量化均值回归显著性
        - κ = 连续时间均值回归率 (从ρ转换)
        - SNR_κ = E[κ] / Std[κ] (估计精度)
        - 评分: 逻辑斯蒂归一化 (S曲线)

        Args:
            model_result: BayesianModeler输出（包含rho_samples）

        Returns:
            (score, snr_kappa): 评分和SNR值
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
