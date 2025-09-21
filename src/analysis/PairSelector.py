# region imports
from AlgorithmImports import *
from collections import defaultdict
# endregion


class PairSelector:
    """配对评估和筛选器 - 负责评估配对质量并筛选最佳配对"""

    def __init__(self, algorithm, config: dict):
        """初始化配对选择器

        Args:
            algorithm: QCAlgorithm实例
            config: 配置字典，包含quality_weights、max_pairs等参数
        """
        self.algorithm = algorithm

        # 质量评分权重
        self.quality_weights = config['quality_weights']

        # 筛选参数
        self.max_symbol_repeats = config['max_symbol_repeats']
        self.max_pairs = config['max_pairs']

    def evaluate_and_select(self, raw_pairs, clean_data):
        """评估并筛选配对（组合方法）

        Args:
            raw_pairs: 协整检验返回的原始配对列表
            clean_data: 清洗后的价格数据

        Returns:
            list: 筛选后的最佳配对列表
        """
        # 步骤1：评估配对质量
        scored_pairs = self.evaluate_quality(raw_pairs, clean_data)

        # 步骤2：筛选最佳配对
        selected_pairs = self.select_best(scored_pairs)

        return selected_pairs

    def evaluate_quality(self, raw_pairs, clean_data):
        """评估配对质量

        Args:
            raw_pairs: 协整检验返回的原始配对列表
            clean_data: 清洗后的价格数据

        Returns:
            list: 添加了quality_score的配对列表
        """
        scored_pairs = []

        for pair_info in raw_pairs:
            symbol1 = pair_info['symbol1']
            symbol2 = pair_info['symbol2']

            # 统计质量分数（基于p值）
            pvalue_score = 1 - pair_info['pvalue']

            # 相关性分数
            data1 = clean_data[symbol1]
            data2 = clean_data[symbol2]
            correlation = data1.corr(data2)
            corr_score = abs(correlation)

            # 流动性分数（基于数据完整性）
            liquidity_score = (pair_info.get('data1_valid_ratio', 0.98) +
                              pair_info.get('data2_valid_ratio', 0.98)) / 2

            # 综合质量分数
            quality_score = (
                self.quality_weights['statistical'] * pvalue_score +
                self.quality_weights['correlation'] * corr_score +
                self.quality_weights['liquidity'] * liquidity_score
            )

            # 添加计算结果到配对信息
            pair_info['quality_score'] = quality_score
            pair_info['correlation'] = correlation
            scored_pairs.append(pair_info)

        return scored_pairs

    def select_best(self, scored_pairs):
        """筛选最佳配对

        根据质量分数排序，并确保单个股票不会出现在过多配对中

        Args:
            scored_pairs: 已评分的配对列表

        Returns:
            list: 筛选后的最佳配对列表
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