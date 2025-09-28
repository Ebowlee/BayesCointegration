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
        self.min_data_completeness_ratio = config.get('min_data_completeness_ratio', 0.98)

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

            # 获取价格数据
            data1 = clean_data[symbol1]
            data2 = clean_data[symbol2]

            # 半衰期分数（均值回归速度）
            half_life_score = self._calculate_half_life_score(
                data1['close'] if 'close' in data1.columns else data1,
                data2['close'] if 'close' in data2.columns else data2,
                pair_info.get('bayesian_results', {})
            )

            # 波动率比率分数（稳定性）
            volatility_ratio_score = self._calculate_volatility_ratio_score(
                data1['close'] if 'close' in data1.columns else data1,
                data2['close'] if 'close' in data2.columns else data2,
                pair_info.get('bayesian_results', {})
            )

            # 流动性分数（基于成交量）
            liquidity_score = self._calculate_liquidity_score(data1, data2)

            # 综合质量分数
            quality_score = (
                self.quality_weights['statistical'] * pvalue_score +
                self.quality_weights['half_life'] * half_life_score +
                self.quality_weights['volatility_ratio'] * volatility_ratio_score +
                self.quality_weights['liquidity'] * liquidity_score
            )

            # 添加计算结果到配对信息（供调试使用）
            pair_info['quality_score'] = quality_score
            pair_info['half_life_score'] = half_life_score
            pair_info['volatility_ratio_score'] = volatility_ratio_score
            pair_info['liquidity_score'] = liquidity_score
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

    def _calculate_half_life_score(self, data1, data2, bayesian_results):
        """计算半衰期分数（均值回归速度）

        Args:
            data1: 股票1的价格数据
            data2: 股票2的价格数据
            bayesian_results: 贝叶斯模型结果

        Returns:
            float: 半衰期分数 (0-1)
        """
        try:
            # 直接从bayesian_results获取残差序列
            residuals = bayesian_results.get('residuals_array')
            if residuals is None or len(residuals) < 2:
                return 0  # 无数据，返回0分

            # AR(1)自相关
            residuals_lag = residuals[:-1]
            residuals_curr = residuals[1:]

            # 计算自相关系数
            import numpy as np
            rho = np.corrcoef(residuals_lag, residuals_curr)[0, 1]

            # 计算半衰期
            if 0 < rho < 1:
                half_life = -np.log(2) / np.log(rho)

                # 简单的线性归一化：5天=1.0, 30天=0.2
                if half_life <= 5:
                    score = 1.0
                elif half_life >= 30:
                    score = 0.2
                else:
                    # 线性插值
                    score = 1.0 - (half_life - 5) * 0.8 / 25

                return score
            else:
                # rho <= 0 或 rho >= 1：无均值回归
                return 0  # 明确的失败信号

        except Exception as e:
            self.algorithm.Debug(f"[PairSelector] 半衰期计算失败: {e}")
            return 0  # 异常返回0分

    def _calculate_volatility_ratio_score(self, data1, data2, bayesian_results):
        """计算波动率比率分数（稳定性）

        Args:
            data1: 股票1的价格数据
            data2: 股票2的价格数据
            bayesian_results: 贝叶斯模型结果

        Returns:
            float: 波动率比率分数 (0-1)
        """
        try:
            import numpy as np

            # 获取残差（或重新计算）
            residuals = bayesian_results.get('residuals_array')
            if residuals is None:
                alpha = bayesian_results.get('alpha_mean', 0)
                beta = bayesian_results.get('beta_mean', 1)
                residuals = np.log(data2) - (alpha + beta * np.log(data1))

            spread_vol = np.std(residuals)

            # 计算对数收益率波动率
            log_returns1 = np.diff(np.log(data1))
            log_returns2 = np.diff(np.log(data2))
            vol1 = np.std(log_returns1)
            vol2 = np.std(log_returns2)

            # 安全检查
            if vol1 <= 0 or vol2 <= 0:
                return 0  # 异常数据，返回0分

            # 计算比率
            combined_vol = np.sqrt(vol1**2 + vol2**2)
            volatility_ratio = spread_vol / combined_vol

            # 线性归一化：0.2以下满分，1.0以上0分
            if volatility_ratio <= 0.2:
                score = 1.0
            elif volatility_ratio >= 1.0:
                score = 0.0
            else:
                # 线性插值
                score = 1.0 - (volatility_ratio - 0.2) / 0.8

            return score

        except Exception as e:
            self.algorithm.Debug(f"[PairSelector] 波动率比率计算失败: {e}")
            return 0  # 异常返回0分

    def _calculate_liquidity_score(self, data1, data2):
        """基于成交量的流动性评分（千万美元归一化）

        Args:
            data1: 包含volume和close的DataFrame
            data2: 包含volume和close的DataFrame

        Returns:
            float: 流动性分数 (0-1)
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

            # 五千万美元归一化（5千万美元 = 1.0分）
            liquidity_score = min(1.0, min_dollar_volume / 5e7)

            return liquidity_score

        except Exception as e:
            self.algorithm.Debug(f"[PairSelector] 流动性计算失败: {e}")
            return 0  # 异常返回0分