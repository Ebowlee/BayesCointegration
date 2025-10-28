# region imports
from abc import ABC, abstractmethod
from typing import Tuple
from datetime import timedelta
# endregion


class RiskRule(ABC):
    """
    风控规则抽象基类 

    所有具体风控规则都必须继承此类并实现check()方法

    设计原则:
    - 统一接口: 所有规则通过check()检测风险
    - 职责分离: 规则只负责检测,RiskManager负责生成Intent
    - Intent Pattern: Rule不再返回action字符串,由RiskManager统一生成CloseIntent
    - 可配置: 通过config字典控制行为
    - 优先级: 支持多规则按优先级排序
    - 冷却期: 避免规则频繁触发

    适用范围:
    - Portfolio层面风控 (爆仓、回撤、市场波动等)
    - Pair层面风控 (持仓超时、仓位异常、配对回撤等)
    - 移除get_action()抽象方法
    - Rule只负责检测(check),不生成Intent
    - Cooldown由RiskManager在Intent执行后激活
    """

    def __init__(self, algorithm, config: dict):
        """
        初始化风控规则

        Args:
            algorithm: QCAlgorithm实例，用于访问Portfolio、Time等
            config: 规则配置字典，包含enabled、priority、threshold等参数

        配置示例:
        {
            'enabled': True,        # 是否启用
            'priority': 100,        # 优先级(数字越大越先执行)
            'threshold': 0.25,      # 触发阈值
            'cooldown_days': 30     # 冷却期(天)
        }
        """
        self.algorithm = algorithm
        self.config = config
        self.enabled = config['enabled']
        self.priority = config['priority']

        # v7.1.2: 支持Portfolio和Pair两种cooldown模式
        self.cooldown_until = None    # Portfolio规则: 全局cooldown
        self.pair_cooldowns = {}      # Pair规则: per-pair cooldown {pair_id: cooldown_until}


    def __repr__(self):
        """便于调试的字符串表示"""
        cooldown_status = f"冷却至{self.cooldown_until}" if self.cooldown_until else "无冷却"
        return (
            f"<{self.__class__.__name__} "
            f"enabled={self.enabled} priority={self.priority} {cooldown_status}>"
        )


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
        2. (可选)检查 self.is_in_cooldown()，如果True直接返回(False, "")
           注意: RiskManager应保证不调用冷却期内的规则,此检查为Fail-Safe机制
        3. 执行具体检测逻辑
        4. 如果触发，返回(True, 详细描述)
        5. 如果未触发，返回(False, "")
        """
        pass


    def is_in_cooldown(self, pair_id=None) -> bool:
        """
        检查是否在冷却期

        冷却期机制:
        - 当规则触发后，会设置cooldown_until时间
        - 在此时间之前（包括到期当天），规则不会再次检测
        - 避免频繁触发同一规则，减少无意义的重复操作

        应用场景:
        - Portfolio规则: 爆仓(永久)、回撤(30天)、市场波动(14天)
        - Pair规则: 持仓超时(30天)、配对回撤(30天)、仓位异常(30天)

        Args:
            pair_id: 配对ID (仅Pair规则需要传入,Portfolio规则传None)

        Returns:
            True: 在冷却期，不应检测
            False: 不在冷却期，可以检测

        设计特点:
        - Portfolio规则 (pair_id=None): 检查全局cooldown_until
        - Pair规则 (pair_id不为None): 检查该配对的pair_cooldowns[pair_id]
        - 向后兼容: 默认pair_id=None,Portfolio规则无需修改

        注意:
        使用 <= 而非 < 确保冷却期到期当天仍保持冷却状态
        """
        if pair_id is None:
            # Portfolio规则: 检查全局cooldown
            if self.cooldown_until is None:
                return False
            return self.algorithm.Time <= self.cooldown_until
        else:
            # Pair规则: 检查per-pair cooldown
            if pair_id not in self.pair_cooldowns:
                return False
            return self.algorithm.Time <= self.pair_cooldowns[pair_id]


    def activate_cooldown(self, pair_id=None, days=None):
        """
        激活冷却期（v7.3.1: 支持动态days参数）

        调用时机(v7.1.0):
        - Intent执行成功后，由RiskManager调用
        - 不在check()检测时激活,而是在订单提交后激活
        - 设置冷却结束时间 = 当前时间 + cooldown_days

        冷却期长度建议:
        Portfolio层面:
        - 爆仓: 36500天 (永久停止交易)
        - 回撤: 30天 (等待市场恢复)
        - 市场波动: 14天 (观察市场稳定性)

        Pair层面:
        - 持仓超时: 30天 (避免该配对立即重开仓)
        - 配对回撤: 20/40天 (根据PnL状态动态决定 - v7.3.1)
        - 仓位异常: 30天 (异常配对暂时冻结)

        Args:
            pair_id: 配对ID (仅Pair规则需要传入,Portfolio规则传None)
            days: 冷却天数 (可选, v7.3.1新增)
                 - 如果提供: 使用此值
                 - 如果None: 从config['cooldown_days']读取
                 - 用于PairDrawdownRule根据PnL状态动态决定冷却期

        设计特点:
        - Portfolio规则 (pair_id=None): 设置全局cooldown_until
        - Pair规则 (pair_id不为None): 设置该配对的pair_cooldowns[pair_id]
        - 动态冷却期 (days不为None): 覆盖config配置值
        - 向后兼容: 默认pair_id=None, days=None, 现有调用无需修改

        特殊情况:
        - 如果days=None且config中没有'cooldown_days'字段，不设置冷却期
        - 如果cooldown_days=0或None，不设置冷却期
        """
        # v7.3.1: 优先使用传入的days参数,否则从config读取
        if days is None:
            if 'cooldown_days' not in self.config:
                return
            days = self.config['cooldown_days']

        if days is None or days <= 0:
            return

        cooldown_end = self.algorithm.Time + timedelta(days=days)

        if pair_id is None:
            # Portfolio规则: 设置全局cooldown
            self.cooldown_until = cooldown_end
        else:
            # Pair规则: 设置per-pair cooldown
            self.pair_cooldowns[pair_id] = cooldown_end
