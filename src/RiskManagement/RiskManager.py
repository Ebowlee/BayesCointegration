"""
RiskManager - 风控调度器

统一管理Portfolio和Pair层面的风控规则，按优先级调度，返回最高优先级的风控动作。
"""

from AlgorithmImports import *
from .base import RiskRule
from .AccountBlowupRule import AccountBlowupRule
from .ExcessiveDrawdownRule import ExcessiveDrawdownRule
from .MarketCondition import MarketCondition
from .HoldingTimeoutRule import HoldingTimeoutRule
from .PositionAnomalyRule import PositionAnomalyRule
from .PairDrawdownRule import PairDrawdownRule
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

    def __init__(self, algorithm, config, pairs_manager=None):
        """
        初始化风控调度器

        Args:
            algorithm: QuantConnect算法实例
            config: StrategyConfig配置对象
            pairs_manager: PairsManager实例（v6.9.3新增，用于集中度分析）
        """
        self.algorithm = algorithm
        self.config = config
        self.pairs_manager = pairs_manager  # v6.9.3: 用于 get_sector_concentration()

        # 全局开关
        self.enabled = config.risk_management.get('enabled', True)

        # 注册Portfolio层面规则
        self.portfolio_rules = self._register_portfolio_rules()

        # 注册Pair层面规则
        self.pair_rules = self._register_pair_rules()

        # 初始化市场条件检查器（独立于风控规则）
        self.market_condition = MarketCondition(algorithm, config)

        # 调试信息
        if self.enabled:
            self.algorithm.Debug(
                f"[RiskManager] 初始化完成: "
                f"Portfolio规则={len(self.portfolio_rules)}, "
                f"Pair规则={len(self.pair_rules)}, "
                f"MarketCondition已启用"
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
            'excessive_drawdown': ExcessiveDrawdownRule,
            # 未来添加更多Portfolio规则:
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


    def _register_pair_rules(self) -> List[RiskRule]:
        """
        从config动态注册Pair层面风控规则

        注册流程:
        1. 从config.risk_management['pair_rules']读取规则配置
        2. 通过rule_map映射规则名称到规则类
        3. 只注册enabled=True的规则
        4. 返回规则实例列表

        当前支持的规则:
        - HoldingTimeoutRule: 持仓超时检测
        - (PositionAnomalyRule: Step 2实现)
        - (PairDrawdownRule: Step 3实现)

        Returns:
            规则实例列表（已按priority降序排序）
        """
        rules = []

        # 检查全局开关
        if not self.enabled:
            return rules

        # 规则名称 -> 规则类的映射
        rule_map = {
            'holding_timeout': HoldingTimeoutRule,
            'position_anomaly': PositionAnomalyRule,  # Step 2已实现
            'pair_drawdown': PairDrawdownRule,        # Step 3已实现
        }

        # 从config读取规则配置
        pair_rule_configs = self.config.risk_management.get('pair_rules', {})

        # 遍历rule_map，动态注册
        for rule_name, rule_class in rule_map.items():
            if rule_name in pair_rule_configs:
                rule_config = pair_rule_configs[rule_name]

                # 只注册enabled的规则
                if rule_config.get('enabled', True):
                    try:
                        rule_instance = rule_class(self.algorithm, rule_config)
                        rules.append(rule_instance)
                        self.algorithm.Debug(
                            f"[RiskManager] 注册Pair规则: {rule_class.__name__} "
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


    def is_safe_to_open_positions(self) -> bool:
        """
        检查市场条件是否允许开仓

        职责:
        - 转发到MarketCondition进行市场波动检查
        - 只影响开仓决策，不影响平仓

        Returns:
            True: 市场条件良好，允许开仓
            False: 市场条件不佳（如高波动），禁止开仓

        使用示例:
        ```python
        # 在main.py的OnData()中，开仓前检查
        if not self.risk_manager.is_safe_to_open_positions():
            return  # 高波动时阻止开仓，但允许平仓继续
        ```

        注意:
        - 此方法与Portfolio风控（has_any_rule_in_cooldown）完全独立
        - 不受risk_management['enabled']全局开关控制
        - MarketCondition有自己独立的enabled开关
        """
        return self.market_condition.is_safe_to_open_positions()


    def get_sector_concentration(self) -> dict:
        """
        获取子行业集中度分析

        从 PairsManager.get_sector_concentration() 迁移而来 (v6.9.3)

        职责:
        - RiskManager 负责风控分析（集中度计算）
        - 通过 PairsManager 查询配对列表（职责分离）
        - 不直接访问 PairsManager 内部状态

        Returns:
            Dict[str, Dict]: 集中度分析结果
            {
                'IndustryGroup名称': {
                    'concentration': float,  # 占总资产比例
                    'value': float,          # 该行业持仓总值
                    'pairs': List[Pairs],    # 该行业的配对列表
                    'pair_count': int        # 配对数量
                }
            }

        设计理念:
        - 依赖注入: 通过 self.pairs_manager 获取数据
        - 接口复用: 调用 get_all_tradeable_pairs() 而非直接访问 pairs 字典
        - 单一职责: 只负责分析计算，不负责存储管理

        示例:
        ```python
        # 在 main.py 中调用
        concentrations = self.risk_manager.get_sector_concentration()
        for industry, data in concentrations.items():
            if data['concentration'] > 0.4:  # 超过40%集中度
                self.Debug(f"[风控] {industry} 集中度过高: {data['concentration']:.1%}")
        ```
        """
        portfolio = self.algorithm.Portfolio
        total_value = portfolio.TotalPortfolioValue

        if total_value <= 0:
            return {}

        # 防御性检查: pairs_manager 未注入
        if self.pairs_manager is None:
            return {}

        industry_group_data = {}

        # 从 PairsManager 获取配对列表（依赖注入 + 接口调用）
        tradeable_pairs = self.pairs_manager.get_all_tradeable_pairs()

        for pair in tradeable_pairs.values():
            if not pair.has_position():
                continue

            info = pair.get_position_info()

            industry_group = pair.industry_group
            if industry_group not in industry_group_data:
                industry_group_data[industry_group] = {
                    'value': 0,
                    'pairs': []
                }

            industry_group_data[industry_group]['value'] += info['value1'] + info['value2']
            industry_group_data[industry_group]['pairs'].append(pair)

        # 计算集中度并格式化返回
        result = {}
        for industry_group, data in industry_group_data.items():
            concentration = data['value'] / total_value
            result[industry_group] = {
                'concentration': concentration,
                'value': data['value'],
                'pairs': data['pairs'],
                'pair_count': len(data['pairs'])
            }

        return result


    def check_pair_risks(self, pair) -> Tuple[Optional[str], List[Tuple[RiskRule, str]]]:
        """
        检查Pair层面风控

        调度逻辑:
        1. 检查全局enabled开关
        2. 遍历所有Pair规则（已按priority降序排序）
        3. 调用rule.check(pair=pair)收集所有触发的规则
        4. 返回最高优先级规则的action + 完整触发列表

        Args:
            pair: Pairs对象,必须包含position_opened_time等属性

        Returns:
            (action, triggered_rules)
            - action: 'pair_close'等，或None（未触发）
            - triggered_rules: [(rule, description), ...] 所有触发的规则列表

        注意:
        - 返回action后，main.py负责执行平仓
        - 本方法不修改任何状态，纯粹的检查和返回
        - 不检查冷却期(Pair规则无需冷却期,订单锁已保护)

        当前支持的规则:
        - HoldingTimeoutRule: 持仓超时检测 (priority=60)
        """
        # 全局禁用时直接返回
        if not self.enabled:
            return None, []

        triggered_rules = []

        # 遍历所有Pair规则（已按priority降序排序）
        for rule in self.pair_rules:
            # 跳过禁用的规则
            if not rule.enabled:
                continue

            # 检查规则（无需冷却期检查,Pair规则不使用冷却期）
            try:
                triggered, description = rule.check(pair=pair)
                if triggered:
                    triggered_rules.append((rule, description))
            except Exception as e:
                self.algorithm.Debug(
                    f"[RiskManager] Pair规则检查异常 {rule.__class__.__name__}: {str(e)}"
                )

        # 如果有触发，返回最高优先级规则的动作
        if triggered_rules:
            # triggered_rules已经按priority排序（因为self.pair_rules是排序的）
            highest_priority_rule, description = triggered_rules[0]
            action = highest_priority_rule.get_action()

            # Debug日志由main.py统一输出,这里只记录触发事实
            if self.config.main.get('debug_mode', False):
                self.algorithm.Debug(
                    f"[Pair风控] {highest_priority_rule.__class__.__name__} "
                    f"(priority={highest_priority_rule.priority}) -> {action}"
                )

            return action, triggered_rules
        else:
            return None, []


    def check_all_pair_risks(self, pairs_with_position) -> dict:
        """
        批量检测所有配对的风险(对称Portfolio风控)

        设计目标:
        - 与check_portfolio_risks()完全对称
        - 实现"检测与执行分离"原则
        - main.py只负责分发和执行

        Args:
            pairs_with_position: 有持仓的配对字典 {pair_id: Pairs}

        Returns:
            Dict[pair_id, (action, triggered_rules)]
            - pair_id: 触发风控的配对ID
            - action: 'pair_close'等
            - triggered_rules: [(rule, description), ...]

        示例:
            risk_actions = risk_manager.check_all_pair_risks(pairs_with_position)
            # 返回: {
            #   ('AAPL', 'MSFT'): ('pair_close', [(HoldingTimeoutRule, "持仓超时...")]),
            #   ('GOOG', 'GOOGL'): ('pair_close', [(PairDrawdownRule, "回撤超限...")])
            # }

        注意:
        - 只返回触发风控的配对,未触发的不包含在字典中
        - 与check_pair_risks()内部调用相同逻辑,只是包装为批量接口
        - 实现Portfolio和Pair风控完全对称的架构
        """
        risk_actions = {}

        for pair in pairs_with_position.values():
            action, triggered_rules = self.check_pair_risks(pair)
            if action:
                risk_actions[pair.pair_id] = (action, triggered_rules)

        return risk_actions


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
