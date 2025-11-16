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
    协整分析器 - 识别具有长期均衡关系的股票配对 (v7.12.0: 支持行业配额)
    """

    def __init__(self, algorithm, module_config, industry_quotas: Dict[str, int] = None):
        """
        初始化协整分析器 (v7.12.0: 新增行业配额参数)

        Args:
            algorithm: QCAlgorithm实例
            module_config: 模块配置对象 (CointegrationConfig dataclass)
            industry_quotas: 行业配额字典 {industry_code: quota}
                - 如果为None或空字典,使用默认配额 (从config.industry_quota.default_quota读取)
                - 如果提供,使用动态配额
        """
        self.algorithm = algorithm
        self.module_config = module_config  # v7.30.1: 保存config引用
        self.pvalue_threshold = module_config.pvalue_threshold

        # 行业分组配置 (v7.30.4: 新增max_stocks_per_industry)
        self.min_stocks_per_industry = module_config.min_stocks_per_industry
        self.max_stocks_per_industry = module_config.max_stocks_per_industry

        # v7.12.0: 行业配额
        self.industry_quotas = industry_quotas if industry_quotas else {}
        self.default_quota = algorithm.config.industry_quota.default_quota

        # v7.31.0: 单股重复限制 (从PairSelector迁移)
        self.max_symbol_repeats = module_config.max_symbol_repeats


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

        # 步骤1: 按行业分组（包含过滤+排序+数量限制）
        industry_groups = self._group_by_industry(valid_symbols)

        # 步骤2: 每个行业内部进行协整配对
        all_cointegrated_pairs = []
        for ig_name, symbols in industry_groups.items():
            # 查找该子行业内的协整配对
            ig_pairs = self._find_cointegrated_pairs_in_group(ig_name, symbols, clean_data)
            all_cointegrated_pairs.extend(ig_pairs)

            # v7.28.3: 日志已移至_find_cointegrated_pairs_in_group()内部
            # 由该方法统一打印完整流程（包含配额信息）

            # 统计
            pairs_count = len(symbols) * (len(symbols) - 1) // 2
            statistics['industry_group_breakdown'][ig_name] = {
                'symbols': len(symbols),
                'pairs_tested': pairs_count,
                'pairs_found': len(ig_pairs)
            }
            statistics['total_pairs_tested'] += pairs_count

        statistics['cointegrated_pairs_found'] = len(all_cointegrated_pairs)


        return {
            'raw_pairs': all_cointegrated_pairs,
            'statistics': statistics
        }


    def _find_cointegrated_pairs_in_group(self, ig_name: str, symbols: List[Symbol], clean_data: Dict) -> List[Dict]:
        """
        在单个子行业内查找协整配对 (v7.12.0: 应用行业配额)

        Args:
            ig_name: 子行业名称
            symbols: 该子行业内的股票列表
            clean_data: 清洗后的价格数据

        Returns:
            通过协整检验的配对列表 (v7.12.0: 应用配额后TOP N配对)

        v7.12.0配额逻辑:
        1. 执行所有配对的协整检验
        2. 按pvalue排序 (从小到大,p值越小越显著)
        3. 选取TOP N配对 (N = 该行业配额)
        """
        cointegrated_pairs = []
        failed_tests = []

        # 步骤1: 生成所有可能的配对组合并检验
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

        # 步骤2: v7.31.0 应用行业配额 + 单股重复限制 (贪心算法)
        # 获取该行业的配额 (如果industry_quotas为空,使用default_quota)
        quota = self.industry_quotas.get(ig_name, self.default_quota)

        # 按pvalue排序 (从小到大,p值越小协整关系越显著)
        sorted_pairs = sorted(cointegrated_pairs, key=lambda x: x['pvalue'])

        # v7.31.0: 贪心算法同时应用配额和重复限制
        selected_pairs = []
        symbol_counts = defaultdict(int)

        for pair in sorted_pairs:
            # 检查配额
            if len(selected_pairs) >= quota:
                break

            # 检查单股重复限制
            s1, s2 = pair['symbol1'], pair['symbol2']
            if (symbol_counts[s1] < self.max_symbol_repeats and
                symbol_counts[s2] < self.max_symbol_repeats):
                selected_pairs.append(pair)
                symbol_counts[s1] += 1
                symbol_counts[s2] += 1

        # v7.28.3: 统一日志 - 显示完整流程（合并原Lines 73-77和165-169）
        industry_names = self.algorithm.config.constants['industry_names']
        industry_name = industry_names.get(int(ig_name), f'未知({ig_name})')

        # 计算相关数量
        num_stocks = len(symbols) if 'symbols' in locals() else 0
        # symbols参数传入,但我们可以从cointegrated_pairs推算检测总数
        # 实际上这里无法直接获取symbols变量,需要从方法签名传递过来
        # 但为了保持完整性,我们从len(cointegrated_pairs) + len(failed_tests)推算
        # 更准确的方式是: n_choose_2 = n*(n-1)/2, 但我们从参数获取

        self.algorithm.Debug(
            f"[协整分析] {industry_name}({ig_name}): "
            f"{len(symbols)}只股票 → 配对{len(symbols)*(len(symbols)-1)//2}对 → "
            f"PValue通过{len(sorted_pairs)}对 → 配额{quota} → 最终选取{len(selected_pairs)}对",
            level=1
        )

        return selected_pairs


    def _group_by_industry(self, symbols: List[Symbol]) -> Dict[str, List[Symbol]]:
        """
        按55个行业分组 - v7.30.4简化

        Args:
            symbols: 已通过Fine筛选的股票列表 (已在粗选阶段按Volume全局排序)

        Returns:
            {industry_code: [symbols]} 字典

        流程:
        1. 按MorningstarIndustryGroupCode分组
        2. 过滤: 最少min_stocks_per_industry只
        3. 限制: 最多max_stocks_per_industry只 (直接切片,无需排序)

        v7.30.4变更:
        - 移除行业内Volume排序 (粗选阶段已完成全局Volume排序)
        - 新增max_stocks_per_industry参数 (直接限制行业股票数上限)

        v7.30.1变更:
        - 移除市值筛选逻辑,只用Volume筛选
        """
        industry_groups = defaultdict(list)

        # 步骤1: 按行业分组
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

                # 验证数据有效性
                if ig_code is None:
                    failed_symbols.append((symbol, 'invalid_data'))
                    continue

                # 直接添加到分组
                industry_groups[ig_code].append({
                    'symbol': symbol,
                    'ig_code': ig_code
                })

            except AttributeError:
                failed_symbols.append((symbol, 'attribute_error'))
            except Exception:
                failed_symbols.append((symbol, 'unknown_error'))

        # 步骤2: 过滤行业股票数 (最少min只,最多max只)
        valid_groups = {}
        skipped_groups = []

        for ig_code, stocks_list in industry_groups.items():
            # 过滤: 至少min_stocks_per_industry只
            if len(stocks_list) < self.min_stocks_per_industry:
                skipped_groups.append((ig_code, len(stocks_list)))
                continue

            # 限制: 最多max_stocks_per_industry只 (粗选已排序,直接切片)
            limited_stocks = stocks_list[:self.max_stocks_per_industry]

            # 提取symbols
            valid_groups[str(ig_code)] = [s['symbol'] for s in limited_stocks]

        return valid_groups




