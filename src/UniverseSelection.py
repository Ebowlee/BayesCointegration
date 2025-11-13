# region imports
from AlgorithmImports import *
from QuantConnect.Algorithm.Framework.Selection import FineFundamentalUniverseSelectionModel
from typing import List, Dict, Tuple, Optional
from collections import defaultdict
from datetime import timedelta
# endregion


class FinancialValidator:
    """
    财务指标验证器

    职责: 根据配置化的规则验证股票的财务指标
    优势: 单一职责、可配置、易测试、易扩展
    """

    def __init__(self, config: dict):
        """
        初始化财务验证器

        Args:
            config: universe_selection配置字典
        """
        self.config = config                               
        self.filters = config.get('financial_filters', {}) 


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
            threshold = filter_config['threshold']

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



class SectorBasedUniverseSelection(FineFundamentalUniverseSelectionModel):
    """
    贝叶斯协整策略的股票选择模型

    两阶段筛选：
    1. 粗选: 价格、成交量、IPO时间筛选
    2. 精选: 财务指标筛选 (PE, ROE, 负债率, 杠杆率)

    注: v7.9.3移除波动率筛选(历史数据证明过滤效果<1%,成本高收益低)
    """

    def __init__(self, algorithm):
        """初始化选股模型"""
        self.algorithm = algorithm
        self.config = algorithm.config.universe_selection

        # 状态管理
        self.selection_on = False
        self.last_fine_selected_symbols = []
        self.fine_selection_count = 0

        # 辅助类实例
        self.financial_validator = FinancialValidator(self.config)

        super().__init__(self._select_coarse, self._select_fine)


    # ========== 公开方法 ==========
    def trigger_selection(self):
        """触发新一轮选股"""
        msg = f"触发第{self.fine_selection_count + 1}次选股 ({self.algorithm.Time.strftime('%Y-%m-%d')})"
        padding = (80 - len(msg)) // 2
        self.algorithm.Debug(f"{'='*padding}{msg}{'='*padding}")
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
            if x.HasFundamentalData                                 # 排除ETF等
            and x.Price > min_price                                 # 价格筛选
            and x.Volume > min_volume                               # 成交量筛选
            and x.SecurityReference.IPODate is not None
            and x.SecurityReference.IPODate <= min_ipo_date         # IPO时间
        ]

        return selected


    def _select_fine(self, fine: List[FineFundamental]) -> List[Symbol]:
        """
        精选阶段: 财务筛选
        流程: 财务筛选 -> 输出所有通过的股票

        注: v7.9.3移除波动率筛选(历史数据显示过滤<1%股票,成本高收益低)
        """
        # 如果未触发选股, 返回上次结果
        if not self.selection_on:
            return self.last_fine_selected_symbols

        # 重置选股标志
        self.selection_on = False
        self.fine_selection_count += 1

        fine = list(fine)

        # 财务筛选 (PE, ROE, 负债率, 杠杆率)
        financially_filtered, financial_stats = self._apply_financial_filters(fine)

        # 缓存结果（不分组，输出所有通过筛选的股票）
        self.last_fine_selected_symbols = [x.Symbol for x in financially_filtered]

        return self.last_fine_selected_symbols


    # ========== 筛选辅助方法 ==========
    def _apply_financial_filters(self, stocks: List[FineFundamental]) -> Tuple[List[FineFundamental], Dict[str, int]]:
        """
        应用财务筛选条件

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


