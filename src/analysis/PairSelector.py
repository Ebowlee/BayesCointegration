# region imports
from AlgorithmImports import *
from collections import defaultdict
import numpy as np
import pandas as pd
from scipy import stats  # 用于OLS回归估计AR(1)系数
from src.analysis.PairData import PairData
# endregion


class PairSelector:
    """配对评估和筛选器 - 负责评估配对质量并筛选最佳配对"""

    def __init__(self, algorithm, shared_config: dict, module_config: dict):
        """
        初始化配对选择器

        Args:
            algorithm: QCAlgorithm实例
            shared_config: 共享配置(analysis_shared)
            module_config: 模块配置(pair_selector)
        """
        self.algorithm = algorithm

        # 从shared_config读取
        self.lookback_days = shared_config['lookback_days']  # 252天,与BayesianModeler统一

        # 从module_config读取
        self.max_symbol_repeats = module_config['max_symbol_repeats']
        self.max_pairs = module_config['max_pairs']
        self.min_quality_threshold = module_config['min_quality_threshold']
        self.quality_weights = module_config['quality_weights']
        self.scoring_thresholds = module_config['scoring_thresholds']


    def selection_procedure(self, modeling_results):
        """
        执行配对筛选流程 - 基于贝叶斯后验参数评估质量并筛选（v7.4.0重构）

        Args:
            modeling_results: BayesianModeler输出的建模结果列表
                每个元素包含: symbol1, symbol2, quality_score, beta_mean, beta_std,
                             lambda_mean, lambda_std, lambda_t_stat, residual_std, etc.

        Returns:
            List[Dict]: 筛选后的配对列表（包含四维质量分数）

        设计变更 (v7.4.0):
        - 不再使用OLS近似，直接使用贝叶斯后验参数
        - 四维评分系统：half_life, beta_stability, mean_reversion_certainty, residual_quality
        - 移除volatility_ratio（理论缺陷）
        """
        # 步骤1: 评估配对质量（使用贝叶斯后验参数）
        scored_pairs = self.evaluate_quality(modeling_results)

        # 步骤2: 筛选最佳配对
        selected_pairs = self.select_best(scored_pairs)

        return selected_pairs


    def evaluate_quality(self, modeling_results):
        """
        评估配对质量（v7.4.0: 基于贝叶斯后验参数的四维评分系统）

        Args:
            modeling_results: BayesianModeler输出的建模结果列表

        四维评分系统:
        1. Half-life (30%): 使用贝叶斯beta_mean计算半衰期
        2. Beta stability (25%): 使用beta_std衡量对冲比率稳定性
        3. Mean-reversion certainty (30%): 使用lambda_t_stat衡量AR(1)显著性
        4. Residual quality (15%): 使用residual_std衡量模型拟合质量

        设计优势:
        - 使用贝叶斯后验参数（比OLS更准确）
        - 移除volatility_ratio（理论缺陷）
        - 所有指标都有明确的统计意义
        """
        scored_pairs = []

        for model_result in modeling_results:
            symbol1 = model_result['symbol1']
            symbol2 = model_result['symbol2']

            # 四维评分计算
            half_life_score, half_life_days = self._calculate_half_life_score_v2(model_result)
            beta_stability_score = self._calculate_beta_stability_score(model_result['beta_std'])
            mean_reversion_score, t_stat = self._calculate_mean_reversion_certainty_score(model_result['lambda_t_stat'])
            residual_quality_score = self._calculate_residual_quality_score(model_result['residual_std'])

            # 综合质量分数（四维加权平均）
            quality_score = (
                self.quality_weights['half_life'] * half_life_score +
                self.quality_weights['beta_stability'] * beta_stability_score +
                self.quality_weights['mean_reversion_certainty'] * mean_reversion_score +
                self.quality_weights['residual_quality'] * residual_quality_score
            )

            # 详细日志：每个配对的四维评分组成
            status = "PASS" if quality_score > self.min_quality_threshold else "FAIL"
            half_life_str = f"{half_life_days:.1f}" if half_life_days is not None else "N/A"
            self.algorithm.Debug(
                f"[PairScore] ({symbol1.Value:4s}, {symbol2.Value:4s}): "
                f"Q={quality_score:.3f} [{status}] | "
                f"Half={half_life_score:.3f}(days={half_life_str}) | "
                f"BetaStab={beta_stability_score:.3f}(std={model_result['beta_std']:.4f}) | "
                f"MeanRev={mean_reversion_score:.3f}(t={t_stat:.2f}) | "
                f"Resid={residual_quality_score:.3f}(std={model_result['residual_std']:.4f})"
            )

            # 更新质量分数到model_result（保留原有字段）
            model_result['quality_score'] = quality_score
            model_result['half_life_score'] = half_life_score
            model_result['beta_stability_score'] = beta_stability_score
            model_result['mean_reversion_score'] = mean_reversion_score
            model_result['residual_quality_score'] = residual_quality_score

            scored_pairs.append(model_result)

        return scored_pairs


    def select_best(self, scored_pairs):
        """
        筛选最佳配对

        流程:
        1. 过滤低于最低分数阈值的配对（质量门槛）
        2. 按质量分数排序
        3. 确保单个股票不会出现在过多配对中
        """
        # Step 1: 最低质量门槛过滤（严格大于阈值）
        min_threshold = self.min_quality_threshold  # 从config读取
        qualified_pairs = [
            p for p in scored_pairs
            if p['quality_score'] > min_threshold  # 严格大于（不包含等于）
        ]

        # 诊断日志：记录淘汰的配对数量
        rejected_count = len(scored_pairs) - len(qualified_pairs)
        if rejected_count > 0:
            self.algorithm.Debug(
                f"[PairSelector] 质量阈值过滤: {rejected_count}个配对 <= {min_threshold:.2f}分"
            )

        # Step 2: 按质量分数排序（从高到低）
        sorted_pairs = sorted(qualified_pairs, key=lambda x: x['quality_score'], reverse=True)

        # Step 3: 单股重复限制（确保单个股票不会出现在过多配对中）
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


    def _linear_interpolate(self, value, min_val, max_val, min_score=0.0, max_score=1.0):
        """
        线性插值计算分数
        """
        if value <= min_val:
            return max_score
        elif value >= max_val:
            return min_score
        else:
            # 线性插值
            return max_score - (value - min_val) * (max_score - min_score) / (max_val - min_val)


    def _calculate_half_life_score(self, log_prices1_recent, log_prices2_recent, beta):
        """
        计算半衰期分数（v7.2.20: 梯形平台设计，7-14天黄金持仓期）

        使用AR(1)模型估计半衰期: half_life = -log(2) / log(ρ)

        评分逻辑（v7.2.20梯形平台）:
        - [0, 3): 0分（过快，缺乏交易空间）
        - [3, 7]: 线性上升 0→1（左侧上升段）
        - [7, 14]: 1.0分（平台期 - 黄金持仓期）
        - [14, 30]: 线性下降 1→0（右侧下降段）
        - [30, ∞): 0分（过慢，持仓时间过长）

        Args:
            log_prices1_recent: 最近252天的对数价格序列（symbol1）- 来自PairData预计算
            log_prices2_recent: 最近252天的对数价格序列（symbol2）- 来自PairData预计算
            beta: 预先计算的协整系数

        Returns:
            (score, half_life_days): 评分和原始半衰期天数（用于诊断日志 v7.2.18.1）
        """
        try:
            # Beta调整价差（使用预计算的对数价格，消除np.log()重复调用）
            spread = log_prices1_recent - beta * log_prices2_recent

            if len(spread) < 2:
                return (0, None)  # 无数据，返回0分

            # AR(1)自相关
            spread_lag = spread[:-1]
            spread_curr = spread[1:]

            # 使用OLS回归估计AR(1)系数(标准方法,处理非零均值更准确)
            slope, _, _, _, _ = stats.linregress(spread_lag, spread_curr)
            rho = slope

            # 计算半衰期
            if 0 < rho < 1:
                half_life = -np.log(2) / np.log(rho)

                # 梯形平台评分（v7.2.20）
                optimal_min = self.scoring_thresholds['half_life']['optimal_min_days']
                optimal_max = self.scoring_thresholds['half_life']['optimal_max_days']
                min_acceptable = self.scoring_thresholds['half_life']['min_acceptable_days']
                max_acceptable = self.scoring_thresholds['half_life']['max_acceptable_days']

                if half_life < min_acceptable:
                    return (0, half_life)  # 过快
                elif half_life <= optimal_min:
                    # 左侧上升段: [3, 7] → [0, 1]
                    score = (half_life - min_acceptable) / (optimal_min - min_acceptable)
                    return (score, half_life)
                elif half_life <= optimal_max:
                    # 平台期: [7, 14] → 1.0（黄金持仓期）
                    return (1.0, half_life)
                elif half_life <= max_acceptable:
                    # 右侧下降段: [14, 30] → [1, 0]
                    score = 1 - (half_life - optimal_max) / (max_acceptable - optimal_max)
                    return (score, half_life)
                else:
                    return (0, half_life)  # 过慢
            else:
                # rho <= 0 或 rho >= 1: 无均值回归
                return (0, None)

        except Exception as e:
            self.algorithm.Debug(f"[PairSelector] 半衰期计算失败: {e}")
            return (0, None)  # 异常返回0分


    # ===== v7.4.0 新增: 四维评分方法 =====

    def _calculate_half_life_score_v2(self, model_result):
        """
        计算半衰期分数 v2（v7.4.0: 使用贝叶斯beta_mean，复用AR(1) lambda）

        使用贝叶斯beta和AR(1) lambda直接计算半衰期: half_life = -log(2) / lambda

        Args:
            model_result: BayesianModeler输出的模型结果（包含lambda_mean）

        Returns:
            (score, half_life_days): 评分和原始半衰期天数
        """
        try:
            lambda_mean = model_result['lambda_mean']

            # lambda应该是负数（均值回归特性）
            if lambda_mean >= 0 or lambda_mean <= -1:
                return (0, None)  # 无效的lambda

            # 计算半衰期
            half_life = -np.log(2) / np.log(1 + lambda_mean)

            # 梯形平台评分（与v7.3.1保持一致）
            optimal_min = self.scoring_thresholds['half_life']['optimal_min_days']
            optimal_max = self.scoring_thresholds['half_life']['optimal_max_days']
            min_acceptable = self.scoring_thresholds['half_life']['min_acceptable_days']
            max_acceptable = self.scoring_thresholds['half_life']['max_acceptable_days']

            if half_life < min_acceptable:
                return (0, half_life)
            elif half_life <= optimal_min:
                score = (half_life - min_acceptable) / (optimal_min - min_acceptable)
                return (score, half_life)
            elif half_life <= optimal_max:
                return (1.0, half_life)
            elif half_life <= max_acceptable:
                score = 1 - (half_life - optimal_max) / (max_acceptable - optimal_max)
                return (score, half_life)
            else:
                return (0, half_life)

        except Exception as e:
            self.algorithm.Debug(f"[PairSelector] 半衰期计算v2失败: {e}")
            return (0, None)


    def _calculate_beta_stability_score(self, beta_std):
        """
        计算Beta稳定性分数（v7.4.0新增）

        Beta稳定性衡量对冲比率的波动程度:
        - beta_std越小 → 对冲比率越稳定 → 分数越高
        - 使用指数衰减函数: score = exp(-decay_factor * beta_std)

        Args:
            beta_std: Beta的后验标准差（来自贝叶斯MCMC）

        Returns:
            float: 评分 [0, 1]
        """
        try:
            decay_factor = self.scoring_thresholds['beta_stability']['decay_factor']
            score = np.exp(-decay_factor * beta_std)
            return max(0.0, min(1.0, score))

        except Exception as e:
            self.algorithm.Debug(f"[PairSelector] Beta稳定性计算失败: {e}")
            return 0.0


    def _calculate_mean_reversion_certainty_score(self, lambda_t_stat):
        """
        计算均值回归确定性分数（v7.4.0新增）

        Lambda t统计量衡量AR(1)系数的显著性:
        - |t_stat|越大 → lambda估计越显著 → 均值回归特性越可靠
        - t_stat应为负数（lambda < 0）
        - 使用分段线性函数

        Args:
            lambda_t_stat: Lambda的t统计量（来自AR(1)模型）

        Returns:
            (score, raw_t_stat): 评分和原始t统计量（用于日志）
        """
        try:
            min_t_stat = self.scoring_thresholds['mean_reversion_certainty']['min_t_stat']
            max_t_stat = self.scoring_thresholds['mean_reversion_certainty']['max_t_stat']

            # 取绝对值（负t统计量更好，但评分基于绝对值）
            abs_t_stat = abs(lambda_t_stat)

            if abs_t_stat < min_t_stat:
                return (0.0, lambda_t_stat)
            elif abs_t_stat >= max_t_stat:
                return (1.0, lambda_t_stat)
            else:
                # 线性插值
                score = (abs_t_stat - min_t_stat) / (max_t_stat - min_t_stat)
                return (score, lambda_t_stat)

        except Exception as e:
            self.algorithm.Debug(f"[PairSelector] 均值回归确定性计算失败: {e}")
            return (0.0, 0.0)


    def _calculate_residual_quality_score(self, residual_std):
        """
        计算残差质量分数（v7.4.0新增，替代volatility_ratio）

        Residual std直接衡量模型拟合质量:
        - residual_std越小 → 模型拟合越好 → 分数越高
        - 使用对数变换改善区分度

        Args:
            residual_std: 残差的后验标准差（来自贝叶斯MCMC）

        Returns:
            float: 评分 [0, 1]
        """
        try:
            min_residual = self.scoring_thresholds['residual_quality']['min_residual_std']
            max_residual = self.scoring_thresholds['residual_quality']['max_residual_std']

            if residual_std <= min_residual:
                return 1.0
            elif residual_std >= max_residual:
                return 0.0
            else:
                # 对数变换线性插值
                log_min = np.log(min_residual)
                log_max = np.log(max_residual)
                log_residual = np.log(residual_std)
                score = 1.0 - (log_residual - log_min) / (log_max - log_min)
                return max(0.0, min(1.0, score))

        except Exception as e:
            self.algorithm.Debug(f"[PairSelector] 残差质量计算失败: {e}")
            return 0.0