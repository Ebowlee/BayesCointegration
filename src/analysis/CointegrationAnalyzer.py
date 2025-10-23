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

    def __init__(self, algorithm, module_config: dict):
        """
        初始化协整分析器

        Args:
            algorithm: QCAlgorithm实例
            module_config: 模块配置字典
        """
        self.algorithm = algorithm
        self.pvalue_threshold = module_config['pvalue_threshold']

        # 子行业分组配置
        self.min_stocks_per_group = module_config['min_stocks_per_group']
        self.max_stocks_per_group = module_config['max_stocks_per_group']


    def cointegration_procedure(self, valid_symbols: List[Symbol], clean_data: Dict[Symbol, pd.DataFrame]) -> Dict:
        """
        执行协整分析流程（按26个子行业分组）

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
        分析单个子行业内的协整关系

        Args:
            ig_name: 子行业名称
            symbols: 该子行业内的股票列表
            clean_data: 清洗后的价格数据

        Returns:
            通过协整检验的配对列表
        """
        cointegrated_pairs = []
        failed_tests = []

        # 生成所有可能的配对组合
        for sym1, sym2 in itertools.combinations(symbols, 2):
            symbol1, symbol2 = sorted([sym1, sym2], key=lambda x: x.Value)

            try:
                prices1 = clean_data[symbol1]['close']
                prices2 = clean_data[symbol2]['close']

                # 验证数据长度一致(理论上DataProcessor已保证,但再次验证)
                if len(prices1) != len(prices2):
                    failed_tests.append((symbol1, symbol2, 'length_mismatch'))
                    continue

                # Engle-Granger协整检验
                score, pvalue, _ = coint(prices1, prices2)

                # 检查p值阈值
                if pvalue < self.pvalue_threshold:
                    cointegrated_pairs.append({
                        'symbol1': symbol1,
                        'symbol2': symbol2,
                        'pvalue': pvalue,
                        'industry_group': ig_name  # 记录子行业(用于后续分析)
                    })

            except ValueError:
                # statsmodels可能抛出ValueError(如数据退化)
                failed_tests.append((symbol1, symbol2, 'statsmodels_error'))
            except KeyError:
                # clean_data中缺少股票数据
                failed_tests.append((symbol1, symbol2, 'data_missing'))
            except Exception:
                failed_tests.append((symbol1, symbol2, 'unknown_error'))

        # 日志记录失败情况
        if failed_tests and self.algorithm.debug_mode:
            sample_failures = [f'{s1.Value}&{s2.Value}({r})' for s1, s2, r in failed_tests[:3]]
            self.algorithm.Debug(
                f"[协整分析] 子行业{ig_name}测试失败{len(failed_tests)}对: {', '.join(sample_failures)}"
                + (f" 等" if len(failed_tests) > 3 else "")
            )

        return cointegrated_pairs


    def _group_by_industry_group(self, symbols: List[Symbol]) -> Dict[str, List[Symbol]]:
        """
        按26个子行业分组，每个子行业选TOP stocks

        Args:
            symbols: UniverseSelection输出的所有通过筛选的股票

        Returns:
            {industry_group_code: [symbols]} 字典
        """
        industry_groups = defaultdict(list)

        # 步骤1: 收集每只股票的市值和子行业信息
        stock_info = []
        failed_symbols = []

        for symbol in symbols:
            try:
                security = self.algorithm.Securities[symbol]

                # 检查基本面数据完整性
                if not security.Fundamentals:
                    failed_symbols.append((symbol, 'no_fundamentals'))
                    continue

                if not security.Fundamentals.AssetClassification:
                    failed_symbols.append((symbol, 'no_classification'))
                    continue

                ig_code = security.Fundamentals.AssetClassification.MorningstarIndustryGroupCode
                market_cap = security.Fundamentals.MarketCap

                # 验证数据有效性
                if ig_code is None or market_cap is None or market_cap <= 0:
                    failed_symbols.append((symbol, 'invalid_data'))
                    continue

                stock_info.append({
                    'symbol': symbol,
                    'ig_code': ig_code,
                    'market_cap': market_cap
                })

            except AttributeError:
                failed_symbols.append((symbol, 'attribute_error'))
            except Exception:
                failed_symbols.append((symbol, 'unknown_error'))

        # 日志记录失败情况
        if failed_symbols and self.algorithm.debug_mode:
            sample_failures = [f'{s.Value}({r})' for s, r in failed_symbols[:5]]
            self.algorithm.Debug(
                f"[协整分析] 分组失败{len(failed_symbols)}只: {', '.join(sample_failures)}"
                + (f" 等" if len(failed_symbols) > 5 else "")
            )

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


