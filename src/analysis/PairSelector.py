# region imports
from AlgorithmImports import *
from collections import defaultdict
import numpy as np
import pandas as pd
from scipy import stats  # 用于OLS回归估计AR(1)系数
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
        self.liquidity_benchmark = module_config['liquidity_benchmark']
        self.quality_weights = module_config['quality_weights']
        self.scoring_thresholds = module_config['scoring_thresholds']


    def selection_procedure(self, raw_pairs, clean_data):
        """
        执行配对筛选流程 - 评估质量并筛选最佳配对
        """
        # 步骤1：评估配对质量
        scored_pairs = self.evaluate_quality(raw_pairs, clean_data)

        # 步骤2：筛选最佳配对
        selected_pairs = self.select_best(scored_pairs)

        return selected_pairs


    def evaluate_quality(self, raw_pairs, clean_data):
        """
        评估配对质量

        设计说明: 使用252天长期窗口与BayesianModeler保持一致
        - 协整是长期概念,评分应基于稳定的长期关系
        - 避免短期窗口捕捉到"伪协整"或运气好的配对
        - 确保评分高的配对在实际交易中也表现良好
        """
        scored_pairs = []

        for pair_info in raw_pairs:
            symbol1 = pair_info['symbol1']
            symbol2 = pair_info['symbol2']

            # 统计质量分数（基于p值的对数转换）
            # 使用Log10转换反映p值的统计学对数特性
            # p=0.001→1.0, p=0.01→0.667, p=0.05→0.434
            pvalue_score = min(1.0, -np.log10(pair_info['pvalue']) / 3.0)

            # 获取数据(DataProcessor保证DataFrame输出)
            data1 = clean_data[symbol1]
            data2 = clean_data[symbol2]
            prices1 = data1['close']
            prices2 = data2['close']

            # ⭐ 预先计算252天窗口数据和beta（只计算一次，用于Half-Life评分）
            if len(prices1) >= self.lookback_days and len(prices2) >= self.lookback_days:
                prices1_recent = prices1[-self.lookback_days:]
                prices2_recent = prices2[-self.lookback_days:]

                # 估计beta（OLS回归，每配对只计算一次）
                linreg_result = stats.linregress(np.log(prices2_recent), np.log(prices1_recent))
                slope = linreg_result.slope
                intercept = linreg_result.intercept
                beta = slope if slope > 0 else 1.0  # 安全检查: beta应为正

                # 计算半衰期分数（传入预计算的数据）
                half_life_score = self._calculate_half_life_score(prices1_recent, prices2_recent, beta)
            else:
                # 数据不足，分数为0
                slope = None
                intercept = None
                half_life_score = 0

            # 流动性分数（基于成交量，独立计算）
            liquidity_score = self._calculate_liquidity_score(data1, data2)

            # 综合质量分数（三指标体系）
            quality_score = (
                self.quality_weights['statistical'] * pvalue_score +
                self.quality_weights['half_life'] * half_life_score +
                self.quality_weights['liquidity'] * liquidity_score
            )

            # 添加评分结果和OLS结果（供BayesianModeler复用）
            pair_info.update({
                'quality_score': quality_score,
                'half_life_score': half_life_score,
                'liquidity_score': liquidity_score,
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


    def _calculate_half_life_score(self, prices1_recent, prices2_recent, beta):
        """
        计算半衰期分数（基于价格序列的均值回归速度）

        使用AR(1)模型估计半衰期: half_life = -log(2) / log(ρ)

        Args:
            prices1_recent: 最近120天的价格序列（symbol1）
            prices2_recent: 最近120天的价格序列（symbol2）
            beta: 预先计算的协整系数
        """
        try:
            # Beta调整价差（直接使用传入的beta，无需重复计算）
            spread = np.log(prices1_recent) - beta * np.log(prices2_recent)

            if len(spread) < 2:
                return 0  # 无数据，返回0分

            # AR(1)自相关
            spread_lag = spread[:-1]
            spread_curr = spread[1:]

            # 使用OLS回归估计AR(1)系数(标准方法,处理非零均值更准确)
            slope, _, _, _, _ = stats.linregress(spread_lag, spread_curr)
            rho = slope

            # 计算半衰期
            if 0 < rho < 1:
                half_life = -np.log(2) / np.log(rho)
                # 使用配置的阈值进行插值
                optimal_days = self.scoring_thresholds['half_life']['optimal_days']
                max_days = self.scoring_thresholds['half_life']['max_acceptable_days']
                return self._linear_interpolate(half_life, optimal_days, max_days, 0.0, 1.0)
            else:
                # rho <= 0 或 rho >= 1: 无均值回归
                return 0

        except Exception as e:
            self.algorithm.Debug(f"[PairSelector] 半衰期计算失败: {e}")
            return 0  # 异常返回0分


    def _calculate_liquidity_score(self, data1, data2):
        """
        基于成交量的流动性评分（使用配置的基准值归一化）

        使用最近60天的滚动窗口,更反映当前流动性状况
        """
        try:
            # 检查volume字段
            if 'volume' not in data1.columns or 'volume' not in data2.columns:
                return 0  # 无成交量数据

            if 'close' not in data1.columns or 'close' not in data2.columns:
                return 0  # 无价格数据

            # 使用最近60天的平均日成交额(更反映当前流动性)
            recent_window = 60
            dollar_volume1 = (data1['volume'][-recent_window:] * data1['close'][-recent_window:]).mean()
            dollar_volume2 = (data2['volume'][-recent_window:] * data2['close'][-recent_window:]).mean()

            # 使用较小的成交额作为配对的流动性（短板效应）
            min_dollar_volume = min(dollar_volume1, dollar_volume2)

            # 使用配置的基准值归一化
            liquidity_score = min(1.0, min_dollar_volume / self.liquidity_benchmark)

            return liquidity_score

        except Exception as e:
            self.algorithm.Debug(f"[PairSelector] 流动性计算失败: {e}")
            return 0  # 异常返回0分