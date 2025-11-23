"""
Execution 模块 - 交易执行层

职责:
    - OrderIntent: 交易意图值对象(数据载体)
    - OrderExecutor: 订单执行引擎(意图→订单)
    - ExecutionManager: 执行协调器(信号聚合→意图生成→订单执行→票据管理)

架构层次:
    业务逻辑层(Pairs) → OrderIntent → OrderExecutor → QuantConnect API
                                    ↓
                            ExecutionManager(协调)
                                    ↓
                            TicketsManager(跟踪)
"""

from .OrderIntent import OpenIntent, CloseIntent
from .OrderExecutor import OrderExecutor
from .ExecutionManager import ExecutionManager

__all__ = [
    'OpenIntent',
    'CloseIntent',
    'OrderExecutor',
    'ExecutionManager'
]
