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
    """
    协整分析器 - 识别具有长期均衡关系的股票配对
    """

    def __init__(self, algorithm, config: dict):
        self.algorithm = algorithm
        self.pvalue_threshold = config['pvalue_threshold']
        self.correlation_threshold = config['correlation_threshold']  # 从配置读取


    def cointegration_procedure(self, valid_symbols: List[Symbol], clean_data: Dict[Symbol, pd.DataFrame],
                                sector_code_to_name: Dict) -> Dict:
        """
        执行协整分析流程 - 分组、检验、统计
        """

        # 初始化统计
        statistics = {'total_pairs_tested': 0, 'cointegrated_pairs_found': 0, 'sector_breakdown': {}}

        # 1. 行业分组
        sector_groups = self._group_by_sector(valid_symbols, sector_code_to_name)

        # 2. 在每个行业内部生成配对组合并进行协整检验
        all_cointegrated_pairs = []
        for sector_name, symbols in sector_groups.items():
            # 仅在同一行业内的股票之间进行配对
            sector_pairs = self._analyze_sector(sector_name, symbols, clean_data)
            all_cointegrated_pairs.extend(sector_pairs)

            # 更新统计 - C(n,2) = n*(n-1)/2 是从n个股票中选2个的组合数
            pairs_count = len(symbols) * (len(symbols) - 1) // 2
            statistics['sector_breakdown'][sector_name] = {'symbols': len(symbols), 'pairs_tested': pairs_count, 'pairs_found': len(sector_pairs)}
            statistics['total_pairs_tested'] += pairs_count

        statistics['cointegrated_pairs_found'] = len(all_cointegrated_pairs)

        # 输出统计信息
        if all_cointegrated_pairs:
            self.algorithm.Debug(
                f"[CointegrationAnalyzer] 发现{len(all_cointegrated_pairs)}个协整对 "
                f"(测试{statistics['total_pairs_tested']}对)"
            )

        return {
            'raw_pairs': all_cointegrated_pairs,  # 所有通过协整检验的配对
            'statistics': statistics
        }


    def _group_by_sector(self, valid_symbols: List[Symbol], sector_code_to_name: Dict) -> Dict[str, List[Symbol]]:
        """
        将股票按行业分组
        """
        result = defaultdict(list)
        for symbol in valid_symbols:
            security = self.algorithm.Securities[symbol]
            sector_code = security.Fundamentals.AssetClassification.MorningstarSectorCode
            sector_name = sector_code_to_name.get(sector_code)

            if sector_name:
                result[sector_name].append(symbol)

        return dict(result)


    def _analyze_sector(self, sector_name: str, symbols: List[Symbol], clean_data: Dict) -> List[Dict]:
        """
        分析单个行业内的协整关系
        """
        cointegrated_pairs = []

        # 生成所有可能的配对组合
        for sym1, sym2 in itertools.combinations(symbols, 2):
            # 确保顺序一致：按字母顺序排序
            symbol1, symbol2 = sorted([sym1, sym2], key=lambda x: x.Value)

            try:
                prices1 = clean_data[symbol1]['close']
                prices2 = clean_data[symbol2]['close']

                # Engle-Granger协整检验
                score, pvalue, _ = coint(prices1, prices2)

                # 计算相关系数
                correlation = prices1.corr(prices2)

                # 检查条件
                if pvalue < self.pvalue_threshold and correlation > self.correlation_threshold:
                    cointegrated_pairs.append({
                        'symbol1': symbol1,
                        'symbol2': symbol2,
                        'pvalue': pvalue,
                        'correlation': correlation,
                        'sector': sector_name
                    })
            except Exception:
                pass  # 忽略单个配对的错误，继续处理其他配对

        return cointegrated_pairs


