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


    def selection_procedure(self, raw_pairs, pair_data_dict, clean_data=None):
        """
        执行配对筛选流程 - 评估质量并筛选最佳配对（重构版）

        Args:
            raw_pairs: CointegrationAnalyzer输出的配对列表
            pair_data_dict: 预构建的PairData对象字典（从main.py传入）
            clean_data: 清洗后的OHLCV数据（v7.2.18: 保留兼容性，实际未使用）

        Returns:
            List[Dict]: 筛选后的配对列表（包含质量分数和OLS参数）

        设计理念:
        - 专注于质量评估和筛选逻辑（单一职责）
        - 数据准备由调用方负责（依赖倒置）
        - 复用预计算的对数价格（性能优化）
        """
        # 步骤1: 评估配对质量（使用预计算的对数价格）
        scored_pairs = self.evaluate_quality(raw_pairs, pair_data_dict)

        # 步骤2: 筛选最佳配对
        selected_pairs = self.select_best(scored_pairs)

        return selected_pairs


    def evaluate_quality(self, raw_pairs, pair_data_dict):
        """
        评估配对质量（v7.2.18: volatility_ratio替代liquidity）

        Args:
            raw_pairs: CointegrationAnalyzer输出的配对列表
            pair_data_dict: {pair_key: PairData} 预构建的PairData对象字典

        设计说明: 使用252天长期窗口与BayesianModeler保持一致
        - 协整是长期概念,评分应基于稳定的长期关系
        - 避免短期窗口捕捉到"伪协整"或运气好的配对
        - 确保评分高的配对在实际交易中也表现良好

        性能优化:
        - 对数价格从PairData获取（预计算，消除重复np.log()）
        - volatility_ratio复用OLS beta（一次计算，多处使用）
        """
        scored_pairs = []

        for pair_info in raw_pairs:
            symbol1 = pair_info['symbol1']
            symbol2 = pair_info['symbol2']
            pair_key = (symbol1, symbol2)

            # 获取PairData对象（包含预计算的对数价格）
            pair_data = pair_data_dict[pair_key]

            # ⭐ 使用PairData的预计算对数价格（消除重复np.log()调用）
            if len(pair_data.log_prices1) >= self.lookback_days:
                log_prices1_recent = pair_data.log_prices1[-self.lookback_days:]
                log_prices2_recent = pair_data.log_prices2[-self.lookback_days:]

                # 估计beta（OLS回归，使用预计算的对数价格）
                linreg_result = stats.linregress(log_prices2_recent, log_prices1_recent)
                slope = linreg_result.slope
                intercept = linreg_result.intercept
                beta = slope if slope > 0 else 1.0  # 安全检查: beta应为正

                # 计算半衰期分数（传入预计算的对数价格）
                half_life_score, half_life_days = self._calculate_half_life_score(log_prices1_recent, log_prices2_recent, beta)

                # 计算价差波动率比值分数（v7.2.18: 替代liquidity，复用OLS beta）
                volatility_ratio_score, raw_vol_ratio = self._calculate_volatility_ratio_score(
                    log_prices1_recent, log_prices2_recent, beta
                )
            else:
                # 数据不足，分数为0
                slope = None
                intercept = None
                half_life_score = 0
                half_life_days = None
                volatility_ratio_score = 0
                raw_vol_ratio = None

            # 综合质量分数（双指标体系 v7.3.1: 移除statistical，专注交易特性）
            quality_score = (
                self.quality_weights['half_life'] * half_life_score +
                self.quality_weights['volatility_ratio'] * volatility_ratio_score
            )

            # 详细日志：每个配对的评分组成（v7.2.18.1: 诊断95.9%过滤率）
            status = "PASS" if quality_score > self.min_quality_threshold else "FAIL"
            half_life_str = f"{half_life_days:.1f}" if half_life_days is not None else "N/A"
            vol_ratio_str = f"{raw_vol_ratio:.3f}" if raw_vol_ratio is not None else "N/A"
            # v7.3.1: 移除Stat字段，简化为双指标输出
            self.algorithm.Debug(
                f"[PairScore] ({symbol1.Value:4s}, {symbol2.Value:4s}): "  # 使用.Value提取ticker字符串
                f"Q={quality_score:.3f} [{status}] | "
                f"Half={half_life_score:.3f}(days={half_life_str}) | "
                f"Vol={volatility_ratio_score:.3f}(ratio={vol_ratio_str})"
            )

            # 添加评分结果和OLS结果（供BayesianModeler复用）
            pair_info.update({
                'quality_score': quality_score,
                'half_life_score': half_life_score,
                'volatility_ratio_score': volatility_ratio_score,
                'ols_beta': slope,      # OLS原始斜率（可能为负）
                'ols_alpha': intercept  # OLS截距
            })
            scored_pairs.append(pair_info)

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


    def _calculate_volatility_ratio_score(self, log_prices1_recent, log_prices2_recent, beta):
        """
        计算方差比值分数（v7.2.20: 对数变换改进区分度）

        核心思想:
        - variance_ratio = Var(spread) / [Var(x) + Var(y)]
        - 比值越小 → 协整越强 → 分数越高
        - ratio=0: 完美协整 → score=1.0
        - ratio=1.0: 无协整临界点 → score=0.0
        - ratio>1.0: 负协整 → score=0.0

        评分公式（对数变换 v7.2.20）:
        - volatility_score = max(0, 1 - log(1 + ratio) / log(2))
        - 在 [0.0, 0.2] 区间拉开到 [0.737, 1.0]，改善拥挤度 31.5%
        - 拉开程度适中，保持连续可导

        物理意义:
        - 方差比值衡量价差方差相对于整体系统方差的占比
        - 1.0是天然临界点（无协整的物理定义）
        - 单位一致性：分子分母都是方差（对数价格的二阶矩）

        Args:
            log_prices1_recent: 最近252天对数价格（symbol1）- 来自PairData预计算
            log_prices2_recent: 最近252天对数价格（symbol2）- 来自PairData预计算
            beta: 协整系数（OLS回归斜率）

        Returns:
            (score, variance_ratio): 评分和原始方差比值（用于诊断日志）
        """
        try:
            # 1. 计算协整价差方差
            spread = log_prices1_recent - beta * log_prices2_recent
            spread_var = np.var(spread, ddof=1)

            # 2. 计算个股方差
            var1 = np.var(log_prices1_recent, ddof=1)
            var2 = np.var(log_prices2_recent, ddof=1)
            stock_var_sum = var1 + var2

            # 3. 计算方差比值
            if stock_var_sum <= 0:
                return (0, None)
            variance_ratio = spread_var / stock_var_sum

            # 4. 对数变换为评分（v7.2.20: 改进区分度）
            # 公式: score = 1 - log(1 + ratio) / log(2)
            # ratio=0 → score=1.0, ratio=1.0 → score=0.0, ratio>1.0 → score=0.0
            # 在 [0.0, 0.2] 区间拉开到 [0.737, 1.0]，改善拥挤度 31.5%
            volatility_score = max(0.0, 1.0 - np.log(1 + variance_ratio) / np.log(2))

            return (volatility_score, variance_ratio)

        except Exception as e:
            self.algorithm.Debug(f"[PairSelector] Variance ratio计算失败: {e}")
            return (0, None)