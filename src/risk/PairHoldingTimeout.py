# region imports
from .RiskBaseRule import RiskRule
from typing import Tuple
# endregion


class PairHoldingTimeoutRule(RiskRule):
    """
    持仓超时风控规则

    检测配对持仓时间是否超过阈值且处于亏损状态。配对交易是短期均值回归策略,
    如果持仓超过max_days仍未回归且亏损,说明协整关系可能失效,应止损退出。

    触发条件 (v7.33.0 复合AND条件):
    - 持仓天数 > max_days (动态计算: half_life × max_halflife_multiplier)
    - **AND** (PnL <= 0 OR PnL is None)

    设计特点:
    - 支持per-pair冷却期: 默认15天
    - 动态持有时间: 基于半衰期分布的自适应公式 (half_life + 2*std 或 half_life × 2.0)
    - v7.33.0: PnL条件判断 - 保持盈利仓位,快速止损亏损仓位
      - 盈利配对 (pnl > 0): 不触发,继续持有
      - 亏损/持平配对 (pnl <= 0): 触发平仓
      - 数据异常 (pnl is None): 触发平仓 (Fail-Safe原则)
    - 优先级中等: priority=70

    配置示例:
    {
        'enabled': True,
        'priority': 70,
        'max_halflife_multiplier': 2.0,
        'cooldown_days': 15
    }

    使用示例:
    ```python
    # RiskManager中:
    triggered, desc = rule.check(pair)
    if triggered:
        intent = pair.get_close_intent(reason='TIMEOUT')  # RiskManager生成Intent
    ```
    """

    def __init__(self, algorithm, config):
        """
        初始化持仓超时规则

        Args:
            algorithm: QCAlgorithm实例
            config: HoldingTimeoutRuleConfig dataclass实例
        """
        super().__init__(algorithm, config)
        self.max_halflife_multiplier = config.max_halflife_multiplier


    def check(self, pair) -> Tuple[bool, str]:
        """
        检查配对是否触发持仓超时

        检查流程:
        1. 检查规则是否启用
        2. (v7.31.0 Fail-Safe) 检查该配对是否在冷却期
        3. 动态计算该配对的最大持有时间 (half_life × multiplier)
        4. 调用pair.get_pair_holding_days()获取实际持仓天数
        5. 判断是否超过动态阈值
        6. v7.33.0: 增加PnL条件 - 只在亏损/持平/异常时触发

        v7.33.0: 复合触发条件
        - 盈利配对 (pnl > 0): 不触发,继续持有
        - 亏损/持平配对 (pnl <= 0): 触发平仓
        - 数据异常 (pnl is None): 触发平仓 (Fail-Safe原则)

        Args:
            pair: Pairs对象,必须实现get_pair_holding_days(), get_pair_pnl(), half_life属性

        Returns:
            (is_triggered, description)
            - is_triggered: True表示超时且亏损/异常,False表示未触发
            - description: 详细描述(包含持仓天数、动态阈值、半衰期、PnL信息)

        设计说明 (v7.11.0):
            - 动态持有时间 = pair.half_life × max_halflife_multiplier
            - 不同配对有不同的持有时间上限 (如8天配对→16天, 20天配对→40天)
            - 避免了固定阈值对慢速配对的过度惩罚
            - 与动态Half-life评分曲线配合,构建完整的自适应风控体系

        示例:
            # 超时+亏损 → 触发
            triggered, desc = rule.check(pair=pair_obj)
            # 返回: (True, "已持仓25天 > 上限20.0天 (半衰期10.0天 × 2.0), PnL=$-500 (亏损/持平)")

            # 超时+盈利 → 不触发
            triggered, desc = rule.check(pair=pair_obj)
            # 返回: (False, "")

            # 超时+数据异常 → 触发
            triggered, desc = rule.check(pair=pair_obj)
            # 返回: (True, "已持仓25天 > 上限20.0天, PnL数据缺失 (异常状态)")
        """
        # 1. 检查是否启用
        if not self.enabled:
            return False, ""

        # 2. v7.31.0: Fail-Safe - RiskManager应已过滤冷却期配对
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
            # v7.33.0: 增加PnL条件判断
            pair_pnl = pair.get_pair_pnl()

            # PnL数据完整性检查
            if pair_pnl is None:
                # 数据不完整 → 触发平仓 (Fail-Safe原则)
                description = (
                    f"已持仓{holding_days}天 > 上限{max_days:.1f}天, "
                    f"PnL数据缺失 (异常状态)"
                )
                return True, description

            # PnL条件: 只在亏损/持平时触发
            if pair_pnl > 0:
                # 盈利配对: 不触发,继续持有
                return False, ""

            # 亏损或持平配对: 触发平仓
            # v7.30.7: 简化description(移除"持仓超时:"前缀和开仓时间)
            # v7.33.0: 增加PnL信息
            if pair.half_life_std > 0:
                # 新公式: half_life + 2*std
                description = (
                    f"已持仓{holding_days}天 > "
                    f"上限{max_days:.1f}天 "
                    f"(半衰期{pair.half_life:.1f}天 + 2×标准差{pair.half_life_std:.1f}天), "
                    f"PnL=${pair_pnl:,.0f} (亏损/持平)"
                )
            else:
                # 旧公式: half_life × multiplier (兼容模式)
                description = (
                    f"已持仓{holding_days}天 > "
                    f"上限{max_days:.1f}天 "
                    f"(半衰期{pair.half_life:.1f}天 × {self.max_halflife_multiplier}), "
                    f"PnL=${pair_pnl:,.0f} (亏损/持平)"
                )
            return True, description

        return False, ""


    def get_trigger_name(self) -> str:
        """返回简化的触发名称(用于整合日志)"""
        return "持仓超时"
