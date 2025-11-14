# region imports
from .RiskBaseRule import RiskRule
from typing import Tuple
# endregion


class PairHoldingTimeoutRule(RiskRule):
    """
    持仓超时风控规则 

    检测配对持仓时间是否超过阈值。配对交易是短期均值回归策略,
    如果持仓超过max_days仍未回归,说明协整关系可能失效,应止损退出。

    触发条件:
    - 持仓天数 > max_days (从pair.pair_opened_time到当前时间)
    - 移除get_action()方法
    - Rule只负责检测,RiskManager负责生成CloseIntent(reason='TIMEOUT')
    - cooldown由RiskManager在Intent执行后激活

    设计特点:
    - 无需冷却期: 订单锁机制(tickets_manager.is_pair_locked)已防止重复提交
    - 简单高效: 只需检查时间差,不涉及PnL计算
    - 优先级中等: priority=60,介于PairAnomaly(100)和PairDrawdown(50)之间

    配置示例:
    {
        'enabled': True,
        'priority': 60,
        'max_days': 30
    }

    使用示例:
    ```python
    # RiskManager中:
    triggered, desc = rule.check(pair)
    if triggered:
        intent = pair.get_close_intent(reason='TIMEOUT')  # RiskManager生成Intent
    ```
    """

    def __init__(self, algorithm, config: dict):
        """
        初始化持仓超时规则 (v7.11.0: 动态持有时间)

        Args:
            algorithm: QCAlgorithm实例
            config: 规则配置字典,必须包含'max_halflife_multiplier'字段
        """
        super().__init__(algorithm, config)
        self.max_halflife_multiplier = config['max_halflife_multiplier']  # v7.11.0: 动态倍数(默认2.0)


    def check(self, pair) -> Tuple[bool, str]:
        """
        检查配对是否触发持仓超时 (v7.11.0: 动态持有时间)

        检查流程:
        1. 检查规则是否启用
        2. 检查该配对是否在冷却期
        3. 动态计算该配对的最大持有时间 (half_life × multiplier)
        4. 调用pair.get_pair_holding_days()获取实际持仓天数
        5. 判断是否超过动态阈值

        Args:
            pair: Pairs对象,必须实现get_pair_holding_days()和half_life属性

        Returns:
            (is_triggered, description)
            - is_triggered: True表示超时,False表示未超时
            - description: 详细描述(包含持仓天数、动态阈值、半衰期、开仓时间)

        设计说明 (v7.11.0):
            - 动态持有时间 = pair.half_life × max_halflife_multiplier
            - 不同配对有不同的持有时间上限 (如8天配对→16天, 20天配对→40天)
            - 避免了固定阈值对慢速配对的过度惩罚
            - 与动态Half-life评分曲线配合,构建完整的自适应风控体系

        示例:
            # half_life=10天的配对
            triggered, desc = rule.check(pair=pair_obj)
            # 返回: (True, "持仓超时: 已持仓25天 > 上限20.0天 (半衰期10.0天 × 2.0, 开仓时间: 2024-01-01)")
        """
        # 1. 检查是否启用
        if not self.enabled:
            return False, ""

        # 2. 检查该配对是否在冷却期
        if self.is_in_cooldown(pair_id=pair.pair_id):
            return False, ""

        # 3. v7.13.0: 基于半衰期分布的动态超时公式
        # 公式: max_days = half_life_mean + 2 * half_life_std (覆盖95%置信区间)
        # 向后兼容: 如果half_life_std=0或不存在,退回到2.0x固定倍数
        if pair.half_life_std > 0:
            max_days = pair.half_life + 2 * pair.half_life_std
        else:
            max_days = pair.half_life * self.max_halflife_multiplier  # 兼容旧数据

        # 4. 获取实际持仓天数 (复用Pairs自带方法,避免时区问题)
        holding_days = pair.get_pair_holding_days()

        # 如果无法获取持仓天数(无持仓或未记录),不触发
        if holding_days is None:
            return False, ""

        # 5. 判断是否超过动态阈值
        if holding_days > max_days:
            # 获取开仓时间用于日志 (如果存在)
            entry_time = getattr(pair, 'pair_opened_time', None)
            entry_time_str = entry_time.strftime('%Y-%m-%d') if entry_time else "未知"

            description = (
                f"持仓超时: 已持仓{holding_days}天 > "
                f"上限{max_days:.1f}天 "
                f"(半衰期{pair.half_life:.1f}天 × {self.max_halflife_multiplier}, "
                f"开仓时间: {entry_time_str})"
            )
            return True, description

        return False, ""
