"""
模拟 QuantConnect 的 AlgorithmImports
用于测试环境，避免依赖真实的 QuantConnect 环境
"""
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, List, Tuple, Set, Dict, Any

# 导出所有需要的类和类型
from .mock_quantconnect import (
    OrderStatus,
    MockSymbol as Symbol,
    MockSecurityIdentifier as SecurityIdentifier,
    MockHolding as Holding,
    MockPortfolio as Portfolio,
    MockOrder as Order,
    MockOrderEvent as OrderEvent,
    MockTransactions as Transactions,
    MockAlgorithm as QCAlgorithm,
    MockInsight as Insight,
    MockPortfolioTarget as PortfolioTarget
)

# 导出 OrderStatus 枚举值，使其可以直接访问
globals().update({status.name: status for status in OrderStatus})

# 模拟其他可能需要的类
class RiskManagementModel:
    """模拟风险管理模型基类"""
    pass

class AlphaModel:
    """模拟 Alpha 模型基类"""
    pass

class UniverseSelectionModel:
    """模拟宇宙选择模型基类"""
    pass

class PortfolioConstructionModel:
    """模拟投资组合构建模型基类"""
    pass

class ExecutionModel:
    """模拟执行模型基类"""
    pass

# 为了兼容性，创建一个通配符导入列表
__all__ = [
    'Symbol', 'SecurityIdentifier', 'Holding', 'Portfolio', 
    'Order', 'OrderEvent', 'OrderStatus', 'Transactions',
    'QCAlgorithm', 'Insight', 'PortfolioTarget',
    'RiskManagementModel', 'AlphaModel', 'UniverseSelectionModel',
    'PortfolioConstructionModel', 'ExecutionModel',
    'datetime', 'timedelta', 'Optional', 'List', 'Tuple', 'Set', 'Dict', 'Any'
]

# 添加 OrderStatus 枚举值到导出列表
__all__.extend([status.name for status in OrderStatus])