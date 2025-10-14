"""
Mock QuantConnect对象

目的:
模拟QuantConnect的核心类,使得测试可以在不依赖LEAN引擎的情况下运行

设计原则:
1. 只实现TicketsManager需要的最小接口
2. 可以手动设置状态(如OrderStatus.Canceled)
3. 完全独立于AlgorithmImports
"""

from datetime import datetime
from enum import IntEnum


# ========== Mock枚举类型 ==========

class MockOrderStatus(IntEnum):
    """模拟QuantConnect的OrderStatus枚举"""
    New = 0
    Submitted = 1
    PartiallyFilled = 2
    Filled = 3
    Canceled = 4
    None_ = 5
    Invalid = 6
    CancelPending = 7
    UpdateSubmitted = 8


# ========== Mock核心类 ==========

class MockSymbol:
    """模拟QuantConnect的Symbol类"""
    def __init__(self, ticker: str):
        self.Value = ticker
        self.ID = f"{ticker}_MOCK_ID"

    def __str__(self):
        return self.Value

    def __repr__(self):
        return f"MockSymbol({self.Value})"

    def __eq__(self, other):
        if isinstance(other, MockSymbol):
            return self.Value == other.Value
        return False

    def __hash__(self):
        return hash(self.Value)


class MockOrderTicket:
    """
    模拟QuantConnect的OrderTicket类

    关键特性:
    - 可以手动设置Status(用于模拟异常)
    - 支持QuantityFilled(用于验证回调)
    - 包含Time属性(用于测试成交时间)
    """
    def __init__(self, order_id: int, symbol: MockSymbol, status: MockOrderStatus):
        self.OrderId = order_id
        self.Symbol = symbol
        self.Status = status
        self.Time = datetime.now()
        self.QuantityFilled = 0  # 默认0,Filled时设置实际数量
        self.Quantity = 0  # 订单数量

    def __repr__(self):
        return (f"MockOrderTicket(OrderId={self.OrderId}, "
                f"Symbol={self.Symbol}, Status={self.Status})")


class MockOrderEvent:
    """
    模拟QuantConnect的OrderEvent类

    OnOrderEvent回调接收的事件对象
    """
    def __init__(self, order_id: int, status: MockOrderStatus):
        self.OrderId = order_id
        self.Status = status
        self.FillQuantity = 0
        self.FillPrice = 0

    def __repr__(self):
        return f"MockOrderEvent(OrderId={self.OrderId}, Status={self.Status})"


class MockAlgorithm:
    """
    模拟QuantConnect的QCAlgorithm类

    最小化实现:
    - Debug(): 打印日志(用于测试观察)
    - Portfolio: 提供简化的持仓查询接口
    """
    def __init__(self):
        self.debug_messages = []  # 记录所有Debug消息,便于测试验证
        self.Portfolio = MockSecurityPortfolioManager()

    def Debug(self, message: str):
        """记录Debug消息(测试环境不会真的打印到QuantConnect日志)"""
        self.debug_messages.append(message)
        print(f"[MOCK_DEBUG] {message}")

    def get_debug_messages(self):
        """获取所有Debug消息(用于测试断言)"""
        return self.debug_messages


class MockSecurityPortfolioManager:
    """模拟Portfolio.Securities字典"""
    def __init__(self):
        self._holdings = {}

    def __getitem__(self, symbol: MockSymbol):
        if symbol not in self._holdings:
            self._holdings[symbol] = MockSecurityHolding()
        return self._holdings[symbol]


class MockSecurityHolding:
    """模拟SecurityHolding,提供Invested属性"""
    def __init__(self):
        self.Invested = False
        self.Quantity = 0


class MockPairsManager:
    """
    模拟PairsManager类

    最小化实现:
    - get_pair_by_id(): 返回Mock的Pairs对象
    - 用于测试TicketsManager的回调机制
    """
    def __init__(self):
        self._pairs = {}

    def add_pair(self, pair_id: str, pairs_obj):
        """添加配对(测试准备阶段)"""
        self._pairs[pair_id] = pairs_obj

    def get_pair_by_id(self, pair_id: str):
        """获取配对对象"""
        return self._pairs.get(pair_id)


class MockPairs:
    """
    模拟Pairs类

    目的:
    验证TicketsManager的回调机制
    - on_position_filled()是否被正确调用
    - 参数传递是否正确
    """
    def __init__(self, symbol1: MockSymbol, symbol2: MockSymbol):
        self.symbol1 = symbol1
        self.symbol2 = symbol2
        self.position_opened_time = None
        self.position_closed_time = None
        self.tracked_qty1 = 0
        self.tracked_qty2 = 0
        self.callback_called = False  # 测试用标志
        self.callback_action = None

    def on_position_filled(self, action: str, fill_time, tickets):
        """
        回调方法(被TicketsManager调用)

        测试验证点:
        - 是否被调用(callback_called=True)
        - 参数是否正确(action, fill_time)
        - tickets是否包含正确的OrderTicket
        """
        self.callback_called = True
        self.callback_action = action

        if action == "OPEN":
            self.position_opened_time = fill_time
            # 从tickets提取成交数量
            for ticket in tickets:
                if ticket is not None and ticket.Status == MockOrderStatus.Filled:
                    if ticket.Symbol == self.symbol1:
                        self.tracked_qty1 = ticket.QuantityFilled
                    elif ticket.Symbol == self.symbol2:
                        self.tracked_qty2 = ticket.QuantityFilled
        elif action == "CLOSE":
            self.position_closed_time = fill_time
            self.tracked_qty1 = 0
            self.tracked_qty2 = 0
