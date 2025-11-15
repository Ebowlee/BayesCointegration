# region imports
from .RiskBaseRule import RiskRule
from typing import Tuple
# endregion


class PairCumulativeLossRule(RiskRule):
    """
    配对累计亏损风控规则 (v7.27.0: 从PairDrawdownRule分离)

    检测配对历史累计收益率,防止"每次亏一点点,累计亏很多"的温水煮青蛙配对。

    核心概念:
    - **累计亏损 (Cumulative Loss)**: 历史所有交易的加权平均收益率
    - **计算公式**: cumulative_return = realized_pnl / realized_cost
    - **触发条件**: cumulative_return < -threshold (如 -0.08 表示累计亏损8%)
    - **与回撤区别**: 回撤基于HWM(动态峰值),亏损基于cost(固定成本)

    功能:
    - 检测历史累计收益率是否低于阈值(默认-8%)
    - 触发后由RiskManager生成CloseIntent
    - 支持可配置冷却期(默认360天)
    - 优先级90 (高于PairDrawdownRule的80)

    配置参数:
    - enabled: 是否启用(默认True)
    - priority: 优先级(默认90,高于回撤规则)
    - threshold: 累计亏损阈值(默认0.08,即8%)
    - cooldown_days: 冷却期天数(默认360天)  # v7.28.0新增

    设计特点:
    - 概念纯粹: 只检测累计亏损,不涉及回撤
    - 独立阈值: 与单次回撤(5%)分离配置
    - 高优先级: 累计问题更严重,优先触发
    - 无最小交易次数: 只要有历史交易就检查
    - 配对专属计算: 使用Pairs.realized_pnl和realized_cost

    使用示例:
    ```python
    # 在RiskManager中
    config = self.config.risk_management['pair_rules']['pair_cumulative_loss']
    rule = PairCumulativeLossRule(algorithm, config)

    intent = rule.check(pair)
    if intent:
        # RiskManager生成CloseIntent
        # ExecutionManager执行Intent后激活cooldown
    ```

    触发场景示例:
    - 配对历史: 5笔交易,累计PnL=-$1,200, 累计Cost=$15,000
    - 累计收益率: -8.0%
    - 触发日志: "配对累计亏损: -8.0% <= -8.0% (5笔历史)"
    """

    def __init__(self, algorithm, config: dict):
        """
        初始化配对累计亏损规则

        Args:
            algorithm: QuantConnect算法实例
            config: 规则配置字典
        """
        super().__init__(algorithm, config)


    def check(self, pair) -> Tuple[bool, str]:
        """
        检查配对是否触发累计亏损风控

        检测逻辑:
        1. 检查规则是否启用
        2. 检查该配对是否在冷却期
        3. 检查是否有历史交易数据
        4. 计算累计收益率: realized_pnl / realized_cost
        5. 判断是否低于阈值

        Args:
            pair: Pairs对象,必须实现 realized_pnl, realized_cost, trade_count 属性

        Returns:
            (is_triggered, description)
            - is_triggered: True表示触发风控,False表示正常
            - description: 详细描述

        触发示例:
            (True, "配对累计亏损: -10.5% <= -8.0% (5笔历史)")

        未触发原因:
            - 规则未启用
            - 配对在冷却期
            - 无历史交易(trade_count=0)
            - 累计收益率未达阈值(如-5% > -8%)
        """
        # 1. 检查是否启用
        if not self.enabled:
            return False, ""

        # 2. 检查该配对是否在冷却期
        if self.is_in_cooldown(pair_id=pair.pair_id):
            return False, ""

        # 3. 前置条件: 必须有历史交易数据
        if pair.trade_count == 0 or pair.realized_cost <= 0:
            return False, ""

        # 4. 计算累计收益率 (小数形式,如 -0.11 代表 -11%)
        cumulative_return = pair.realized_pnl / pair.realized_cost

        # 5. 获取阈值
        threshold = self.config['threshold']

        # 智能日志: 只在触发或接近阈值时打印(减少噪音)
        warning_threshold = threshold * 0.8  # 警告线: 阈值的80%

        # 6. 判断是否触发 (累计亏损 >= 阈值)
        if cumulative_return <= -threshold:
            # 触发: 累计亏损超过阈值
            description = (
                f"配对累计亏损: {cumulative_return*100:.1f}% <= "
                f"-{threshold*100:.1f}% ({pair.trade_count}笔历史, "
                f"PnL=${pair.realized_pnl:,.0f}, Cost=${pair.realized_cost:,.0f})"
            )
            self.algorithm.Debug(f"[Pair风控] PairCumulativeLossRule 触发! {description}")
            return True, description

        # 接近阈值时打印警告(警告线到阈值之间)
        elif cumulative_return <= -warning_threshold:
            self.algorithm.Debug(
                f"[Pair风控] PairCumulativeLossRule 警告: "
                f"累计亏损={cumulative_return*100:.2f}% (接近阈值-{threshold*100:.0f}%, "
                f"{pair.trade_count}笔历史)"
            )

        # 正常情况: 静默(不打印,减少日志噪音)
        return False, ""
