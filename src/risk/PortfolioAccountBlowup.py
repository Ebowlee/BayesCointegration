"""
AccountBlowupRule - 账户爆仓线风控规则 

检测账户总价值的亏损比例，如果超过阈值则由RiskManager生成所有持仓的CloseIntent。
这是最高优先级的Portfolio层面风控规则。
"""

from AlgorithmImports import *
from .RiskBaseRule import RiskRule
from typing import Tuple


class AccountBlowupRule(RiskRule):
    """
    账户爆仓线风控规则 

    功能:
    - 检测账户亏损是否超过阈值（默认25%）
    - 触发后由RiskManager生成所有持仓的CloseIntent
    - 支持永久冷却期（默认36500天）

    配置参数:
    - enabled: 是否启用（默认True）
    - priority: 优先级（默认100，最高优先级）
    - threshold: 亏损阈值（默认0.25，即25%）
    - cooldown_days: 冷却期天数（默认36500，约100年，相当于永久）
    - 移除get_action()方法
    - Rule只负责检测,RiskManager负责生成Intent
    - Cooldown由RiskManager在Intent执行后激活

    使用示例:
    ```python
    # 在RiskManager中
    config = self.config.risk_management['portfolio_rules']['account_blowup']
    rule = AccountBlowupRule(algorithm, config)

    triggered, description = rule.check()
    if triggered:
        # RiskManager生成所有持仓的CloseIntent
        pairs = self.pairs_manager.get_pairs_with_position()
        intents = [pair.get_close_intent(reason='RISK_TRIGGER') for pair in pairs.values()]
        # ExecutionManager执行Intent后激活cooldown
    ```
    """

    def __init__(self, algorithm, config: dict):
        """
        初始化账户爆仓线规则

        Args:
            algorithm: QuantConnect算法实例
            config: 规则配置字典
        """
        super().__init__(algorithm, config)

        # 获取初始资金（用于计算亏损比例）
        self.initial_capital = algorithm.config.main['cash']


    def check(self, **kwargs) -> Tuple[bool, str]:
        """
        检测账户是否触发爆仓线

        检测逻辑:
        1. 获取当前账户总价值
        2. 计算亏损比例 = (初始资金 - 当前价值) / 初始资金
        3. 如果亏损比例 >= 阈值，则触发

        Returns:
            (是否触发, 风险描述)
            - 触发: (True, "账户爆仓: 亏损XX.X% >= 阈值YY.Y%")
            - 未触发: (False, "")
        """
        # 检查是否在冷却期内
        if self.is_in_cooldown():
            if self.algorithm.config.main.get('debug_mode', False):
                self.algorithm.Debug(f"[AccountBlowup] 跳过: 冷却期至{self.cooldown_until}")
            return False, ""

        # 获取当前账户总价值
        portfolio_value = self.algorithm.Portfolio.TotalPortfolioValue

        # 计算亏损比例
        loss_ratio = (self.initial_capital - portfolio_value) / self.initial_capital

        # 获取阈值
        threshold = self.config['threshold']

        # 智能日志: 只在触发或接近阈值时打印(减少噪音)
        warning_threshold = threshold * 0.8  # 警告线: 阈值的80%

        # 判断是否触发（大于等于阈值）
        if loss_ratio >= threshold:
            description = (
                f"账户爆仓: 亏损{loss_ratio*100:.1f}% >= 阈值{threshold*100:.1f}% "
                f"(当前价值: ${portfolio_value:,.0f}, 初始资金: ${self.initial_capital:,.0f})"
            )
            self.algorithm.Debug(f"[AccountBlowup] 触发! {description}")
            return True, description

        # 接近阈值时打印警告(警告线到阈值之间)
        elif loss_ratio >= warning_threshold:
            self.algorithm.Debug(
                f"[AccountBlowup] 警告: 亏损={loss_ratio*100:.2f}% (接近阈值{threshold*100:.0f}%, "
                f"当前=${portfolio_value:,.0f})"
            )

        # 正常情况: 静默(不打印,减少日志噪音)
        return False, ""
