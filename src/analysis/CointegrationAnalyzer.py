# region imports
from AlgorithmImports import *
import numpy as np
import pandas as pd
from typing import Dict, List
from collections import defaultdict
import itertools
import random
from statsmodels.tsa.stattools import coint
import statsmodels.api as sm
# endregion


class CointegrationAnalyzer:
    """
    协整分析器 - 识别具有长期均衡关系的股票配对 (v7.65.0: 纯协整检验模块)

    职责:
    - 执行Engle-Granger协整检验
    - 按行业分组进行配对测试
    - 返回所有通过pvalue阈值的配对

    设计原则:
    - 单一职责: 只负责协整检验,不处理配额筛选
    - 配额管理: 由IndustryQuotaManager在后续步骤处理
    """

    def __init__(self, algorithm, module_config, data_processor_config):
        """
        初始化协整分析器 (v8.4.0: 从DataProcessorConfig读取时间窗口参数)

        Args:
            algorithm: QCAlgorithm实例
            module_config: CointegrationConfig dataclass
            data_processor_config: DataProcessorConfig dataclass (时间窗口参数来源)
        """
        self.algorithm = algorithm
        self.module_config = module_config
        self.pvalue_threshold = module_config.pvalue_threshold

        # v8.4.0: 时间窗口切片 - 从DataProcessorConfig统一读取 (消除重复定义)
        self.bayesian_lookback_days = data_processor_config.bayesian_lookback_days

        # 行业分组配置
        self.min_stocks_per_industry = module_config.min_stocks_per_industry
        self.max_stocks_per_industry = module_config.max_stocks_per_industry


    def _run_ols_regression(self, log_prices1: np.ndarray, log_prices2: np.ndarray) -> Dict:
        """
        OLS回归获取α, β, σ的估计值及标准误 (v8.5.0 Empirical Bayes)

        模型: log(P1) = α + β × log(P2) + ε

        Returns:
            {
                'alpha_ols': α̂ (截距),
                'alpha_se': se(α̂),
                'beta_ols': β̂ (斜率),
                'beta_se': se(β̂),
                'sigma_ols': σ̂_resid (残差标准差)
            }
        """
        X = sm.add_constant(log_prices2)
        results = sm.OLS(log_prices1, X).fit()
        return {
            'alpha_ols': results.params[0],
            'alpha_se': results.bse[0],
            'beta_ols': results.params[1],
            'beta_se': results.bse[1],
            'sigma_ols': np.std(results.resid),
        }


    def _is_etf(self, symbol: Symbol) -> bool:
        """
        检测symbol是否为ETF (v8.0.0)

        Args:
            symbol: 要检测的Symbol对象

        Returns:
            True: 是ETF, False: 是股票
        """
        return symbol in self.algorithm.etf_symbols


    def _get_etf_industries(self, symbol: Symbol) -> List[int]:
        """
        获取ETF的所有行业代码 (v8.1.0: 支持多行业映射)

        Args:
            symbol: ETF Symbol对象

        Returns:
            行业代码列表, 如:
            - 单行业ETF: [31130] (SOXX → 半导体)
            - 多行业ETF: [10150, 10160] (XME → 金属矿业+钢铁)
            - 映射缺失: [] (空列表)

        设计说明:
            v8.1.0改进: 从返回单个int改为返回List[int],
            使单个ETF能同时参与多个行业的协整检验,
            充分利用配置层的多行业映射意图。
        """
        ticker = symbol.Value
        if ticker not in self.algorithm.etf_industry_mapping:
            return []

        industries = self.algorithm.etf_industry_mapping[ticker]

        # 统一返回列表格式 (兼容单个int和List[int])
        if isinstance(industries, list):
            return industries
        else:
            return [industries]


    def cointegration_procedure(self, valid_symbols: List[Symbol], clean_data: Dict[Symbol, pd.DataFrame]) -> Dict:
        """
        执行协整分析流程（按26个子行业分组）

        Args:
            valid_symbols: UniverseSelection输出的所有通过筛选的股票
            clean_data: 清洗后的价格数据

        Returns:
            {
                'pairs': [...],               # 通过协整检验的配对列表 (v7.67.0: 重命名)
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

        # v8.16.0: 随机抽样限流 (MCMC算力保护)
        original_count = len(all_cointegrated_pairs)
        max_pairs = self.module_config.max_cointegrated_pairs
        if original_count > max_pairs:
            all_cointegrated_pairs = random.sample(all_cointegrated_pairs, max_pairs)
            self.algorithm.Debug(
                f"[协整限流] {original_count}对 → 随机抽样{max_pairs}对"
            )
            statistics['sampled_pairs'] = max_pairs
        else:
            statistics['sampled_pairs'] = original_count

        return {
            'pairs': all_cointegrated_pairs,  # v7.67.0: 简化键名 raw_pairs → pairs
            'statistics': statistics
        }


    def _find_cointegrated_pairs_in_group(self, ig_name: str, symbols: List[Symbol], clean_data: Dict) -> List[Dict]:
        """
        在单个子行业内查找协整配对 (v8.4.0: 时间窗口切片,消除数据窥探)

        Args:
            ig_name: 子行业名称
            symbols: 该子行业内的股票列表
            clean_data: 清洗后的价格数据 (312天完整数据)

        Returns:
            通过pvalue阈值的所有配对列表 (按pvalue排序)

        v8.4.0变更:
            协整检验使用 data[:-60] (前252天)，与MCMC窗口(后60天)完全隔离
        """
        cointegrated_pairs = []
        failed_tests = []

        # v8.4.0: 计算协整检验的切片范围 (排除后60天)
        slice_end = -self.bayesian_lookback_days if self.bayesian_lookback_days > 0 else None

        # 对所有配对执行协整检验
        for sym1, sym2 in itertools.combinations(symbols, 2):
            symbol1, symbol2 = sorted([sym1, sym2], key=lambda x: x.Value)

            try:
                # v8.4.0: 只使用前252天数据进行协整检验 (消除数据窥探)
                prices1 = clean_data[symbol1]['close'].iloc[:slice_end]
                prices2 = clean_data[symbol2]['close'].iloc[:slice_end]

                # 验证数据长度一致(理论上DataProcessor已保证,但再次验证)
                if len(prices1) != len(prices2):
                    failed_tests.append((symbol1, symbol2, 'length_mismatch'))
                    continue

                # 验证时间索引一致 (防止错位)
                if not prices1.index.equals(prices2.index):
                    failed_tests.append((symbol1, symbol2, 'index_mismatch'))
                    continue

                # Engle-Granger协整检验 (使用前252天数据)
                score, pvalue, _ = coint(prices1, prices2)

                # 检查p值阈值
                if pvalue < self.pvalue_threshold:
                    # v8.5.0: OLS回归获取先验参数 (Empirical Bayes)
                    ols_result = self._run_ols_regression(
                        np.log(prices1.values), np.log(prices2.values)
                    )
                    cointegrated_pairs.append({
                        'symbol1': symbol1,
                        'symbol2': symbol2,
                        'pvalue': pvalue,
                        'industry_code': int(ig_name),  # v7.40.8: 统一使用整数格式
                        **ols_result,  # v8.5.0: 展开OLS结果 (5个字段)
                    })

            except ValueError:
                failed_tests.append((symbol1, symbol2, 'statsmodels_error'))
            except KeyError:
                failed_tests.append((symbol1, symbol2, 'data_missing'))
            except Exception:
                failed_tests.append((symbol1, symbol2, 'unknown_error'))

        # 按pvalue排序后返回(便于后续配额管理器使用)
        sorted_pairs = sorted(cointegrated_pairs, key=lambda x: x['pvalue'])

        return sorted_pairs



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
        """
        industry_groups = defaultdict(list)

        # 步骤1: 按行业分组
        failed_symbols = []

        for symbol in symbols:
            try:
                # v8.1.0: ETF分支处理 (支持多行业映射)
                if self._is_etf(symbol):
                    industries = self._get_etf_industries(symbol)
                    if not industries:
                        failed_symbols.append((symbol, 'etf_no_mapping'))
                        continue

                    # 关键改进: 将同一个ETF添加到多个行业分组
                    for ig_code in industries:
                        industry_groups[ig_code].append({
                            'symbol': symbol,
                            'ig_code': ig_code
                        })
                    continue

                # 股票路径: 使用Fundamentals (现有逻辑)
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

