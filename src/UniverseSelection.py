# region imports
from AlgorithmImports import *
from QuantConnect.Algorithm.Framework.Selection import FineFundamentalUniverseSelectionModel
from typing import List, Dict, Tuple, Optional
from collections import defaultdict
from datetime import timedelta
import numpy as np
# endregion


class FinancialValidator:
    """
    财务指标验证器 (v6.7.0)

    职责: 根据配置化的规则验证股票的财务指标
    优势: 单一职责、可配置、易测试、易扩展
    """

    def __init__(self, config: dict):
        """
        初始化财务验证器

        Args:
            config: universe_selection配置字典
        """
        self.config = config                               # universe_selection配置字典
        self.filters = config.get('financial_filters', {}) # 财务筛选器规则配置

    def validate_stock(self, stock: FineFundamental) -> Tuple[bool, List[str]]:
        """
        验证单只股票的财务指标

        Args:
            stock: 股票基本面数据

        Returns:
            (是否通过, 失败原因列表)
        """
        # 基础数据检查
        if not stock.ValuationRatios or not stock.OperationRatios:
            return False, ['data_missing']

        fail_reasons = []

        # 遍历所有启用的筛选器
        for filter_name, filter_config in self.filters.items():
            if not filter_config.get('enabled', True):
                continue

            # 获取指标值
            value = self._get_metric_value(stock, filter_config['path'])
            if value is None:
                fail_reasons.append(filter_config['fail_key'])
                continue

            # 获取阈值
            threshold_key = filter_config['threshold_key']
            threshold = self.config[threshold_key]

            # 比较操作
            operator = filter_config['operator']
            if operator == 'lt' and value >= threshold:
                fail_reasons.append(filter_config['fail_key'])
            elif operator == 'gt' and value <= threshold:
                fail_reasons.append(filter_config['fail_key'])

        return len(fail_reasons) == 0, fail_reasons

    def _get_metric_value(self, stock: FineFundamental, path: str) -> Optional[float]:
        """
        通过路径字符串获取嵌套对象的属性值

        Args:
            stock: 股票对象
            path: 点分隔的属性路径 (如 'ValuationRatios.PERatio')

        Returns:
            指标值或None (路径不存在或类型错误时返回None)
        """
        try:
            value = stock
            # 按路径逐层解析对象属性 (如 'ValuationRatios.PERatio' → stock.ValuationRatios.PERatio)
            for attr in path.split('.'):
                value = getattr(value, attr, None)  # 获取下一层属性,不存在返回None
                if value is None:
                    return None
            return value
        except (AttributeError, TypeError):
            return None


class SelectionLogger:
    """
    选股日志记录器 (v6.7.0)

    职责: 统一管理选股过程的日志输出
    优势: 单一职责、格式统一、易于维护
    """

    def __init__(self, algorithm, sector_code_to_name: dict):
        """
        初始化日志记录器

        Args:
            algorithm: QuantConnect算法实例
            sector_code_to_name: 行业代码到名称的映射字典
        """
        self.algorithm = algorithm                         # QuantConnect算法实例
        self.sector_code_to_name = sector_code_to_name     # 行业代码到名称映射

    def log_selection_summary(self, round_num: int, initial_count: int,
                              final_count: int, financial_stats: Dict[str, int],
                              volatility_stats: Dict[str, int], final_stocks: List[FineFundamental]):
        """
        输出选股流程的完整统计信息

        Args:
            round_num: 选股轮次
            initial_count: 粗选数量
            final_count: 最终数量
            financial_stats: 财务筛选统计
            volatility_stats: 波动率筛选统计
            final_stocks: 最终选中的股票列表
        """
        if not self.algorithm.debug_mode:
            return

        # 主要流程统计
        self.algorithm.Debug(
            f"第【{round_num}】次选股: 粗选{initial_count}只 -> 最终{final_count}只"
        )

        # 财务淘汰原因
        self._log_financial_failures(initial_count, financial_stats)

        # 波动率淘汰原因
        self._log_volatility_failures(volatility_stats)

        # 行业分布
        self._log_sector_distribution(final_stocks)

    def _log_financial_failures(self, initial_count: int, stats: Dict[str, int]):
        """
        记录财务筛选淘汰原因

        Args:
            initial_count: 粗选股票总数
            stats: 财务筛选统计字典,包含'passed'和各种失败原因计数
        """
        financial_passed = stats.get('passed', 0)
        financial_failed = initial_count - financial_passed

        if financial_failed <= 0:
            return

        reasons = []
        if stats.get('pe_failed', 0) > 0:
            reasons.append(f"PE高{stats['pe_failed']}")
        if stats.get('roe_failed', 0) > 0:
            reasons.append(f"ROE低{stats['roe_failed']}")
        if stats.get('debt_failed', 0) > 0:
            reasons.append(f"负债高{stats['debt_failed']}")
        if stats.get('leverage_failed', 0) > 0:
            reasons.append(f"杠杆高{stats['leverage_failed']}")
        if stats.get('data_missing', 0) > 0:
            reasons.append(f"数据缺失{stats['data_missing']}")

        if reasons:
            self.algorithm.Debug(f"财务淘汰{financial_failed}: {', '.join(reasons)}")

    def _log_volatility_failures(self, stats: Dict[str, int]):
        """
        记录波动率筛选淘汰原因

        Args:
            stats: 波动率筛选统计字典,包含'total'、'passed'、'volatility_failed'、'data_missing'
        """
        volatility_failed = stats['total'] - stats['passed']
        if volatility_failed > 0:
            self.algorithm.Debug(
                f"波动率淘汰{volatility_failed}: "
                f"高波动{stats.get('volatility_failed', 0)}, "
                f"数据不足{stats.get('data_missing', 0)}"
            )

    def _log_sector_distribution(self, stocks: List[FineFundamental]):
        """
        记录最终选中股票的行业分布情况

        Args:
            stocks: 最终选中的股票列表
        """
        if not stocks:
            return

        sector_dist = defaultdict(int)
        for stock in stocks:
            sector_code = stock.AssetClassification.MorningstarSectorCode
            sector_name = self.sector_code_to_name.get(sector_code, "未知")
            sector_dist[sector_name] += 1

        # 按数量排序
        sorted_sectors = sorted(sector_dist.items(), key=lambda x: x[1], reverse=True)
        sector_info = [f"{name}{count}只" for name, count in sorted_sectors]
        self.algorithm.Debug(f"行业分布: {', '.join(sector_info)}")


class SectorBasedUniverseSelection(FineFundamentalUniverseSelectionModel):
    """
    贝叶斯协整策略的股票选择模型

    两阶段筛选：
    1. 粗选: 价格、成交量、IPO时间筛选
    2. 精选: 财务指标、波动率、行业分组筛选
    """

    def __init__(self, algorithm):
        """初始化选股模型 (v6.7.0: 使用辅助类重构)"""
        self.algorithm = algorithm
        self.config = algorithm.config.universe_selection
        self.sector_code_to_name = algorithm.config.sector_code_to_name
        self.sector_name_to_code = algorithm.config.sector_name_to_code

        # 状态管理
        self.selection_on = False
        self.last_fine_selected_symbols = []
        self.fine_selection_count = 0

        # 辅助类实例 (v6.7.0: 职责分离)
        self.financial_validator = FinancialValidator(self.config)
        self.logger = SelectionLogger(algorithm, self.sector_code_to_name)

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
        精选阶段: 财务、波动率和行业筛选 (v6.7.0: 使用辅助类重构)
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

        # 步骤2: 波动率计算
        volatilities = self._calculate_volatilities(financially_filtered)

        # 步骤3: 波动率筛选
        volatility_filtered, volatility_stats = self._apply_volatility_filter(
            financially_filtered, volatilities
        )

        # 步骤4: 行业分组和排序
        final_stocks = self._group_and_sort_by_sector(volatility_filtered)

        # 步骤5: 缓存结果
        self.last_fine_selected_symbols = [x.Symbol for x in final_stocks]

        # 步骤6: 输出统计 (使用SelectionLogger)
        self.logger.log_selection_summary(
            self.fine_selection_count, len(fine), len(final_stocks),
            financial_stats, volatility_stats, final_stocks
        )

        return self.last_fine_selected_symbols

    # ========== 筛选辅助方法 ==========

    def _apply_financial_filters(self, stocks: List[FineFundamental]) -> Tuple[List[FineFundamental], Dict[str, int]]:
        """
        应用财务筛选条件 (v6.7.0: 使用FinancialValidator)

        Args:
            stocks: 待筛选的股票列表

        Returns:
            (通过的股票列表, 统计信息字典)
        """
        filtered_stocks = []
        stats = defaultdict(int, total=len(stocks), passed=0)

        for stock in stocks:
            # 使用FinancialValidator进行验证
            passed, fail_reasons = self.financial_validator.validate_stock(stock)

            if passed:
                filtered_stocks.append(stock)
                stats['passed'] += 1
            else:
                for reason in fail_reasons:
                    stats[reason] += 1

        return filtered_stocks, stats

    def _calculate_volatilities(self, stocks: List[FineFundamental]) -> Dict[Symbol, float]:
        """
        批量计算股票的年化波动率 (v6.7.0: 分离计算逻辑)

        Args:
            stocks: 待计算的股票列表

        Returns:
            {Symbol: 年化波动率} 字典
        """
        volatilities = {}
        lookback_days = self.algorithm.config.analysis['lookback_days']
        annualization_factor = self.config['annualization_factor']
        min_required_days = lookback_days * self.algorithm.config.analysis['data_completeness_ratio']

        # 批量获取历史数据
        symbols = [stock.Symbol for stock in stocks]
        try:
            all_history = self.algorithm.History(symbols, lookback_days, Resolution.Daily)
            if all_history.empty:
                return volatilities
        except Exception:
            return volatilities

        # 计算每只股票的波动率
        for stock in stocks:
            try:
                if stock.Symbol not in all_history.index.levels[0]:
                    continue

                history = all_history.loc[stock.Symbol]

                # 数据完整性检查
                if history.empty or len(history) < min_required_days:
                    continue

                # 计算年化波动率
                closes = history['close']
                returns = closes.pct_change().dropna()
                volatility = returns.std() * np.sqrt(annualization_factor)

                volatilities[stock.Symbol] = volatility

            except Exception:
                continue

        return volatilities

    def _apply_volatility_filter(self, stocks: List[FineFundamental],
                                 volatilities: Dict[Symbol, float]) -> Tuple[List[FineFundamental], Dict[str, int]]:
        """
        应用波动率筛选 (v6.7.0: 接受预计算的波动率)

        Args:
            stocks: 待筛选的股票列表
            volatilities: 预计算的波动率字典

        Returns:
            (通过的股票列表, 统计信息字典)
        """
        filtered_stocks = []
        stats = {'total': len(stocks), 'passed': 0, 'volatility_failed': 0, 'data_missing': 0}
        max_volatility = self.config['max_volatility']

        for stock in stocks:
            # 检查是否有波动率数据
            if stock.Symbol not in volatilities:
                stats['data_missing'] += 1
                continue

            volatility = volatilities[stock.Symbol]

            # 波动率筛选
            if volatility <= max_volatility:
                stock.Volatility = volatility  # 存储供后续使用
                filtered_stocks.append(stock)
                stats['passed'] += 1
            else:
                stats['volatility_failed'] += 1

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