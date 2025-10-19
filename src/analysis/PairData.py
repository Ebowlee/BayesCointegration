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

    def __post_init__(self):
        """
        数据验证（轻量级，仅检查配对一致性）

        验证逻辑:
        - DataProcessor已保证单个股票质量（252天，价格>0，无NaN）
        - 此处仅验证配对级别的一致性

        验证规则:
        1. 配对数据长度必须一致（防止数据提取时的索引错误）
        2. 对数价格长度必须一致（防止工厂方法实现错误）

        Raises:
            ValueError: 如果验证失败

        设计理念:
        - 适度防御：只验证DataProcessor无法验证的配对级别约束
        - 避免重复：不验证DataProcessor已保证的约束（价格>0，长度=252）
        - 早期发现：在对象创建时就捕获数据问题，而非等到MCMC采样时崩溃
        """
        # 验证1：配对数据长度一致（DataProcessor无法验证的配对级别约束）
        if len(self.prices1) != len(self.prices2):
            raise ValueError(
                f"价格序列长度不一致: "
                f"{self.symbol1.Value}={len(self.prices1)}, "
                f"{self.symbol2.Value}={len(self.prices2)}"
            )

        # 验证2：对数转换正确性（防止工厂方法bug）
        if len(self.log_prices1) != len(self.prices1) or len(self.log_prices2) != len(self.prices2):
            raise ValueError(
                f"对数价格长度错误: "
                f"{self.symbol1.Value} log_prices={len(self.log_prices1)} vs prices={len(self.prices1)}, "
                f"{self.symbol2.Value} log_prices={len(self.log_prices2)} vs prices={len(self.prices2)}"
            )

    @classmethod
    def from_clean_data(cls, pair_info: Dict, clean_data: Dict) -> 'PairData':
        """
        工厂方法：从 clean_data 自动提取和转换价格数据

        封装数据提取和对数转换逻辑，提供简洁的对象创建接口。

        Args:
            pair_info: 配对信息字典（必须包含 'symbol1' 和 'symbol2'）
                格式: {'symbol1': Symbol, 'symbol2': Symbol, ...}
            clean_data: 清洗后的价格数据字典
                格式: {Symbol: DataFrame with 'close' column}

        Returns:
            PairData: 包含原始价格和对数价格的不可变对象

        Raises:
            KeyError: 如果 symbol 不在 clean_data 中
            ValueError: 如果数据验证失败（由 __post_init__ 触发）

        示例:
            >>> pair_info = {'symbol1': SPY, 'symbol2': QQQ, ...}
            >>> clean_data = {SPY: DataFrame(...), QQQ: DataFrame(...)}
            >>> pair_data = PairData.from_clean_data(pair_info, clean_data)
            >>> # 自动完成数据提取和对数转换

        设计理念:
        - 封装实现细节：使用者无需知道如何从DataFrame提取数据
        - 一次性转换：对数转换只执行一次，提升性能
        - 自动验证：创建时自动触发 __post_init__ 验证
        """
        # 提取股票代码
        symbol1 = pair_info['symbol1']
        symbol2 = pair_info['symbol2']

        # 从 clean_data 提取价格序列（DataFrame → np.ndarray）
        try:
            prices1 = clean_data[symbol1]['close'].values
            prices2 = clean_data[symbol2]['close'].values
        except KeyError as e:
            raise KeyError(
                f"clean_data 中缺少股票数据: {e}. "
                f"可用股票: {list(clean_data.keys())}"
            )

        # 执行对数转换（只计算一次！）
        log_prices1 = np.log(prices1)
        log_prices2 = np.log(prices2)

        # 调用构造函数（会自动触发 __post_init__ 验证）
        return cls(
            symbol1=symbol1,
            symbol2=symbol2,
            prices1=prices1,
            prices2=prices2,
            log_prices1=log_prices1,
            log_prices2=log_prices2
        )
