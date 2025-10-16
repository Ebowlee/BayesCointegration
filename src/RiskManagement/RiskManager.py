"""
RiskManager - 风控调度器

统一管理Portfolio和Pair层面的风控规则，按优先级调度，返回最高优先级的风控动作。
"""

from AlgorithmImports import *
from .base import RiskRule
from .AccountBlowupRule import AccountBlowupRule
from typing import List, Tuple, Optional


class RiskManager:
    """
    风控调度器

    职责:
    - 注册和管理所有Portfolio和Pair层面的风控规则
    - 按优先级检查规则，返回最高优先级的动作
    - 不执行实际操作（由main.py执行）

    设计原则:
    - 配置驱动：从config自动注册规则
    - 无状态调度：不保存触发历史，规则自己管理冷却期
    - 优先级排序：始终返回最高优先级规则的动作
    - 分层检查：Portfolio和Pair分开调度

    使用示例:
    ```python
    # 在main.py的Initialize()中
    from src.RiskManagement import RiskManager
    self.risk_manager = RiskManager(self, self.config)

    # 在OnData()中检查Portfolio风控
    action, triggered = self.risk_manager.check_portfolio_risks()
    if action == 'portfolio_liquidate_all':
        self._liquidate_all_positions()
        # 激活触发规则的冷却期
        for rule, desc in triggered:
            rule.activate_cooldown()

    # 在交易循环中检查Pair风控
    for pair in pairs:
        action, triggered = self.risk_manager.check_pair_risks(pair)
        if action == 'pair_close':
            pair.close_position()
    ```
    """

    def __init__(self, algorithm, config):
        """
        初始化风控调度器

        Args:
            algorithm: QuantConnect算法实例
            config: StrategyConfig配置对象
        """
        self.algorithm = algorithm
        self.config = config

        # 全局开关
        self.enabled = config.risk_management.get('enabled', True)

        # 注册Portfolio层面规则
        self.portfolio_rules = self._register_portfolio_rules()

        # 注册Pair层面规则（暂时为空，后续步骤实现）
        self.pair_rules = []

        # 调试信息
        if self.enabled:
            self.algorithm.Debug(
                f"[RiskManager] 初始化完成: "
                f"Portfolio规则={len(self.portfolio_rules)}, "
                f"Pair规则={len(self.pair_rules)}"
            )
        else:
            self.algorithm.Debug("[RiskManager] 全局禁用")


    def _register_portfolio_rules(self) -> List[RiskRule]:
        """
        从config动态注册Portfolio层面风控规则

        注册流程:
        1. 从config.risk_management['portfolio_rules']读取规则配置
        2. 通过rule_map映射规则名称到规则类
        3. 只注册enabled=True的规则
        4. 返回规则实例列表

        扩展方法:
        未来添加新规则只需在rule_map中添加映射:
            'new_rule_name': NewRuleClass

        Returns:
            规则实例列表（已按priority降序排序）
        """
        rules = []

        # 检查全局开关
        if not self.enabled:
            return rules

        # 规则名称 -> 规则类的映射
        rule_map = {
            'account_blowup': AccountBlowupRule,
            # 未来添加更多Portfolio规则:
            # 'excessive_drawdown': ExcessiveDrawdownRule,
            # 'market_volatility': MarketVolatilityRule,
            # 'sector_concentration': SectorConcentrationRule,
        }

        # 从config读取规则配置
        portfolio_rule_configs = self.config.risk_management.get('portfolio_rules', {})

        # 遍历rule_map，动态注册
        for rule_name, rule_class in rule_map.items():
            if rule_name in portfolio_rule_configs:
                rule_config = portfolio_rule_configs[rule_name]

                # 只注册enabled的规则
                if rule_config.get('enabled', True):
                    try:
                        rule_instance = rule_class(self.algorithm, rule_config)
                        rules.append(rule_instance)
                        self.algorithm.Debug(
                            f"[RiskManager] 注册Portfolio规则: {rule_class.__name__} "
                            f"(priority={rule_instance.priority})"
                        )
                    except Exception as e:
                        self.algorithm.Debug(
                            f"[RiskManager] 注册规则失败 {rule_name}: {str(e)}"
                        )

        # 按优先级降序排序（priority高的在前）
        rules.sort(key=lambda r: r.priority, reverse=True)

        return rules


    def check_portfolio_risks(self) -> Tuple[Optional[str], List[Tuple[RiskRule, str]]]:
        """
        检查Portfolio层面风控

        调度逻辑:
        1. 检查全局enabled开关
        2. 遍历所有Portfolio规则（已按priority降序排序）
        3. 跳过disabled和冷却中的规则
        4. 调用rule.check()收集所有触发的规则
        5. 返回最高优先级规则的action + 完整触发列表

        Returns:
            (action, triggered_rules)
            - action: 'portfolio_liquidate_all'等，或None（未触发）
            - triggered_rules: [(rule, description), ...] 所有触发的规则列表

        注意:
        - 返回action后，main.py负责执行动作并激活rule.activate_cooldown()
        - 本方法不修改任何状态，纯粹的检查和返回
        """
        # 全局禁用时直接返回
        if not self.enabled:
            return None, []

        triggered_rules = []

        # 遍历所有Portfolio规则（已按priority降序排序）
        for rule in self.portfolio_rules:
            # 跳过禁用的规则
            if not rule.enabled:
                continue

            # 跳过冷却期内的规则
            if rule.is_in_cooldown():
                continue

            # 检查规则
            try:
                triggered, description = rule.check()
                if triggered:
                    triggered_rules.append((rule, description))
            except Exception as e:
                self.algorithm.Debug(
                    f"[RiskManager] 规则检查异常 {rule.__class__.__name__}: {str(e)}"
                )

        # 如果有触发，返回最高优先级规则的动作
        if triggered_rules:
            # triggered_rules已经按priority排序（因为self.portfolio_rules是排序的）
            highest_priority_rule, description = triggered_rules[0]
            action = highest_priority_rule.get_action()

            self.algorithm.Debug(
                f"[风控触发] Portfolio: {highest_priority_rule.__class__.__name__} "
                f"(priority={highest_priority_rule.priority}) -> {action}"
            )
            self.algorithm.Debug(f"[风控描述] {description}")

            return action, triggered_rules
        else:
            return None, []


    def has_any_rule_in_cooldown(self) -> bool:
        """
        检查是否有任何Portfolio规则在冷却期

        用途:
        在OnData中检查，确保冷却期内不执行任何交易逻辑。
        这是防止风控触发后继续交易的关键机制。

        Returns:
            True: 有规则在冷却期，应阻止所有交易
            False: 没有规则在冷却期，可以继续交易

        示例:
        ```python
        # 在main.py的OnData()中
        if self.risk_manager.has_any_rule_in_cooldown():
            return  # 冷却期内阻止所有交易
        ```
        """
        if not self.enabled:
            return False

        for rule in self.portfolio_rules:
            if rule.is_in_cooldown():
                return True

        return False


    def check_pair_risks(self, pair) -> Tuple[Optional[str], List[Tuple[RiskRule, str]]]:
        """
        检查Pair层面风控

        Args:
            pair: Pairs对象

        Returns:
            (action, triggered_rules)
            - action: 'pair_close', 'pair_liquidate'等，或None
            - triggered_rules: [(rule, description), ...]

        注意:
        本步骤（Step 3）暂未实现Pair规则，占位返回None。
        后续步骤将实现HoldingTimeoutRule等Pair规则。
        """
        # 全局禁用时直接返回
        if not self.enabled:
            return None, []

        # Step 3暂不实现Pair规则，占位
        # 后续步骤将添加:
        # - HoldingTimeoutRule
        # - PositionAnomalyRule
        # - PairDrawdownRule

        return None, []


    def get_registered_rules_info(self) -> dict:
        """
        获取已注册规则的信息（用于调试和测试）

        Returns:
            {
                'portfolio': [{'name': ..., 'priority': ..., 'enabled': ...}, ...],
                'pair': [...]
            }
        """
        info = {
            'portfolio': [],
            'pair': []
        }

        for rule in self.portfolio_rules:
            info['portfolio'].append({
                'name': rule.__class__.__name__,
                'priority': rule.priority,
                'enabled': rule.enabled,
                'in_cooldown': rule.is_in_cooldown()
            })

        for rule in self.pair_rules:
            info['pair'].append({
                'name': rule.__class__.__name__,
                'priority': rule.priority,
                'enabled': rule.enabled,
                'in_cooldown': rule.is_in_cooldown()
            })

        return info
