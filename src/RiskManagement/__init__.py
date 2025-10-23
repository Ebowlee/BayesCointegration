"""
风控管理模块

提供插件化的风控规则系统，支持：
- Portfolio层面风控（爆仓、回撤等）
- Pair层面风控（持仓超时、仓位异常、配对回撤等）
- 市场条件检查（波动率等，用于开仓决策）
- 灵活的规则配置和优先级管理
- 统一的冷却期机制
"""

from .PortfolioBaseRule import RiskRule
from .PortfolioAccountBlowup import AccountBlowupRule
from .PortfolioDrawdown import ExcessiveDrawdownRule
from .MarketCondition import MarketCondition
from .HoldingTimeoutRule import HoldingTimeoutRule
from .PositionAnomalyRule import PositionAnomalyRule
from .PairDrawdownRule import PairDrawdownRule
from .RiskManager import RiskManager

__all__ = [
    'RiskRule',
    'AccountBlowupRule',
    'ExcessiveDrawdownRule',
    'MarketCondition',
    'HoldingTimeoutRule',
    'PositionAnomalyRule',
    'PairDrawdownRule',
    'RiskManager'
]
