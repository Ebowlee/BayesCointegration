"""
trade模块 - 交易分析

提供:
- TradeAnalyzer: 主协调器
- TradeSnapshot: Value Object (可选)
"""

from .TradeAnalyzer import TradeAnalyzer
from .TradeSnapshot import TradeSnapshot

__all__ = ['TradeAnalyzer', 'TradeSnapshot']
