"""
Mock classes for QuantConnect framework components
用于模拟 QuantConnect 的核心类，使得单元测试可以独立运行
"""
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from enum import Enum


# 模拟 QuantConnect 的 OrderStatus
class OrderStatus(Enum):
    """订单状态枚举"""
    New = 0
    Submitted = 1
    PartiallyFilled = 2
    Filled = 3
    Canceled = 5
    Invalid = 7
    CancelPending = 8
    UpdateSubmitted = 9


class MockSymbol:
    """模拟股票代码"""
    def __init__(self, value: str):
        self.Value = value
        self.ID = MockSecurityIdentifier(value)
        
    def __eq__(self, other):
        return isinstance(other, MockSymbol) and self.Value == other.Value
        
    def __hash__(self):
        return hash(self.Value)
        
    def __str__(self):
        return self.Value
        
    def __repr__(self):
        return f"MockSymbol('{self.Value}')"


class MockSecurityIdentifier:
    """模拟证券标识符"""
    def __init__(self, symbol: str):
        self.Symbol = symbol
        self.Market = "USA"
        
    def __str__(self):
        return f"{self.Symbol} {self.Market}"


class MockHolding:
    """模拟持仓信息"""
    def __init__(self, symbol: MockSymbol = None, invested: bool = False, 
                 quantity: float = 0, average_price: float = 0, price: float = 100):
        self.Symbol = symbol
        self.Invested = invested
        self.Quantity = quantity
        self.AveragePrice = average_price
        self.Price = price
        self.HoldingsCost = abs(quantity * average_price)
        self.UnrealizedProfit = self._calculate_unrealized_profit()
        
    def _calculate_unrealized_profit(self) -> float:
        """计算未实现盈亏"""
        if self.Quantity == 0:
            return 0
        if self.Quantity > 0:  # 做多
            return self.Quantity * (self.Price - self.AveragePrice)
        else:  # 做空
            return -self.Quantity * (self.AveragePrice - self.Price)


class MockPortfolio(dict):
    """模拟投资组合"""
    def __init__(self):
        super().__init__()
        self.TotalPortfolioValue = 100000
        self.Cash = 100000
        
    def __getitem__(self, symbol: MockSymbol) -> MockHolding:
        """获取持仓，如果不存在则返回空持仓"""
        if symbol not in self:
            self[symbol] = MockHolding(symbol=symbol)
        return super().__getitem__(symbol)
        
    def items(self):
        """返回所有持仓"""
        return super().items()


class MockOrder:
    """模拟订单"""
    def __init__(self, order_id: int, symbol: MockSymbol, quantity: float, 
                 time: datetime, order_type: str = "Market"):
        self.OrderId = order_id
        self.Symbol = symbol
        self.Quantity = quantity
        self.Time = time
        self.Type = order_type
        self.Status = OrderStatus.New
        self.Tag = ""
        
    def __repr__(self):
        return f"MockOrder({self.OrderId}, {self.Symbol.Value}, {self.Quantity})"


class MockOrderEvent:
    """模拟订单事件"""
    def __init__(self, order_id: int, symbol: MockSymbol, status: OrderStatus, 
                 utc_time: datetime, fill_quantity: float = 0, fill_price: float = 0):
        self.OrderId = order_id
        self.Symbol = symbol
        self.Status = status
        self.UtcTime = utc_time
        self.FillQuantity = fill_quantity
        self.FillPrice = fill_price
        
    def __repr__(self):
        return f"MockOrderEvent({self.OrderId}, {self.Status.name})"


class MockTransactions:
    """模拟交易管理器"""
    def __init__(self):
        self.orders: Dict[int, MockOrder] = {}
        self._next_order_id = 1
        
    def GetOrderById(self, order_id: int) -> Optional[MockOrder]:
        """根据ID获取订单"""
        return self.orders.get(order_id)
        
    def CreateOrder(self, symbol: MockSymbol, quantity: float, 
                   time: datetime) -> MockOrder:
        """创建新订单"""
        order = MockOrder(self._next_order_id, symbol, quantity, time)
        self.orders[self._next_order_id] = order
        self._next_order_id += 1
        return order


class MockAlgorithm:
    """模拟算法实例"""
    def __init__(self, start_time: datetime = None):
        self.Time = start_time or datetime(2024, 8, 1)
        self.Portfolio = MockPortfolio()
        self.Transactions = MockTransactions()
        self.debug_messages = []
        self.logs = []
        
    def Debug(self, message: str):
        """记录调试消息"""
        self.debug_messages.append(f"[{self.Time}] {message}")
        
    def Log(self, message: str):
        """记录日志消息"""
        self.logs.append(f"[{self.Time}] {message}")
        
    def SetTime(self, new_time: datetime):
        """设置当前时间"""
        self.Time = new_time
        
    def AddDays(self, days: int):
        """增加天数"""
        self.Time += timedelta(days=days)


class MockInsight:
    """模拟交易信号"""
    def __init__(self, symbol: MockSymbol, period: timedelta, direction: str, 
                 magnitude: float = None, confidence: float = None, tag: str = ""):
        self.Symbol = symbol
        self.Period = period
        self.Direction = direction  # "Up", "Down", "Flat"
        self.Magnitude = magnitude
        self.Confidence = confidence
        self.Tag = tag
        self.GeneratedTimeUtc = datetime.utcnow()
        
    def __repr__(self):
        return f"MockInsight({self.Symbol.Value}, {self.Direction})"


class MockPortfolioTarget:
    """模拟投资组合目标"""
    def __init__(self, symbol: MockSymbol, quantity: float):
        self.Symbol = symbol
        self.Quantity = quantity
        
    def __repr__(self):
        return f"MockPortfolioTarget({self.Symbol.Value}, {self.Quantity})"


# 辅助函数
def create_filled_order_event(order: MockOrder, fill_time: datetime = None) -> MockOrderEvent:
    """创建已成交的订单事件"""
    return MockOrderEvent(
        order.OrderId,
        order.Symbol,
        OrderStatus.Filled,
        fill_time or order.Time,
        order.Quantity,
        100  # 默认成交价
    )


def create_submitted_order_event(order: MockOrder) -> MockOrderEvent:
    """创建已提交的订单事件"""
    return MockOrderEvent(
        order.OrderId,
        order.Symbol,
        OrderStatus.Submitted,
        order.Time,
        0,
        0
    )