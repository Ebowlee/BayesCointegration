"""
PortfolioDrawdownRule - 组合回撤风控规则 

检测账户净值从最高水位的回撤比例,如果超过阈值则由RiskManager生成所有持仓的CloseIntent。
这是Portfolio层面的关键风控规则,优先级仅次于AccountBlowupRule。
与AccountBlowup的唯一区别是冷却期长度(30天vs永久)。
"""

from AlgorithmImports import *
from .RiskBaseRule import RiskRule
from typing import Tuple


class PortfolioDrawdownRule(RiskRule):
    """
    组合回撤风控规则 

    功能:
    - 追踪账户净值的历史最高水位(high water mark)
    - 检测当前回撤是否超过阈值(默认15%)
    - 触发后由RiskManager生成所有持仓的CloseIntent
    - 支持30天冷却期(可恢复交易)

    配置参数:
    - enabled: 是否启用(默认True)
    - priority: 优先级(默认90,仅次于AccountBlowup的100)
    - threshold: 回撤阈值(默认0.15,即15%)
    - cooldown_days: 冷却期天数(默认30,可恢复)

    与AccountBlowupRule的区别:
    - 触发条件: 回撤(动态HWM) vs 亏损(固定initial_capital)
    - 冷却期: 30天(可恢复) vs 36500天(永久)
    - HWM重置: 触发时重置HWM为当前净值,避免冷却期后重复触发

    触发后行为:
    1. RiskManager生成所有持仓的CloseIntent
    2. 重置HWM为当前净值(避免冷却期后因回撤持续而重复触发)
    3. ExecutionManager执行Intent后激活30天冷却期
    4. 30天后,如果净值未继续下跌,不会再次触发

    使用示例:
    ```python
    # 在RiskManager中
    config = self.config.risk_management['portfolio_rules']['portfolio_drawdown']
    rule = PortfolioDrawdownRule(algorithm, config)

    triggered, description = rule.check()
    if triggered:
        # RiskManager生成所有持仓的CloseIntent
        pairs = self.pairs_manager.get_pairs_with_position()
        intents = [pair.get_close_intent(reason='RISK_TRIGGER') for pair in pairs.values()]
        # ExecutionManager执行Intent后激活cooldown
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
                f"[PortfolioDrawdown] 初始化: HWM=${self.high_water_mark:,.0f}"
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
                    f"[PortfolioDrawdown] 跳过: 冷却期至{self.cooldown_until}"
                )
            return False, ""

        # 获取当前账户总价值
        portfolio_value = self.algorithm.Portfolio.TotalPortfolioValue

        # 更新最高水位
        if portfolio_value > self.high_water_mark:
            if self.algorithm.config.main.get('debug_mode', False):
                self.algorithm.Debug(
                    f"[PortfolioDrawdown] 更新HWM: "
                    f"${self.high_water_mark:,.0f} -> ${portfolio_value:,.0f}"
                )
            self.high_water_mark = portfolio_value

        # 计算回撤比例
        drawdown = (self.high_water_mark - portfolio_value) / self.high_water_mark

        # 获取阈值
        threshold = self.config['threshold']

        # 智能日志: 只在触发或接近阈值时打印(减少噪音)
        warning_threshold = threshold * 0.8  # 警告线: 阈值的80%

        # 判断是否触发(大于等于阈值)
        if drawdown >= threshold:
            description = (
                f"组合回撤: 回撤{drawdown*100:.1f}% >= 阈值{threshold*100:.1f}% "
                f"(当前价值: ${portfolio_value:,.0f}, 最高水位: ${self.high_water_mark:,.0f})"
            )
            self.algorithm.Debug(f"[PortfolioDrawdown] 触发! {description}")

            # 重置HWM为当前净值,避免冷却期后重复触发
            # 逻辑: 触发清仓后,从当前净值重新开始追踪,相当于"重新归零"
            old_hwm = self.high_water_mark
            self.high_water_mark = portfolio_value
            self.algorithm.Debug(
                f"[PortfolioDrawdown] 重置HWM: ${old_hwm:,.0f} -> ${self.high_water_mark:,.0f}"
            )

            return True, description

        # 接近阈值时打印警告(警告线到阈值之间)
        elif drawdown >= warning_threshold:
            self.algorithm.Debug(
                f"[PortfolioDrawdown] 警告: 回撤={drawdown*100:.2f}% (接近阈值{threshold*100:.0f}%, "
                f"HWM=${self.high_water_mark:,.0f}, 当前=${portfolio_value:,.0f})"
            )

        # 正常情况: 静默(不打印,减少日志噪音)
        return False, ""
