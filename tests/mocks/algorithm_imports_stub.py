"""
AlgorithmImports桩模块

目的:
在测试环境中模拟QuantConnect的AlgorithmImports模块,
使得生产代码可以正常import

设计:
- 导出所有生产代码需要的类型和枚举
- 指向tests.mocks中的Mock实现
"""

from tests.mocks.mock_qc_objects import (
    MockOrderStatus as OrderStatus,
    MockOrderTicket as OrderTicket,
    MockOrderEvent as OrderEvent,
    MockSymbol as Symbol
)

__all__ = ['OrderStatus', 'OrderTicket', 'OrderEvent', 'Symbol']
