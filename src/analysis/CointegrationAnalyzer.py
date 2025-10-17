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
        self.correlation_threshold = config['correlation_threshold']

        # v6.7.2新增：子行业分组配置
        self.min_stocks_per_group = config.get('min_stocks_per_group', 5)
        self.max_stocks_per_group = config.get('max_stocks_per_group', 20)


    def cointegration_procedure(self, valid_symbols: List[Symbol], clean_data: Dict[Symbol, pd.DataFrame]) -> Dict:
        """
        执行协整分析流程（v6.7.2: 按26个子行业分组）

        Args:
            valid_symbols: UniverseSelection输出的所有通过筛选的股票
            clean_data: 清洗后的价格数据

        Returns:
            {
                'raw_pairs': [...],           # 通过协整检验的配对列表
                'statistics': {...}           # 统计信息
            }
        """
        statistics = {
            'total_pairs_tested': 0,
            'cointegrated_pairs_found': 0,
            'industry_group_breakdown': {}
        }

        # 步骤1: 按26个子行业分组（包含过滤+排序+数量限制）
        industry_groups = self._group_by_industry_group(valid_symbols)

        # 步骤2: 每个子行业内部进行协整配对
        all_cointegrated_pairs = []
        for ig_name, symbols in industry_groups.items():
            # 分析该子行业内的配对
            ig_pairs = self._analyze_industry_group(ig_name, symbols, clean_data)
            all_cointegrated_pairs.extend(ig_pairs)

            # 统计
            pairs_count = len(symbols) * (len(symbols) - 1) // 2
            statistics['industry_group_breakdown'][ig_name] = {
                'symbols': len(symbols),
                'pairs_tested': pairs_count,
                'pairs_found': len(ig_pairs)
            }
            statistics['total_pairs_tested'] += pairs_count

        statistics['cointegrated_pairs_found'] = len(all_cointegrated_pairs)

        # 输出统计
        if all_cointegrated_pairs:
            self.algorithm.Debug(
                f"[协整分析] 发现{len(all_cointegrated_pairs)}个协整对 "
                f"(测试{statistics['total_pairs_tested']}对，来自{len(industry_groups)}个子行业)"
            )

        return {
            'raw_pairs': all_cointegrated_pairs,
            'statistics': statistics
        }


    def _analyze_industry_group(self, ig_name: str, symbols: List[Symbol], clean_data: Dict) -> List[Dict]:
        """
        分析单个子行业内的协整关系（v6.7.2重命名）

        Args:
            ig_name: 子行业名称
            symbols: 该子行业内的股票列表
            clean_data: 清洗后的价格数据

        Returns:
            通过协整检验的配对列表
        """
        cointegrated_pairs = []

        # 生成所有可能的配对组合
        for sym1, sym2 in itertools.combinations(symbols, 2):
            symbol1, symbol2 = sorted([sym1, sym2], key=lambda x: x.Value)

            try:
                prices1 = clean_data[symbol1]['close']
                prices2 = clean_data[symbol2]['close']

                # Engle-Granger协整检验
                score, pvalue, _ = coint(prices1, prices2)
                correlation = prices1.corr(prices2)

                # 检查条件
                if pvalue < self.pvalue_threshold and correlation > self.correlation_threshold:
                    cointegrated_pairs.append({
                        'symbol1': symbol1,
                        'symbol2': symbol2,
                        'pvalue': pvalue,
                        'correlation': correlation,
                        'industry_group': ig_name  # 记录子行业（用于后续分析）
                    })
            except Exception:
                pass

        return cointegrated_pairs


    def _group_by_industry_group(self, symbols: List[Symbol]) -> Dict[str, List[Symbol]]:
        """
        按26个子行业分组，每个子行业选TOP stocks（v6.7.2新增）

        Args:
            symbols: UniverseSelection输出的所有通过筛选的股票

        Returns:
            {industry_group_code: [symbols]} 字典
        """
        industry_groups = defaultdict(list)

        # 步骤1: 收集每只股票的市值和子行业信息
        stock_info = []
        for symbol in symbols:
            try:
                security = self.algorithm.Securities[symbol]
                ig_code = security.Fundamentals.AssetClassification.MorningstarIndustryGroupCode
                market_cap = security.Fundamentals.MarketCap

                stock_info.append({
                    'symbol': symbol,
                    'ig_code': ig_code,
                    'market_cap': market_cap
                })
            except Exception:
                continue

        # 步骤2: 按子行业分组
        for info in stock_info:
            industry_groups[info['ig_code']].append(info)

        # 步骤3: 过滤+排序+限制数量
        valid_groups = {}
        skipped_groups = []

        for ig_code, stocks_list in industry_groups.items():
            # 过滤：至少min_stocks_per_group只
            if len(stocks_list) < self.min_stocks_per_group:
                skipped_groups.append((ig_code, len(stocks_list)))
                continue

            # 排序：按市值降序
            sorted_stocks = sorted(stocks_list, key=lambda x: x['market_cap'], reverse=True)

            # 限制：最多max_stocks_per_group只
            top_stocks = sorted_stocks[:self.max_stocks_per_group]

            # 提取symbols
            valid_groups[str(ig_code)] = [s['symbol'] for s in top_stocks]

            # 日志
            self.algorithm.Debug(
                f"[协整分析] 子行业{ig_code}: 候选{len(stocks_list)}只 → 选中{len(top_stocks)}只"
            )

        # 日志：跳过的子行业
        if skipped_groups:
            skipped_info = [f"{ig}({count}只)" for ig, count in skipped_groups]
            self.algorithm.Debug(
                f"[协整分析] 跳过{len(skipped_groups)}个子行业(股票数<{self.min_stocks_per_group}): {', '.join(skipped_info)}"
            )

        return valid_groups


