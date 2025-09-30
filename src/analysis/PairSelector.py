# region imports
from AlgorithmImports import *
from collections import defaultdict
import numpy as np
import pandas as pd
# endregion


class PairSelector:
    """配对评估和筛选器 - 负责评估配对质量并筛选最佳配对"""

    def __init__(self, algorithm, config: dict):
        """
        初始化配对选择器
        """
        self.algorithm = algorithm

        # 质量评分权重
        self.quality_weights = config['quality_weights']

        # 筛选参数
        self.max_symbol_repeats = config['max_symbol_repeats']
        self.max_pairs = config['max_pairs']
        self.data_completeness_ratio = config['data_completeness_ratio']
        self.liquidity_benchmark = config['liquidity_benchmark']

        # 评分阈值
        self.scoring_thresholds = config['scoring_thresholds']


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
        """
        scored_pairs = []

        for pair_info in raw_pairs:
            symbol1 = pair_info['symbol1']
            symbol2 = pair_info['symbol2']

            # 统计质量分数（基于p值）
            pvalue_score = 1 - pair_info['pvalue']

            # 统一获取数据
            data1 = clean_data[symbol1]
            data2 = clean_data[symbol2]

            # 统一处理价格数据
            prices1 = data1['close'] if isinstance(data1, pd.DataFrame) else data1
            prices2 = data2['close'] if isinstance(data2, pd.DataFrame) else data2

            # 计算各项分数
            half_life_score = self._calculate_half_life_score(prices1, prices2)
            volatility_ratio_score = self._calculate_volatility_ratio_score(prices1, prices2)

            # 流动性分数（基于成交量）
            liquidity_score = self._calculate_liquidity_score(data1, data2)

            # 综合质量分数
            quality_score = (
                self.quality_weights['statistical'] * pvalue_score +
                self.quality_weights['half_life'] * half_life_score +
                self.quality_weights['volatility_ratio'] * volatility_ratio_score +
                self.quality_weights['liquidity'] * liquidity_score
            )

            # 添加评分结果
            pair_info.update({
                'quality_score': quality_score,
                'half_life_score': half_life_score,
                'volatility_ratio_score': volatility_ratio_score,
                'liquidity_score': liquidity_score
            })
            scored_pairs.append(pair_info)

        return scored_pairs


    def select_best(self, scored_pairs):
        """
        筛选最佳配对
        根据质量分数排序，并确保单个股票不会出现在过多配对中
        """
        # 按质量分数排序（从高到低）
        sorted_pairs = sorted(scored_pairs, key=lambda x: x['quality_score'], reverse=True)

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


    def _calculate_half_life_score(self, prices1, prices2):
        """
        计算半衰期分数（基于价格序列的均值回归速度）
        """
        try:
            # 计算价差（简单方法，不依赖贝叶斯）
            spread = np.log(prices2) - np.log(prices1)

            if len(spread) < 2:
                return 0  # 无数据，返回0分

            # AR(1)自相关
            spread_lag = spread[:-1]
            spread_curr = spread[1:]

            # 计算自相关系数
            rho = np.corrcoef(spread_lag, spread_curr)[0, 1]

            # 计算半衰期
            if 0 < rho < 1:
                half_life = -np.log(2) / np.log(rho)
                # 使用配置的阈值进行插值：optimal_days=1.0, max_acceptable_days=0.0
                optimal_days = self.scoring_thresholds['half_life']['optimal_days']
                max_days = self.scoring_thresholds['half_life']['max_acceptable_days']
                return self._linear_interpolate(half_life, optimal_days, max_days, 0.0, 1.0)
            else:
                # rho <= 0 或 rho >= 1：无均值回归
                return 0  # 明确的失败信号

        except Exception as e:
            self.algorithm.Debug(f"[PairSelector] 半衰期计算失败: {e}", 2)
            return 0  # 异常返回0分


    def _calculate_volatility_ratio_score(self, prices1, prices2):
        """
        计算波动率比率分数（稳定性）
        """
        try:
            # 计算简单价差
            spread = np.log(prices2) - np.log(prices1)
            spread_vol = np.std(spread)

            # 计算对数收益率波动率
            log_returns1 = np.diff(np.log(prices1))
            log_returns2 = np.diff(np.log(prices2))
            vol1 = np.std(log_returns1)
            vol2 = np.std(log_returns2)

            # 安全检查
            if vol1 <= 0 or vol2 <= 0:
                return 0  # 异常数据，返回0分

            # 计算比率
            combined_vol = np.sqrt(vol1**2 + vol2**2)
            volatility_ratio = spread_vol / combined_vol

            # 使用配置的阈值进行插值：optimal_ratio=1.0分，max_acceptable_ratio=0分
            optimal_ratio = self.scoring_thresholds['volatility_ratio']['optimal_ratio']
            max_ratio = self.scoring_thresholds['volatility_ratio']['max_acceptable_ratio']
            return self._linear_interpolate(volatility_ratio, optimal_ratio, max_ratio, 0.0, 1.0)

        except Exception as e:
            self.algorithm.Debug(f"[PairSelector] 波动率比率计算失败: {e}", 2)
            return 0  # 异常返回0分


    def _calculate_liquidity_score(self, data1, data2):
        """
        基于成交量的流动性评分（使用配置的基准值归一化）
        """
        try:
            # 检查volume字段
            if 'volume' not in data1.columns or 'volume' not in data2.columns:
                return 0  # 无成交量数据

            if 'close' not in data1.columns or 'close' not in data2.columns:
                return 0  # 无价格数据

            # 计算平均日成交额（美元）
            dollar_volume1 = (data1['volume'] * data1['close']).mean()
            dollar_volume2 = (data2['volume'] * data2['close']).mean()

            # 使用较小的成交额作为配对的流动性（短板效应）
            min_dollar_volume = min(dollar_volume1, dollar_volume2)

            # 使用配置的基准值归一化
            liquidity_score = min(1.0, min_dollar_volume / self.liquidity_benchmark)

            return liquidity_score

        except Exception as e:
            self.algorithm.Debug(f"[PairSelector] 流动性计算失败: {e}", 2)
            return 0  # 异常返回0分