"""
ExcessiveDrawdownRule - 过度回撤风控规则

检测账户净值从最高水位的回撤比例,如果超过阈值则触发全仓清算。
这是Portfolio层面的关键风控规则,优先级仅次于AccountBlowupRule。
与AccountBlowup的唯一区别是冷却期长度(30天vs永久)。
"""

from AlgorithmImports import *
from .base import RiskRule
from typing import Tuple


class ExcessiveDrawdownRule(RiskRule):
    """
    过度回撤风控规则

    功能:
    - 追踪账户净值的历史最高水位(high water mark)
    - 检测当前回撤是否超过阈值(默认15%)
    - 触发后返回'portfolio_liquidate_all'动作(全仓清算)
    - 支持30天冷却期(可恢复交易)

    配置参数:
    - enabled: 是否启用(默认True)
    - priority: 优先级(默认90,仅次于AccountBlowup的100)
    - threshold: 回撤阈值(默认0.15,即15%)
    - cooldown_days: 冷却期天数(默认30,可恢复)
    - action: 响应动作(默认'portfolio_liquidate_all')

    与AccountBlowupRule的区别:
    - 触发条件: 回撤(动态HWM) vs 亏损(固定initial_capital)
    - 冷却期: 30天(可恢复) vs 36500天(永久)
    - 响应动作: 相同,都是全仓清算
    - HWM重置: 触发时重置HWM为当前净值,避免冷却期后重复触发

    触发后行为:
    1. 执行全仓清算(调用_liquidate_all_positions)
    2. 重置HWM为当前净值(避免冷却期后因回撤持续而重复触发)
    3. 激活30天冷却期
    4. 30天后,如果净值未继续下跌,不会再次触发

    使用示例:
    ```python
    config = self.config.risk_management['portfolio_rules']['excessive_drawdown']
    rule = ExcessiveDrawdownRule(algorithm, config)

    triggered, description = rule.check()
    if triggered:
        action = rule.get_action()  # 'portfolio_liquidate_all'
        # 执行全仓清算...
        rule.activate_cooldown()  # 激活30天冷却期
        # HWM已在check()中自动重置
    ```
    """

    def __init__(self, algorithm, config: dict):
        """
        初始化过度回撤规则

        Args:
            algorithm: QuantConnect算法实例
            config: 规则配置字典
        """
        super().__init__(algorithm, config)

        # 初始化最高水位为初始资金
        self.high_water_mark = algorithm.config.main['cash']

        # Debug日志
        if algorithm.config.main.get('debug_mode', False):
            self.algorithm.Debug(
                f"[ExcessiveDrawdown] 初始化: HWM=${self.high_water_mark:,.0f}"
            )


    def check(self, **kwargs) -> Tuple[bool, str]:
        """
        检测账户是否触发过度回撤

        检测逻辑:
        1. 更新最高水位(如果当前净值更高)
        2. 计算回撤比例 = (最高水位 - 当前净值) / 最高水位
        3. 如果回撤比例 >= 阈值,则触发
        4. 触发时重置HWM为当前净值(避免冷却期后重复触发)

        Returns:
            (是否触发, 风险描述)
            - 触发: (True, "过度回撤: 回撤XX.X% >= 阈值YY.Y%")
            - 未触发: (False, "")

        重要: 触发后HWM会自动重置,这是防止冷却期循环触发的关键机制
        """
        # 检查是否在冷却期内
        if self.is_in_cooldown():
            if self.algorithm.config.main.get('debug_mode', False):
                self.algorithm.Debug(
                    f"[ExcessiveDrawdown] 跳过: 冷却期至{self.cooldown_until}"
                )
            return False, ""

        # 获取当前账户总价值
        portfolio_value = self.algorithm.Portfolio.TotalPortfolioValue

        # 更新最高水位
        if portfolio_value > self.high_water_mark:
            if self.algorithm.config.main.get('debug_mode', False):
                self.algorithm.Debug(
                    f"[ExcessiveDrawdown] 更新HWM: "
                    f"${self.high_water_mark:,.0f} -> ${portfolio_value:,.0f}"
                )
            self.high_water_mark = portfolio_value

        # 计算回撤比例
        drawdown = (self.high_water_mark - portfolio_value) / self.high_water_mark

        # 获取阈值
        threshold = self.config['threshold']

        # 添加诊断日志(debug模式下输出)
        if self.algorithm.config.main.get('debug_mode', False):
            self.algorithm.Debug(
                f"[ExcessiveDrawdown] 检查: HWM=${self.high_water_mark:,.0f}, "
                f"当前=${portfolio_value:,.0f}, 回撤={drawdown*100:.2f}%, "
                f"阈值={threshold*100:.0f}%"
            )

        # 判断是否触发(大于等于阈值)
        if drawdown >= threshold:
            description = (
                f"过度回撤: 回撤{drawdown*100:.1f}% >= 阈值{threshold*100:.1f}% "
                f"(当前价值: ${portfolio_value:,.0f}, 最高水位: ${self.high_water_mark:,.0f})"
            )
            self.algorithm.Debug(f"[ExcessiveDrawdown] 触发! {description}")

            # 重置HWM为当前净值,避免冷却期后重复触发
            # 逻辑: 触发清仓后,从当前净值重新开始追踪,相当于"重新归零"
            old_hwm = self.high_water_mark
            self.high_water_mark = portfolio_value
            self.algorithm.Debug(
                f"[ExcessiveDrawdown] 重置HWM: ${old_hwm:,.0f} -> ${self.high_water_mark:,.0f}"
            )

            return True, description

        return False, ""


    def get_action(self) -> str:
        """
        获取响应动作

        Returns:
            'portfolio_liquidate_all' - 全仓清算(与AccountBlowup相同)
        """
        return self.config['action']
