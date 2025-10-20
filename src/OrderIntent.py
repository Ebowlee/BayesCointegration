# region imports
from dataclasses import dataclass
from AlgorithmImports import Symbol
from typing import Tuple
# endregion


@dataclass
class OpenIntent:
    """
    开仓意图数据类

    封装开仓所需的所有信息,将"意图"与"执行"分离。

    设计理念:
    - 值对象(Value Object): 不可变数据传递对象
    - 职责单一: 只负责数据存储,不包含任何业务逻辑
    - 与Pairs解耦: Pairs生成Intent,OrderExecutor执行Intent

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
        tickets = order_executor.execute_open(intent)
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

    设计理念:
    - 值对象(Value Object): 不可变数据传递对象
    - 职责单一: 只负责数据存储,不包含任何业务逻辑
    - 与Pairs解耦: Pairs生成Intent,OrderExecutor执行Intent

    属性:
        pair_id: 配对标识符 (symbol1, symbol2)
        symbol1: 第一只股票的Symbol对象
        symbol2: 第二只股票的Symbol对象
        qty1: 第一只股票的当前持仓数量(需要平仓的数量)
        qty2: 第二只股票的当前持仓数量(需要平仓的数量)
        reason: 平仓原因 ('CLOSE', 'STOP_LOSS', 'TIMEOUT', 'RISK_TRIGGER')
        tag: 订单标签,用于追踪和分析(包含reason信息)

    使用场景:
        # Pairs生成意图
        intent = pair.get_close_intent(reason='STOP_LOSS')

        # OrderExecutor执行意图
        tickets = order_executor.execute_close(intent)
    """
    pair_id: Tuple[str, str]
    symbol1: Symbol
    symbol2: Symbol
    qty1: int
    qty2: int
    reason: str
    tag: str
