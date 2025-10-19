# region imports
from AlgorithmImports import *
from dataclasses import dataclass
import numpy as np
from typing import Dict
# endregion


@dataclass(frozen=True)
class PairData:
    """
    配对价格数据值对象

    封装配对的原始价格和对数价格，提供类型安全的数据访问。
    使用 @dataclass(frozen=True) 确保对象创建后不可变。

    属性:
        symbol1: 第一只股票代码
        symbol2: 第二只股票代码
        prices1: 第一只股票原始价格序列
        prices2: 第二只股票原始价格序列
        log_prices1: 第一只股票对数价格序列 (np.log(prices1))
        log_prices2: 第二只股票对数价格序列 (np.log(prices2))

    示例:
        >>> pair_data = PairData(
        ...     symbol1=SPY,
        ...     symbol2=QQQ,
        ...     prices1=np.array([100.0, 101.0, 102.0]),
        ...     prices2=np.array([200.0, 202.0, 204.0]),
        ...     log_prices1=np.log(np.array([100.0, 101.0, 102.0])),
        ...     log_prices2=np.log(np.array([200.0, 202.0, 204.0]))
        ... )
        >>> print(pair_data.symbol1)
        SPY
        >>> print(len(pair_data.prices1))
        3

    设计理念:
        - 不可变性: frozen=True 防止意外修改
        - 类型安全: 明确区分 prices 和 log_prices
        - 单一职责: 仅负责数据存储，不包含业务逻辑
    """
    symbol1: Symbol
    symbol2: Symbol
    prices1: np.ndarray
    prices2: np.ndarray
    log_prices1: np.ndarray
    log_prices2: np.ndarray
