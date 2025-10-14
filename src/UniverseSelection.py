# region imports
from AlgorithmImports import *
from QuantConnect.Algorithm.Framework.Selection import FineFundamentalUniverseSelectionModel
from typing import List, Dict, Tuple, Optional
from collections import defaultdict
from datetime import timedelta
import numpy as np
# endregion


class SectorBasedUniverseSelection(FineFundamentalUniverseSelectionModel):
    """
    贝叶斯协整策略的股票选择模型

    两阶段筛选：
    1. 粗选: 价格、成交量、IPO时间筛选
    2. 精选: 财务指标、波动率、行业分组筛选
    """

    def __init__(self, algorithm):
        """初始化选股模型"""
        self.algorithm = algorithm
        self.config = algorithm.config.universe_selection
        self.sector_code_to_name = algorithm.config.sector_code_to_name
        self.sector_name_to_code = algorithm.config.sector_name_to_code

        # 状态管理
        self.selection_on = False
        self.last_fine_selected_symbols = []
        self.fine_selection_count = 0

        # 财务筛选条件（实例属性）
        self.financial_criteria = {
            'pe_failed': {
                'attr_path': ['ValuationRatios', 'PERatio'],
                'config_key': 'max_pe',
                'operator': 'lt'  # PE < max_pe
            },
            'roe_failed': {
                'attr_path': ['OperationRatios', 'ROE', 'Value'],
                'config_key': 'min_roe',
                'operator': 'gt'  # ROE > min_roe
            },
            'debt_failed': {
                'attr_path': ['OperationRatios', 'DebtToAssets', 'Value'],
                'config_key': 'max_debt_ratio',
                'operator': 'lt'  # Debt < max_debt_ratio
            },
            'leverage_failed': {
                'attr_path': ['OperationRatios', 'FinancialLeverage', 'Value'],
                'config_key': 'max_leverage_ratio',
                'operator': 'lt'  # Leverage < max_leverage_ratio
            }
        }

        super().__init__(self._select_coarse, self._select_fine)

    # ========== 公开方法 ==========

    def trigger_selection(self):
        """触发新一轮选股"""
        self.selection_on = True

    # ========== 主要筛选方法 ==========

    def _select_coarse(self, coarse: List[CoarseFundamental]) -> List[Symbol]:
        """
        粗选阶段: 基础筛选
        筛选条件: 基本面数据、价格、成交量、IPO时间
        """
        # 如果未触发选股, 返回上次结果
        if not self.selection_on:
            return self.last_fine_selected_symbols

        coarse = list(coarse)  # 转换迭代器为列表

        # 预计算筛选阈值
        min_ipo_date = self.algorithm.Time - timedelta(days=self.config['min_days_since_ipo'])
        min_price = self.config['min_price']
        min_volume = self.config['min_volume']

        # 高效筛选: 短路求值优化
        selected = [
            x.Symbol for x in coarse
            if x.HasFundamentalData                      # 排除ETF等
            and x.Price > min_price                      # 价格筛选
            and x.Volume > min_volume                    # 成交量筛选
            and x.SecurityReference.IPODate is not None
            and x.SecurityReference.IPODate <= min_ipo_date  # IPO时间
        ]

        return selected

    def _select_fine(self, fine: List[FineFundamental]) -> List[Symbol]:
        """
        精选阶段: 财务、波动率和行业筛选
        流程: 财务筛选 -> 波动率筛选 -> 行业分组
        """
        # 如果未触发选股, 返回上次结果
        if not self.selection_on:
            return self.last_fine_selected_symbols

        # 重置选股标志
        self.selection_on = False
        self.fine_selection_count += 1

        fine = list(fine)

        # 步骤1: 财务筛选 (PE, ROE, 负债率, 杠杆率)
        financially_filtered, financial_stats = self._apply_financial_filters(fine)

        # 步骤2: 波动率筛选
        volatility_filtered, volatility_stats = self._apply_volatility_filter(financially_filtered)

        # 步骤3: 行业分组和排序
        final_stocks = self._group_and_sort_by_sector(volatility_filtered)

        # 步骤4: 缓存结果
        self.last_fine_selected_symbols = [x.Symbol for x in final_stocks]

        # 步骤5: 输出统计 (精简版)
        self._log_selection_results(len(fine), final_stocks, financial_stats, volatility_stats)

        return self.last_fine_selected_symbols

    # ========== 筛选辅助方法 ==========

    def _apply_financial_filters(self, stocks: List[FineFundamental]) -> Tuple[List[FineFundamental], Dict[str, int]]:
        """应用财务筛选条件"""
        filtered_stocks = []
        stats = defaultdict(int, total=len(stocks), passed=0)

        for stock in stocks:
            passed, fail_reasons = self._check_financial_criteria(stock)

            if passed:
                filtered_stocks.append(stock)
                stats['passed'] += 1
            else:
                for reason in fail_reasons:
                    stats[reason] += 1

        return filtered_stocks, stats

    def _check_financial_criteria(self, stock: FineFundamental) -> Tuple[bool, List[str]]:
        """检查单只股票的财务指标"""
        fail_reasons = []

        if not stock.ValuationRatios or not stock.OperationRatios:
            return False, ['data_missing']

        for reason, criteria in self.financial_criteria.items():
            try:
                # 动态导航属性路径
                value = stock
                for attr in criteria['attr_path']:
                    value = getattr(value, attr, None)
                    if value is None:
                        break

                if value is None:
                    fail_reasons.append(reason)
                else:
                    threshold = self.config[criteria['config_key']]
                    # 比较操作: lt=小于, gt=大于
                    if criteria['operator'] == 'lt' and value >= threshold:
                        fail_reasons.append(reason)
                    elif criteria['operator'] == 'gt' and value <= threshold:
                        fail_reasons.append(reason)

            except (AttributeError, TypeError):
                fail_reasons.append('data_missing')
                break

        return len(fail_reasons) == 0, fail_reasons

    def _apply_volatility_filter(self, stocks: List[FineFundamental]) -> Tuple[List[FineFundamental], Dict[str, int]]:
        """计算年化波动率并筛选波动适中的股票"""
        filtered_stocks = []
        stats = {'total': len(stocks), 'passed': 0, 'volatility_failed': 0, 'data_missing': 0}

        max_volatility = self.config['max_volatility']
        # 使用统一的analysis配置参数
        lookback_days = self.algorithm.config.analysis['lookback_days']

        # 批量获取所有股票的历史数据以提升性能
        symbols = [stock.Symbol for stock in stocks]

        try:
            all_history = self.algorithm.History(
                symbols,
                lookback_days,
                Resolution.Daily
            )

            if all_history.empty:
                return [], stats

        except Exception:
            return [], stats

        # 处理每只股票
        for stock in stocks:
            try:
                # 从批量数据中提取该股票的历史
                if stock.Symbol in all_history.index.levels[0]:
                    history = all_history.loc[stock.Symbol]
                else:
                    stats['data_missing'] += 1
                    continue

                # 数据完整性检查
                min_required_days = lookback_days * self.algorithm.config.analysis['data_completeness_ratio']
                if history.empty or len(history) < min_required_days:
                    stats['data_missing'] += 1
                    continue

                # 计算日收益率和年化波动率
                closes = history['close']
                returns = closes.pct_change().dropna()
                volatility = returns.std() * np.sqrt(252)  # 年化: sqrt(252)

                if volatility <= max_volatility:
                    stock.Volatility = volatility  # 存储供后续使用
                    filtered_stocks.append(stock)
                    stats['passed'] += 1
                else:
                    stats['volatility_failed'] += 1

            except Exception:
                stats['data_missing'] += 1

        return filtered_stocks, stats

    def _group_and_sort_by_sector(self, stocks: List[FineFundamental]) -> List[FineFundamental]:
        """按行业分组，每个行业选取波动率低、成交量大的股票"""
        sector_groups = defaultdict(list)
        valid_sector_codes = set(self.sector_name_to_code.values())

        # 按行业分组
        for stock in stocks:
            sector_code = stock.AssetClassification.MorningstarSectorCode
            if sector_code in valid_sector_codes and hasattr(stock, 'Volatility'):
                sector_groups[sector_code].append(stock)

        max_per_sector = self.config['max_stocks_per_sector']
        all_selected = []

        for sector_code, sector_stocks in sector_groups.items():
            # 排序: 波动率升序, 成交量降序
            sorted_stocks = sorted(
                sector_stocks,
                key=lambda x: (x.Volatility, -x.Volume)
            )
            all_selected.extend(sorted_stocks[:max_per_sector])

        return all_selected

    # ========== 日志输出方法 ==========

    def _log_selection_results(self, initial_count: int, final_stocks: List[FineFundamental],
                              financial_stats: Dict[str, int], volatility_stats: Dict[str, int]):
        """输出选股统计信息"""
        if not self.algorithm.debug_mode:  # False=不输出
            return

        # 计算各阶段数量
        financial_passed = financial_stats['passed']
        volatility_passed = volatility_stats['passed']
        final_count = len(final_stocks)

        # 主要流程统计
        self.algorithm.Debug(
            f"第【{self.fine_selection_count}】次选股: 粗选{initial_count}只 -> 最终{final_count}只"
        )

        # 财务淘汰原因
        financial_failed = initial_count - financial_passed
        if financial_failed > 0:
            reasons = []
            if financial_stats.get('pe_failed', 0) > 0:
                reasons.append(f"PE高{financial_stats['pe_failed']}")
            if financial_stats.get('roe_failed', 0) > 0:
                reasons.append(f"ROE低{financial_stats['roe_failed']}")
            if financial_stats.get('debt_failed', 0) > 0:
                reasons.append(f"负债高{financial_stats['debt_failed']}")
            if financial_stats.get('leverage_failed', 0) > 0:
                reasons.append(f"杠杆高{financial_stats['leverage_failed']}")
            if financial_stats.get('data_missing', 0) > 0:
                reasons.append(f"数据缺失{financial_stats['data_missing']}")

            if reasons:
                self.algorithm.Debug(f"财务淘汰{financial_failed}: {', '.join(reasons)}")

        # 波动率淘汰原因
        volatility_failed = volatility_stats['total'] - volatility_passed
        if volatility_failed > 0:
            self.algorithm.Debug(
                f"波动率淘汰{volatility_failed}: 高波动{volatility_stats.get('volatility_failed', 0)}, "
                f"数据不足{volatility_stats.get('data_missing', 0)}"
            )

        # 行业分布
        if final_stocks:
            sector_dist = defaultdict(int)
            for stock in final_stocks:
                sector_code = stock.AssetClassification.MorningstarSectorCode
                sector_name = self.sector_code_to_name.get(sector_code, "未知")
                sector_dist[sector_name] += 1

            # 按数量排序
            sorted_sectors = sorted(sector_dist.items(), key=lambda x: x[1], reverse=True)
            sector_info = [f"{name}{count}只" for name, count in sorted_sectors]
            self.algorithm.Debug(f"行业分布: {', '.join(sector_info)}")