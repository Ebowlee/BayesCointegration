# region imports
from AlgorithmImports import *
from .OrderIntent import OpenIntent, CloseIntent
from src.constants import OrderAction
# endregion


class OrderExecutor:
    """
    订单执行服务(纯执行层,无业务逻辑)

    设计理念:
    - 单一职责: 只负责将Intent转换为OrderTicket,不做任何业务判断
    - 无状态设计: 不存储任何配对或订单信息(状态由TicketsManager管理)
    - 依赖注入: 通过构造函数注入algorithm引用
    - 可测试性: 可以轻松mock algorithm进行单元测试

    职责边界:
    ✅ 负责: Intent → MarketOrder 转换,返回OrderTicket列表
    ❌ 不负责: 信号生成、资金管理、订单追踪、风控检查

    与其他模块的关系:
    - Pairs: 生成Intent对象(get_open_intent, get_close_intent)
    - OrderExecutor: 执行Intent对象(本类)
    - TicketsManager: 追踪OrderTicket状态
    - ExecutionManager: 协调整个执行流程

    使用示例:
        # 初始化(在main.py中)
        order_executor = OrderExecutor(self, self.tickets_manager)

        # 开仓流程(自动注册)
        intent = pair.get_open_intent(margin_allocated, data)
        if intent:
            order_executor.execute_open(intent)  # 自动注册,无返回值

        # 平仓流程(自动注册)
        intent = pair.get_close_intent(reason='STOP_LOSS')
        if intent:
            order_executor.execute_close(intent)  # 自动注册,无返回值
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

        设计说明:
            - 使用intent.tag统一标记两条腿(便于追踪和分析)
            - 数量由Intent预先计算(正数=做多,负数=做空)
            - 自动注册到TicketsManager进行订单追踪
            - 返回bool表示是否成功提交订单(最终成交由OnOrderEvent异步确定)

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
            self.tickets_manager.register_tickets(intent.pair_id, tickets, OrderAction.OPEN)
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

        设计说明:
            - 只平掉有持仓的腿(qty != 0)
            - 平仓数量 = -当前持仓数量(反向操作)
            - 使用intent.tag标记(包含reason信息)
            - 自动注册到TicketsManager进行订单追踪
            - 返回bool表示是否成功提交订单(最终成交由OnOrderEvent异步确定)

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
            self.tickets_manager.register_tickets(intent.pair_id, tickets, OrderAction.CLOSE)
            return True

        return False
