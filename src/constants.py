"""
共享常量定义

本模块集中管理策略中使用的所有常量类,避免循环导入问题。

常量类:
    - TradingSignal: 交易信号常量
    - PositionMode: 持仓模式常量
    - OrderAction: 订单动作常量

设计原则:
    - 单一职责: 只定义常量,不包含任何业务逻辑
    - 零依赖: 不导入任何其他业务模块,避免循环依赖
    - 集中管理: 所有模块从此统一源头导入常量
"""


class TradingSignal:
    """交易信号常量"""
    LONG_SPREAD = 'LONG_SPREAD'     # 做多价差(买入symbol1,卖出symbol2)
    SHORT_SPREAD = 'SHORT_SPREAD'   # 做空价差(卖出symbol1,买入symbol2)
    CLOSE = 'CLOSE'                 # 平仓信号
    STOP_LOSS = 'STOP_LOSS'         # 止损信号
    HOLD = 'HOLD'                   # 持有信号
    WAIT = 'WAIT'                   # 等待信号
    COOLDOWN = 'COOLDOWN'           # 冷却期
    NO_DATA = 'NO_DATA'             # 无数据


class PositionMode:
    """持仓模式常量(整合状态+方向)"""
    NONE = 'NONE'                    # 无持仓
    LONG_SPREAD = 'LONG_SPREAD'      # 正常做多价差 (qty1>0, qty2<0)
    SHORT_SPREAD = 'SHORT_SPREAD'    # 正常做空价差 (qty1<0, qty2>0)
    PARTIAL_LEG1 = 'PARTIAL_LEG1'    # 只有第一腿
    PARTIAL_LEG2 = 'PARTIAL_LEG2'    # 只有第二腿
    ANOMALY_SAME = 'ANOMALY_SAME'    # 异常:同向持仓


class OrderAction:
    """订单动作常量"""
    OPEN = 'OPEN'
    CLOSE = 'CLOSE'
