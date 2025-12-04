"""
OrderExecutor - 订单执行模块 (v7.98.3)

包含:
- OpenIntent: 开仓意图数据类
- CloseIntent: 平仓意图数据类
- OrderExecutor: 订单执行服务

设计理念:
- Intent Pattern: 将"意图"与"执行"分离
- 单一职责: Intent 只存数据, Executor 只执行订单
- 无状态: Executor 不存储订单信息 (由 TicketsManager 管理)
"""

from dataclasses import dataclass
from AlgorithmImports import *
from typing import Tuple


# ============================================================================
# 第一部分: 意图数据类 (Value Objects)
# ============================================================================

@dataclass
class OpenIntent:
    """
    开仓意图数据类

    封装开仓所需的所有信息,将"意图"与"执行"分离。

    属性:
        pair_id: 配对标识符 (symbol1, symbol2)
        symbol1: 第一只股票的Symbol对象
        symbol2: 第二只股票的Symbol对象
        qty1: 第一只股票的目标数量(正数=做多,负数=做空)
        qty2: 第二只股票的目标数量(正数=做多,负数=做空)
        signal: 交易信号类型 (LONG_SPREAD 或 SHORT_SPREAD)
        tag: 订单标签,用于追踪和分析

    使用场景:
        # Pairs生成意图
        intent = pair.get_open_intent(margin_allocated, data)

        # OrderExecutor执行意图
        success = order_executor.execute_open(intent)
    """
    pair_id: Tuple[str, str]
    symbol1: Symbol
    symbol2: Symbol
    qty1: int
    qty2: int
    signal: str
    tag: str


@dataclass
class CloseIntent:
    """
    平仓意图数据类

    封装平仓所需的所有信息,将"意图"与"执行"分离。

    属性:
        pair_id: 配对标识符 (symbol1, symbol2)
        symbol1: 第一只股票的Symbol对象
        symbol2: 第二只股票的Symbol对象
        qty1: 第一只股票的当前持仓数量(需要平仓的数量)
        qty2: 第二只股票的当前持仓数量(需要平仓的数量)
        reason: 平仓原因 (必须匹配config.pairs_manager.cooldown_multipliers中的key):
            - 'TRAILING_STOP': 移动止损 (2个半衰期) [v8.26.0 新增]
            - 'TIMEOUT': 持有超时 (2个半衰期)
            - 'DRAWDOWN': 回撤触发 (4个半衰期)
            - 'ANOMALY': 单腿异常 (永久冷却)
        tag: 订单标签,用于追踪和分析(包含reason信息)

    使用场景:
        # Pairs生成意图
        intent = pair.get_close_intent(reason='TRAILING_STOP')

        # OrderExecutor执行意图
        success = order_executor.execute_close(intent)
    """
    pair_id: Tuple[str, str]
    symbol1: Symbol
    symbol2: Symbol
    qty1: int
    qty2: int
    reason: str
    tag: str


# ============================================================================
# 第二部分: 订单执行服务
# ============================================================================

class OrderExecutor:
    """
    订单执行服务 (纯执行层, 无业务逻辑)

    设计理念:
    - 单一职责: 只负责将Intent转换为OrderTicket,不做任何业务判断
    - 无状态设计: 不存储任何配对或订单信息(状态由TicketsManager管理)
    - 依赖注入: 通过构造函数注入algorithm引用

    职责边界:
    ✅ 负责: Intent → MarketOrder 转换,返回成功/失败
    ❌ 不负责: 信号生成、资金管理、订单追踪、风控检查

    与其他模块的关系:
    - Pairs: 生成Intent对象(get_open_intent, get_close_intent)
    - OrderExecutor: 执行Intent对象(本类)
    - TicketsManager: 追踪OrderTicket状态
    """

    def __init__(self, algorithm, tickets_manager):
        """
        初始化订单执行器

        Args:
            algorithm: QCAlgorithm实例,用于调用MarketOrder方法
            tickets_manager: TicketsManager实例,用于自动注册订单
        """
        self.algorithm = algorithm
        self.tickets_manager = tickets_manager

    def execute_open(self, intent: OpenIntent) -> bool:
        """
        执行开仓意图

        将OpenIntent转换为两个MarketOrder订单(配对的两条腿),并自动注册到TicketsManager。

        Args:
            intent: OpenIntent对象,包含配对ID、Symbol、数量、信号和标签

        Returns:
            bool: True=订单已提交并注册, False=订单提交失败

        执行流程:
            1. 提交symbol1订单 → ticket1
            2. 提交symbol2订单 → ticket2
            3. 检查两条腿是否都成功 → 自动注册到TicketsManager并返回True
        """
        # 提交两条腿的市价订单
        ticket1 = self.algorithm.MarketOrder(intent.symbol1, intent.qty1, tag=intent.tag)
        ticket2 = self.algorithm.MarketOrder(intent.symbol2, intent.qty2, tag=intent.tag)

        # 检查订单是否成功提交
        if ticket1 and ticket2:
            # 自动注册到TicketsManager
            tickets = [ticket1, ticket2]
            self.tickets_manager.register_tickets(intent.pair_id, tickets, 'OPEN')
            return True

        return False

    def execute_close(self, intent: CloseIntent) -> bool:
        """
        执行平仓意图

        将CloseIntent转换为MarketOrder订单,平掉所有持仓,并自动注册到TicketsManager。

        Args:
            intent: CloseIntent对象,包含配对ID、Symbol、当前持仓数量、原因和标签

        Returns:
            bool: True=订单已提交并注册, False=无订单提交(无持仓)

        执行流程:
            1. 检查qty1是否非0 → 提交平仓订单
            2. 检查qty2是否非0 → 提交平仓订单
            3. 如果有订单提交,自动注册到TicketsManager并返回True

        容错处理:
            - 如果两条腿都是0(无持仓),返回False
            - 单边持仓也能正常处理(只平掉有持仓的腿)
        """
        tickets = []

        # 平掉第一条腿(如果有持仓)
        if intent.qty1 != 0:
            ticket1 = self.algorithm.MarketOrder(intent.symbol1, -intent.qty1, tag=intent.tag)
            if ticket1:
                tickets.append(ticket1)

        # 平掉第二条腿(如果有持仓)
        if intent.qty2 != 0:
            ticket2 = self.algorithm.MarketOrder(intent.symbol2, -intent.qty2, tag=intent.tag)
            if ticket2:
                tickets.append(ticket2)

        # 如果有订单提交,自动注册到TicketsManager
        if tickets:
            self.tickets_manager.register_tickets(
                intent.pair_id, tickets, 'CLOSE', reason=intent.reason
            )
            return True

        return False
