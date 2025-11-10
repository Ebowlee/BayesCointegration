"""
黑名单模块 (v7.7.0 重构)

核心功能:
- 识别历史表现差的配对
- 提供黑名单过滤给 PairSelector
- 防止重复亏损

设计原则:
- 面向对象: 交易历史存储在 Pairs 对象中
- 无状态: BlacklistManager 不存储数据,只提供判断逻辑
- 配置化: 所有阈值从 config.trade_analysis 读取
"""

from .BlacklistManager import BlacklistManager

__all__ = ['BlacklistManager']
