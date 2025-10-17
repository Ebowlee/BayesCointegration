# region imports
from abc import ABC, abstractmethod
from typing import Tuple
from datetime import timedelta
# endregion


class RiskRule(ABC):
    """
    风控规则抽象基类

    所有具体风控规则都必须继承此类并实现抽象方法

    设计原则:
    - 统一接口: 所有规则通过check()检测风险
    - 职责分离: 规则只负责检测，不执行动作
    - 可配置: 通过config字典控制行为
    - 优先级: 支持多规则按优先级排序
    - 冷却期: 避免规则频繁触发

    适用范围:
    - Portfolio层面风控 (爆仓、回撤、市场波动等)
    - Pair层面风控 (持仓超时、仓位异常、配对回撤等)
    """

    def __init__(self, algorithm, config: dict):
        """
        初始化风控规则

        Args:
            algorithm: QCAlgorithm实例，用于访问Portfolio、Time等
            config: 规则配置字典，包含enabled、priority、threshold等参数

        配置示例:
        {
            'enabled': True,                     # 是否启用
            'priority': 100,                     # 优先级(数字越大越先执行)
            'threshold': 0.25,                   # 触发阈值
            'cooldown_days': 30,                 # 冷却期(天)
            'action': 'portfolio_liquidate_all'  # 响应动作
        }
        """
        self.algorithm = algorithm
        self.config = config
        self.enabled = config.get('enabled', True)
        self.priority = config.get('priority', 50)
        self.cooldown_until = None


    @abstractmethod
    def check(self, **kwargs) -> Tuple[bool, str]:
        """
        检测风险（抽象方法，子类必须实现）

        Args:
            **kwargs: 可选参数
                - pair: Pairs对象（Pair级规则需要传入）

        Returns:
            (是否触发, 风险描述)

        示例:
            # Portfolio规则调用
            triggered, desc = rule.check()
            # 返回: (True, "爆仓线触发: 亏损30.5%")

            # Pair规则调用
            triggered, desc = rule.check(pair=pair_obj)
            # 返回: (True, "持仓超时: 已持仓35天，上限30天")

        实现要点:
        1. 先检查 self.enabled，如果False直接返回(False, "")
        2. 再检查 self.is_in_cooldown()，如果True直接返回(False, "")
        3. 执行具体检测逻辑
        4. 如果触发，返回(True, 详细描述)
        5. 如果未触发，返回(False, "")
        """
        pass


    @abstractmethod
    def get_action(self) -> str:
        """
        获取响应动作类型（抽象方法，子类必须实现）

        Returns:
            动作类型字符串，格式: {scope}_{action}_{target}

        命名规范:
        - Portfolio层面: portfolio_{action}_{target}
        - Pair层面: pair_{action}

        Portfolio层面动作:
        - 'portfolio_liquidate_all': 全部清仓
        - 'portfolio_stop_new_entries': 停止开新仓
        - 'portfolio_reduce_exposure_50': 减仓50%
        - 'portfolio_rebalance_sectors': 行业再平衡

        Pair层面动作:
        - 'pair_close': 平仓单个配对
        - 'pair_liquidate': 强制清算（异常仓位）

        实现方式:
        通常直接返回配置中的'action'字段:
            return self.config['action']

        也可以根据条件动态返回:
            if self.config['threshold'] > 0.3:
                return 'portfolio_liquidate_all'
            else:
                return 'portfolio_stop_new_entries'
        """
        pass


    def is_in_cooldown(self) -> bool:
        """
        检查是否在冷却期

        冷却期机制:
        - 当规则触发后，会设置cooldown_until时间
        - 在此时间之前（包括到期当天），规则不会再次检测
        - 避免频繁触发同一规则，减少无意义的重复操作

        应用场景:
        - 爆仓规则触发后，永久冷却（cooldown_days=36500）
        - 回撤规则触发后，冷却30天，等待市场恢复
        - 配对回撤触发后，冷却15-30天，避免立即重开仓

        Returns:
            True: 在冷却期，不应检测
            False: 不在冷却期，可以检测

        注意:
        使用 <= 而非 < 确保冷却期到期当天仍保持冷却状态
        """
        if self.cooldown_until is None:
            return False

        return self.algorithm.Time <= self.cooldown_until


    def activate_cooldown(self):
        """
        激活冷却期

        调用时机:
        - 规则触发后，由RiskManager调用
        - 设置冷却结束时间 = 当前时间 + cooldown_days

        冷却期长度建议:
        Portfolio层面:
        - 爆仓: 36500天 (永久停止交易)
        - 回撤: 30天 (等待市场恢复)
        - 市场波动: 14天 (观察市场稳定性)

        Pair层面:
        - 持仓超时: 15-30天 (避免立即重开仓)
        - 配对回撤: 30天 (止损后充分冷却)
        - 仓位异常: 1天 (快速重试)

        特殊情况:
        - 如果配置中没有'cooldown_days'字段，不设置冷却期
        - 如果cooldown_days=0或None，不设置冷却期
        """
        if 'cooldown_days' in self.config:
            days = self.config['cooldown_days']
            if days is not None and days > 0:
                self.cooldown_until = self.algorithm.Time + timedelta(days=days)
