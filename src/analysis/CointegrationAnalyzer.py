# region imports
from AlgorithmImports import *
import numpy as np
import pandas as pd
from typing import Dict, List
from collections import defaultdict
import itertools
from statsmodels.tsa.stattools import coint
# endregion


class CointegrationAnalyzer:
    """协整分析器 - 识别具有长期均衡关系的股票配对"""

    def __init__(self, algorithm, config: dict):
        self.algorithm = algorithm
        self.pvalue_threshold = config['pvalue_threshold']
        self.correlation_threshold = config['correlation_threshold']

    def find_cointegrated_pairs(self, valid_symbols: List[Symbol], clean_data: Dict[Symbol, pd.DataFrame],
                                sector_code_to_name: Dict) -> Dict:
        """找出所有通过协整检验的配对（不做筛选）"""
        # 初始化统计
        statistics = {
            'total_pairs_tested': 0,
            'cointegrated_pairs_found': 0,
            'sector_breakdown': {}
        }

        # 1. 行业分组
        sector_groups = self._group_by_sector(valid_symbols, sector_code_to_name)

        # 2. 生成配对并进行协整检验
        all_cointegrated_pairs = []

        for sector_name, symbols in sector_groups.items():
            sector_pairs = self._analyze_sector(sector_name, symbols, clean_data)
            all_cointegrated_pairs.extend(sector_pairs)

            # 更新统计
            pairs_count = len(symbols) * (len(symbols) - 1) // 2
            statistics['sector_breakdown'][sector_name] = {
                'symbols': len(symbols),
                'pairs_tested': pairs_count,
                'pairs_found': len(sector_pairs)
            }
            statistics['total_pairs_tested'] += pairs_count

        statistics['cointegrated_pairs_found'] = len(all_cointegrated_pairs)

        # 输出统计信息
        if all_cointegrated_pairs:
            self.algorithm.Debug(
                f"[AlphaModel.Coint] 发现{len(all_cointegrated_pairs)}个协整对 "
                f"(测试{statistics['total_pairs_tested']}对)"
            )

        return {
            'raw_pairs': all_cointegrated_pairs,  # 所有通过协整检验的配对
            'statistics': statistics
        }

    def _group_by_sector(self, valid_symbols: List[Symbol], sector_code_to_name: Dict) -> Dict[str, List[Symbol]]:
        """将股票按行业分组"""
        result = defaultdict(list)
        for symbol in valid_symbols:
            security = self.algorithm.Securities[symbol]
            sector_code = security.Fundamentals.AssetClassification.MorningstarSectorCode
            sector_name = sector_code_to_name.get(sector_code)

            if sector_name:
                result[sector_name].append(symbol)

        return dict(result)

    def _analyze_sector(self, sector_name: str, symbols: List[Symbol], clean_data: Dict) -> List[Dict]:
        """分析单个行业内的协整关系"""
        cointegrated_pairs = []

        # 生成所有可能的配对组合
        for symbol1, symbol2 in itertools.combinations(symbols, 2):
            prices1 = clean_data[symbol1]['close']
            prices2 = clean_data[symbol2]['close']

            # 测试协整性
            pair_result = self._test_pair_cointegration(
                symbol1, symbol2, prices1, prices2, sector_name,
                clean_data[symbol1], clean_data[symbol2]
            )
            if pair_result:
                cointegrated_pairs.append(pair_result)

        return cointegrated_pairs

    def _test_pair_cointegration(self, symbol1: Symbol, symbol2: Symbol,
                                prices1: pd.Series, prices2: pd.Series,
                                sector_name: str, data1: pd.DataFrame = None,
                                data2: pd.DataFrame = None) -> Dict:
        """测试单个配对的协整性"""
        try:
            # Engle-Granger协整检验
            score, pvalue, _ = coint(prices1, prices2)

            # 计算相关系数
            correlation = prices1.corr(prices2)

            # 检查条件
            if pvalue < self.pvalue_threshold and correlation > self.correlation_threshold:
                # 计算流动性匹配
                liquidity_match = 0.5
                if data1 is not None and data2 is not None:
                    liquidity_match = self._calculate_liquidity_match(data1, data2)

                return {
                    'symbol1': symbol1,
                    'symbol2': symbol2,
                    'pvalue': pvalue,
                    'correlation': correlation,
                    'sector': sector_name,
                    'liquidity_match': liquidity_match
                }
        except Exception:
            pass
        return None

    def _calculate_liquidity_match(self, data1: pd.DataFrame, data2: pd.DataFrame) -> float:
        """计算流动性匹配度"""
        try:
            # 计算平均日成交额
            avg_dollar_volume1 = (data1['volume'] * data1['close']).mean()
            avg_dollar_volume2 = (data2['volume'] * data2['close']).mean()

            if avg_dollar_volume1 == 0 or avg_dollar_volume2 == 0:
                return 0

            # 成交额比率
            volume_ratio = min(avg_dollar_volume1, avg_dollar_volume2) / max(avg_dollar_volume1, avg_dollar_volume2)

            return float(volume_ratio)

        except Exception:
            return 0.5

