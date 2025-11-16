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
            config: universe_selection配置对象 (UniverseConfig dataclass)
        """
        self.config = config
        self.filters = config.financial_filters 


    def validate_stock(self, stock: FineFundamental) -> Tuple[bool, List[str]]:
        """
        验证单只股票的财务指标

        Args:
            stock: 股票基本面数据

        Returns:
            (是否通过, 失败原因列表)

        v7.29.0: 支持OR逻辑的估值筛选
        """
        # 基础数据检查
        if not stock.ValuationRatios or not stock.OperationRatios:
            return False, ['data_missing']

        fail_reasons = []

        # 遍历所有启用的筛选器
        for filter_name, filter_config in self.filters.items():
            if not filter_config.get('enabled', True):
                continue

            # v7.29.0: 检查是否为OR逻辑筛选器
            if filter_config.get('type') == 'or':
                if not self._validate_or_rules(stock, filter_config):
                    fail_reasons.append(filter_config['fail_key'])
                continue  # OR逻辑单独处理,跳过常规流程

            # 常规AND逻辑筛选(保持原有逻辑)
            # 获取指标值
            value = self._get_metric_value(stock, filter_config['path'])
            if value is None:
                fail_reasons.append(filter_config['fail_key'])
                continue

            # 获取阈值
            threshold = filter_config['threshold']

            # 比较操作 (v7.29.1: 支持le/ge包含边界操作符)
            operator = filter_config['operator']
            if operator == 'lt' and value >= threshold:
                fail_reasons.append(filter_config['fail_key'])
            elif operator == 'gt' and value <= threshold:
                fail_reasons.append(filter_config['fail_key'])
            elif operator == 'le' and value > threshold:  # v7.29.1新增: ≤
                fail_reasons.append(filter_config['fail_key'])
            elif operator == 'ge' and value < threshold:  # v7.29.1新增: ≥
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


    def _validate_or_rules(self, stock: FineFundamental, filter_config: dict) -> bool:
        """
        验证OR逻辑规则(任意一条通过即可) - v7.29.0

        Args:
            stock: 股票基本面数据
            filter_config: OR筛选器配置(包含rules数组)

        Returns:
            是否通过(至少一条规则满足)

        示例: (PE≤100 OR PS≤10)
            - PE=50, PS=null → ✅ 通过(PE满足)
            - PE=null, PS=5 → ✅ 通过(PS满足)
            - PE=120, PS=15 → ❌ 不通过(都不满足)
            - PE=null, PS=null → ❌ 不通过(都无效)

        技术细节:
            - QuantConnect返回null而非负数表示无效指标
            - PE=null: 亏损公司(EPS<0或极端值)
            - PS=null: 无收入公司(Sales≤0)
        """
        rules = filter_config.get('rules', [])

        for rule in rules:
            # 获取指标值
            value = self._get_metric_value(stock, rule['path'])
            if value is None:
                continue  # 该指标无效,检查下一条规则

            # 检查是否满足阈值
            threshold = rule['threshold']
            operator = rule['operator']

            if operator == 'le' and value <= threshold:
                return True  # 该条规则通过,整个OR逻辑通过
            elif operator == 'ge' and value >= threshold:
                return True

        # 所有规则都未通过
        return False



class SectorBasedUniverseSelection(FineFundamentalUniverseSelectionModel):
    """
    贝叶斯协整策略的股票选择模型

    两阶段筛选：
    1. 粗选: 价格、成交量、IPO时间筛选
    2. 精选: 财务指标筛选 (PE/PS估值OR逻辑, 负债率, 杠杆率)

    注: 波动率筛选已移除(历史数据证明过滤效果<1%,成本高收益低)
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

        # v7.28.2: 打印资金状态(可投资金/已占用资金)
        available_margin = self.algorithm.Portfolio.MarginRemaining
        total_margin_used = self.algorithm.Portfolio.TotalMarginUsed
        total_value = self.algorithm.Portfolio.TotalPortfolioValue
        self.algorithm.Debug(
            f"[资金状态] 可投资金: ${available_margin:,.2f} | "
            f"总价值: ${total_value:,.2f} | "
            f"已占用资金: ${total_margin_used:,.2f}"
        )

        self.selection_on = True


    # ========== 主要筛选方法 ==========
    def _select_coarse(self, coarse: List[CoarseFundamental]) -> List[Symbol]:
        """
        粗选阶段: 基础筛选 + 流动性排序
        筛选条件: 基本面数据、价格、市值、IPO时间

        v7.30.3变更:
        - 移除DollarVolume阈值筛选
        - 新增Volume排序+TOP N机制 (精确控制Fine阶段股票数量)

        v7.30.0变更:
        - 新增市值筛选 (MarketCap >= $1B)
        - 交易量筛选移至行业内部 (精选阶段TOP N)
        """
        # 如果未触发选股, 返回上次结果
        if not self.selection_on:
            return self.last_fine_selected_symbols

        coarse = list(coarse)  # 转换迭代器为列表

        # 预计算筛选阈值
        min_ipo_date = self.algorithm.Time - timedelta(days=self.config.min_days_since_ipo)
        min_price = self.config.min_price
        min_market_cap = self.config.min_market_cap
        max_coarse_stocks = self.config.max_coarse_stocks       # v7.30.3: TOP N

        # 步骤1: 基础筛选
        filtered = [
            x for x in coarse
            if x.HasFundamentalData                             # 排除ETF等
            and x.Price > min_price                             # 价格筛选
            and x.MarketCap >= min_market_cap                   # v7.30.0: 市值筛选
            and x.SecurityReference.IPODate is not None
            and x.SecurityReference.IPODate <= min_ipo_date     # IPO时间
        ]

        # 步骤2: 按Volume降序排序 + 取TOP N
        sorted_by_volume = sorted(filtered, key=lambda x: x.Volume, reverse=True)
        top_n = sorted_by_volume[:max_coarse_stocks]

        # 步骤3: 提取Symbol返回
        selected = [x.Symbol for x in top_n]

        return selected


    def _select_fine(self, fine: List[FineFundamental]) -> List[Symbol]:
        """
        精选阶段: 财务筛选
        流程: 财务筛选 -> 输出所有通过的股票

        v7.30.0变更说明:
        - 行业内交易量TOP 50筛选已移至CointegrationAnalyzer (架构优化)
        - 理由: 行业内筛选应在协整检验前进行,职责分离更清晰

        注: 波动率筛选已移除(历史数据显示过滤<1%股票,成本高收益低)
        """
        # 如果未触发选股, 返回上次结果
        if not self.selection_on:
            return self.last_fine_selected_symbols

        # 重置选股标志
        self.selection_on = False
        self.fine_selection_count += 1

        fine = list(fine)

        # 财务筛选 (PE/PS估值OR逻辑, 负债率, 杠杆率)
        financially_filtered, financial_stats = self._apply_financial_filters(fine)

        # 缓存结果 (不做行业分组,输出所有通过筛选的股票)
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

        # v7.29.2: 估值筛选详细统计(level=1)
        if 'valuation_failed' in stats and stats['valuation_failed'] > 0:
            fail_rate = (stats['valuation_failed'] / stats['total'] * 100) if stats['total'] > 0 else 0
            self.algorithm.Debug(
                f"[估值筛选] 输入{stats['total']}只 → "
                f"通过{stats['passed']}只 → "
                f"估值失败{stats['valuation_failed']}只 ({fail_rate:.1f}%)",
                level=1
            )

        return filtered_stocks, stats


