# region imports
from .RiskBaseRule import RiskRule
from typing import Tuple
# endregion


class PairHoldingTimeoutRule(RiskRule):
    """
    持仓超时风控规则 (v7.38.1: 基于指数衰减物理公式)

    检测配对持仓时间是否超过基于均值回归路径计算的动态阈值。
    配对交易是短期均值回归策略,如果持仓超过物理预期时间仍未回归,
    说明协整关系可能失效,应止损退出。

    触发条件:
    - 持仓天数 > max_days (基于指数衰减公式动态计算)

    指数衰减公式 (v7.38.1):
    - 均值回归路径: Z(t) = Z_entry × (0.5)^(t/half_life)
    - 求解半衰期数: n = ln(exit_threshold/entry_zscore) / ln(0.5)
    - 最大持有天数: max_days = n × half_life

    设计特点:
    - 支持per-pair冷却期: 默认90天
    - 个性化超时限制: 每个配对根据实际entry_zscore计算专属阈值
    - 物理意义明确: 基于均值回归速率和入场位置的数学推导
    - v7.34.2: 移除盈利豁免机制，统一触发条件 (holding_days > max_days，不论盈亏)
    - 保留PnL=None时的Fail-Safe平仓逻辑
    - 优先级中等: priority=70

    配置示例 (v7.38.1简化):
    {
        'enabled': True,
        'priority': 70,
        'cooldown_days': 90
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
        # v7.38.1: 删除已废弃的max_halflife_multiplier配置


    def check(self, pair) -> Tuple[bool, str]:
        """
        检查配对是否触发持仓超时 (v7.38.1: 基于指数衰减公式)

        检查流程:
        1. 检查规则是否启用
        2. (v7.31.0 Fail-Safe) 检查该配对是否在冷却期
        3. v7.38.1: 基于指数衰减公式计算动态超时阈值
           - 获取exit_threshold (0.3) 和 entry_zscore (实际开仓Z-score)
           - 计算所需半衰期数: n = ln(exit_threshold/entry_zscore) / ln(0.5)
           - 计算最大持有天数: max_days = n × pair.half_life
        4. 调用pair.get_pair_holding_days()获取实际持仓天数
        5. 判断是否超过动态阈值
        6. v7.34.2: 统一触发条件 (不检查盈亏状态，仅Fail-Safe检查PnL=None)

        Args:
            pair: Pairs对象,必须实现:
                - get_pair_holding_days(): 返回持仓天数
                - get_pair_pnl(): 返回PnL (用于Fail-Safe检查)
                - half_life: 半衰期属性
                - entry_zscore: 实际开仓时的Z-score

        Returns:
            (is_triggered, description)
            - is_triggered: True表示超时(不论盈亏),False表示未触发
            - description: 详细描述(包含持仓天数、动态阈值、指数衰减路径)

        设计说明 (v7.38.1):
            - 个性化超时: 每个配对根据实际entry_zscore计算专属阈值
            - 物理意义: 基于均值回归速率的数学推导,而非统计置信区间
            - 示例计算: 入场1.9σ, 出场0.3σ, 半衰期8天
              → n = ln(0.3/1.9)/ln(0.5) ≈ 2.66
              → max_days = 2.66 × 8 ≈ 21天

        示例输出:
            # 超时触发
            triggered, desc = rule.check(pair=pair_obj)
            # 返回: (True, "已持仓25天 > 上限21.3天 (入场1.90σ → 出场0.3σ, 需2.66个半衰期 × 8.0天)")

            # PnL数据异常 → 触发 (Fail-Safe)
            triggered, desc = rule.check(pair=pair_obj)
            # 返回: (True, "已持仓25天 > 上限21.3天, PnL数据缺失 (异常状态)")
        """
        # 1. 检查是否启用
        if not self.enabled:
            return False, ""

        # 2. v7.31.0: Fail-Safe - RiskManager应已过滤冷却期配对
        if self.is_in_cooldown(pair_id=pair.pair_id):
            return False, ""

        # 3. v7.38.2: 调用Pairs.get_max_holding_days() (封装指数衰减公式)
        max_days = pair.get_max_holding_days()
        if max_days is None:
            return False, ""  # 数据不完整,不触发

        # 4. 获取实际持仓天数 (复用Pairs自带方法,避免时区问题)
        holding_days = pair.get_pair_holding_days()

        # 如果无法获取持仓天数(无持仓或未记录),不触发
        if holding_days is None:
            return False, ""

        # 5. 判断是否超过动态阈值
        if holding_days > max_days:
            # v7.34.2: Fail-Safe检查 - 仅验证PnL数据完整性
            pair_pnl = pair.get_pair_unrealized_pnl()

            # PnL数据完整性检查
            if pair_pnl is None:
                # 数据不完整 → 触发平仓 (Fail-Safe原则)
                description = (
                    f"已持仓{holding_days}天 > 上限{max_days:.1f}天, "
                    f"PnL数据缺失 (异常状态)"
                )
                return True, description

            # v7.38.2: 简化描述 (详细计算已封装在Pairs.get_max_holding_days()中)
            description = (
                f"已持仓{holding_days}天 > 上限{max_days:.1f}天 "
                f"(基于入场Z-score和半衰期{pair.half_life:.1f}天的指数衰减计算)"
            )
            return True, description

        return False, ""


    def get_trigger_name(self) -> str:
        """返回简化的触发名称(用于整合日志)"""
        return "持仓超时"
