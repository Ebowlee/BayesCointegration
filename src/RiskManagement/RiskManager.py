"""
RiskManager - 风控调度器 (v7.1.0 Intent Pattern重构)

统一管理Portfolio和Pair层面的风控规则，检测风险并生成CloseIntent对象。

v7.1.0核心变更:
- 规则只负责检测(check)，不再提供get_action()方法
- RiskManager统一生成CloseIntent对象(reason基于Rule→Reason映射)
- cooldown激活延后到Intent执行成功后(activate_cooldown_for_portfolio/pairs)
- 完全分离: Rule检测 → RiskManager生成Intent → ExecutionManager执行 → RiskManager激活cooldown
"""

from AlgorithmImports import *
from .PortfolioBaseRule import RiskRule
from .PortfolioAccountBlowup import AccountBlowupRule
from .PortfolioDrawdown import ExcessiveDrawdownRule
from .MarketCondition import MarketCondition
from .HoldingTimeoutRule import HoldingTimeoutRule
from .PositionAnomalyRule import PositionAnomalyRule
from .PairDrawdownRule import PairDrawdownRule
from src.execution.OrderIntent import CloseIntent  # v7.1.0: Intent Pattern
from typing import List, Tuple, Optional


class RiskManager:
    """
    风控调度器 (v7.1.0 Intent Pattern重构)

    职责:
    - 注册和管理所有Portfolio和Pair层面的风控规则
    - 按优先级检查规则，生成CloseIntent对象
    - 为执行成功的Intent激活对应Rule的cooldown
    - 不执行实际操作（由ExecutionManager执行）

    设计原则 (v7.1.0更新):
    - 配置驱动：从config自动注册规则
    - Intent生成：Rule检测 → RiskManager生成Intent → ExecutionManager执行
    - 映射机制：Rule类名→reason字符串, pair_id→Rule实例
    - 延迟cooldown：Intent执行成功后再激活cooldown
    - 优先级排序：始终返回最高优先级规则的Intent
    - 分层检查：Portfolio和Pair分开调度，API完全对称

    v7.1.0核心API变更:
    - check_portfolio_risks() → List[CloseIntent] (旧: (action, triggered_rules))
    - check_pair_risks(pair) → Optional[CloseIntent] (旧: (action, triggered_rules))
    - check_all_pair_risks() → List[CloseIntent] (旧: Dict[pair_id, (action, triggered)])
    - activate_cooldown_for_portfolio(executed_pair_ids) (新增)
    - activate_cooldown_for_pairs(executed_pair_ids) (新增)

    使用示例 (v7.1.0):
    ```python
    # 在main.py的Initialize()中
    from src.RiskManagement import RiskManager
    self.risk_manager = RiskManager(self, self.config, self.pairs_manager)

    # 在OnData()中检查Portfolio风控
    intents = self.risk_manager.check_portfolio_risks()
    if intents:
        executed_pair_ids = []
        for intent in intents:
            tickets = self.order_executor.execute_close(intent)
            if tickets:
                executed_pair_ids.append(intent.pair_id)
                self.tickets_manager.register_tickets(intent.pair_id, tickets, OrderAction.CLOSE)
        # Intent执行后激活cooldown
        self.risk_manager.activate_cooldown_for_portfolio(executed_pair_ids)

    # 检查Pair风控
    intents = self.risk_manager.check_all_pair_risks(pairs_with_position)
    if intents:
        executed_pair_ids = []
        for intent in intents:
            tickets = self.order_executor.execute_close(intent)
            if tickets:
                executed_pair_ids.append(intent.pair_id)
                self.tickets_manager.register_tickets(intent.pair_id, tickets, OrderAction.CLOSE)
        # Intent执行后激活cooldown
        self.risk_manager.activate_cooldown_for_pairs(executed_pair_ids)
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

        # v7.1.0 新增: Portfolio层映射机制(Intent Pattern)
        # Rule类名 → CloseIntent的reason字符串
        self._portfolio_rule_to_reason_map = {
            'AccountBlowupRule': 'RISK_TRIGGER',
            'ExcessiveDrawdownRule': 'RISK_TRIGGER',
        }
        # pair_id → Rule实例的映射(用于Intent执行后激活cooldown)
        self._portfolio_intent_to_rule_map = {}

        # v7.1.0 新增: Pair层映射机制(Intent Pattern)
        # Rule类名 → CloseIntent的reason字符串
        self._pair_rule_to_reason_map = {
            'HoldingTimeoutRule': 'TIMEOUT',
            'PositionAnomalyRule': 'ANOMALY',
            'PairDrawdownRule': 'DRAWDOWN',
        }
        # pair_id → Rule实例的映射(用于Intent执行后激活cooldown)
        self._pair_intent_to_rule_map = {}

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


    def check_portfolio_risks(self) -> List[CloseIntent]:
        """
        检查Portfolio层面风控,返回需要平仓的CloseIntent列表 (v7.1.0重构)

        调度逻辑:
        1. 检查全局enabled开关
        2. 清空Intent→Rule映射(新OnData周期开始)
        3. 遍历所有Portfolio规则(已按priority降序排序)
        4. 跳过disabled和冷却中的规则
        5. 调用rule.check()检测是否触发
        6. 如果触发,生成所有持仓的CloseIntent并记录映射
        7. 返回CloseIntent列表(ExecutionManager执行后激活cooldown)

        Returns:
            List[CloseIntent]: 需要平仓的Intent列表,空列表表示未触发

        v7.1.0变更:
        - 返回值从(action, triggered_rules)改为List[CloseIntent]
        - RiskManager负责生成Intent(不再依赖Rule.get_action())
        - 记录Intent→Rule映射(用于后续激活cooldown)
        - Portfolio规则触发后立即返回(最高优先级)

        使用示例:
            intents = self.risk_manager.check_portfolio_risks()
            if intents:
                self.execution_manager.handle_portfolio_risk_intents(intents, self.risk_manager)
        """
        # 全局禁用时直接返回
        if not self.enabled:
            return []

        # 清空Intent→Rule映射(每次OnData重新生成)
        self._portfolio_intent_to_rule_map.clear()

        # v7.1.0修复: Portfolio规则排他性检查
        # 任何一个Portfolio规则在cooldown，阻止所有风控检查（全局冻结）
        for rule in self.portfolio_rules:
            if rule.is_in_cooldown():
                # 发现cooldown规则，直接返回空列表
                # 原因: Portfolio规则影响全局，一个规则触发cooldown应阻止所有交易
                return []

        # 遍历所有Portfolio规则(已按priority降序排序)
        for rule in self.portfolio_rules:
            # 跳过禁用的规则
            if not rule.enabled:
                continue

            # 注意: 无需再检查cooldown（已在上方统一检查）

            # 检查规则
            try:
                triggered, description = rule.check()
                if triggered:
                    # 日志记录触发信息
                    self.algorithm.Debug(
                        f"[Portfolio风控] {rule.__class__.__name__} "
                        f"(priority={rule.priority}): {description}"
                    )

                    # 获取reason字符串
                    reason = self._portfolio_rule_to_reason_map.get(
                        rule.__class__.__name__,
                        'RISK_TRIGGER'  # 默认reason
                    )

                    # 生成所有持仓的CloseIntent
                    pairs = self.pairs_manager.get_pairs_with_position()
                    intents = []
                    for pair in pairs.values():
                        intent = pair.get_close_intent(reason=reason)
                        if intent:
                            intents.append(intent)
                            # 记录映射: pair_id → rule (用于后续激活cooldown)
                            self._portfolio_intent_to_rule_map[intent.pair_id] = rule

                    # Portfolio规则触发后立即返回(最高优先级,不检查其他规则)
                    if intents:
                        self.algorithm.Debug(
                            f"[Portfolio风控] 生成{len(intents)}个CloseIntent"
                        )
                        return intents

            except Exception as e:
                self.algorithm.Debug(
                    f"[RiskManager] Portfolio规则检查异常 {rule.__class__.__name__}: {str(e)}"
                )

        # 没有规则触发,返回空列表
        return []


    def activate_cooldown_for_portfolio(self, executed_pair_ids: List[Tuple]) -> None:
        """
        为已执行的Portfolio层Intent激活对应Rule的cooldown (v7.1.0新增)

        触发时机:
        - ExecutionManager执行完Portfolio风控的CloseIntent后调用
        - 传入所有成功执行的pair_id列表
        - 根据_portfolio_intent_to_rule_map查找对应的Rule并激活cooldown

        设计原则:
        - 去重: 同一个Rule只激活一次cooldown(即使生成了多个Intent)
        - 容错: 如果pair_id不在映射中(理论上不应该),跳过不报错
        - 日志: 每个激活的Rule都记录日志,方便追踪

        Args:
            executed_pair_ids: 已成功执行的pair_id列表
                              (ExecutionManager根据OrderTicket执行结果传入)

        示例:
        ```python
        # ExecutionManager中:
        intents = self.risk_manager.check_portfolio_risks()
        if intents:
            executed_pair_ids = self.executor.execute_intents(intents)
            # 激活cooldown
            self.risk_manager.activate_cooldown_for_portfolio(executed_pair_ids)
        ```

        v7.1.0变更:
        - 新增方法,替代原check_portfolio_risks()中的cooldown激活逻辑
        - cooldown激活延后到Intent执行成功后,而非检测时
        - 支持部分执行场景(只为成功执行的Intent激活cooldown)
        """
        activated_rules = set()  # 用于去重,防止同一Rule被多次激活

        for pair_id in executed_pair_ids:
            # 查找该pair_id对应的Rule
            if pair_id in self._portfolio_intent_to_rule_map:
                rule = self._portfolio_intent_to_rule_map[pair_id]

                # 检查是否已激活过(去重)
                if rule not in activated_rules:
                    rule.activate_cooldown()
                    activated_rules.add(rule)

                    # 记录日志
                    self.algorithm.Debug(
                        f"[Portfolio风控] {rule.__class__.__name__} "
                        f"冷却期激活至 {rule.cooldown_until}"
                    )

        # 清空映射(防止下次OnData误用旧映射)
        self._portfolio_intent_to_rule_map.clear()


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


    def cleanup_pair_hwm(self, pair_id: tuple):
        """
        清理配对的 HWM 状态 (v6.9.4 新增)

        职责:
        - 转发清理请求到 PairDrawdownRule
        - 避免 main.py 直接访问 Rule 内部

        调用时机:
        - 配对平仓后立即调用
        - main.py 在 OnData 中检测到平仓完成后调用

        Args:
            pair_id: 配对标识符元组 (symbol1, symbol2)

        示例:
        ```python
        # main.py 中
        if not pair.has_position():  # 检测到平仓完成
            self.risk_manager.cleanup_pair_hwm(pair.pair_id)
        ```

        设计原则:
        - 封装 Rule 内部细节
        - 提供统一的清理接口
        - 防止内存泄漏(长期运行策略)
        """
        # 转发到 PairDrawdownRule (通过 pair_rules 查找)
        for rule in self.pair_rules:
            # 只有 PairDrawdownRule 有 on_pair_closed 方法
            if hasattr(rule, 'on_pair_closed'):
                rule.on_pair_closed(pair_id)
                break


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


    def check_pair_risks(self, pair) -> Optional[CloseIntent]:
        """
        检查Pair层面风控,返回CloseIntent或None (v7.1.0重构)

        调度逻辑:
        1. 检查全局enabled开关
        2. 遍历所有Pair规则（已按priority降序排序）
        3. 找到第一个触发的规则立即返回Intent（最高优先级）
        4. 生成CloseIntent并记录映射(用于后续cooldown激活)

        Args:
            pair: Pairs对象,必须包含pair_opened_time等属性

        Returns:
            Optional[CloseIntent]:
            - CloseIntent对象: 包含pair_id, symbols, quantities, reason, tag
            - None: 未触发任何规则

        v7.1.0变更:
        - 返回类型从 (action, triggered_rules) 改为 Optional[CloseIntent]
        - Rule不再提供get_action(),RiskManager统一生成Intent
        - 触发时立即返回(最高优先级),不再收集所有触发规则
        - 记录Intent→Rule映射,供activate_cooldown_for_pairs()使用

        注意:
        - 不检查冷却期(Pair规则无需冷却期,订单锁已保护)
        - 本方法不修改任何状态,纯粹的检查和返回
        - Intent执行后由ExecutionManager调用activate_cooldown_for_pairs()

        当前支持的规则:
        - PositionAnomalyRule: 仓位异常检测 (priority=100)
        - HoldingTimeoutRule: 持仓超时检测 (priority=60)
        - PairDrawdownRule: 配对回撤检测 (priority=50)
        """
        # 全局禁用时直接返回
        if not self.enabled:
            return None

        # 遍历所有Pair规则（已按priority降序排序）
        for rule in self.pair_rules:
            # 跳过禁用的规则
            if not rule.enabled:
                continue

            # 检查规则（无需冷却期检查,Pair规则不使用冷却期）
            try:
                triggered, description = rule.check(pair=pair)
                if triggered:
                    # 找到触发规则,立即生成Intent并返回(最高优先级)
                    self.algorithm.Debug(
                        f"[Pair风控] {rule.__class__.__name__} 触发: {description}"
                    )

                    # 获取reason字符串
                    reason = self._pair_rule_to_reason_map.get(
                        rule.__class__.__name__, 'RISK_TRIGGER')

                    # 生成CloseIntent
                    intent = pair.get_close_intent(reason=reason)
                    if intent:
                        # 记录映射: pair_id → rule (用于cooldown激活)
                        self._pair_intent_to_rule_map[intent.pair_id] = rule
                        return intent
                    else:
                        # 理论上不应该发生(pair有持仓才会被检查)
                        self.algorithm.Error(
                            f"[Pair风控] {pair.pair_id} 触发{rule.__class__.__name__}但无法生成Intent"
                        )
                        return None

            except Exception as e:
                self.algorithm.Debug(
                    f"[RiskManager] Pair规则检查异常 {rule.__class__.__name__}: {str(e)}"
                )

        # 没有规则触发,返回None
        return None


    def check_all_pair_risks(self, pairs_with_position) -> List[CloseIntent]:
        """
        批量检测所有配对的风险,返回CloseIntent列表 (v7.1.0重构)

        设计目标:
        - 与check_portfolio_risks()完全对称
        - 实现"检测与执行分离"原则
        - ExecutionManager只负责执行Intent

        Args:
            pairs_with_position: 有持仓的配对字典 {pair_id: Pairs}

        Returns:
            List[CloseIntent]: 需要平仓的Intent列表,空列表表示未触发

        v7.1.0变更:
        - 返回类型从 Dict[pair_id, (action, triggered_rules)] 改为 List[CloseIntent]
        - 调用check_pair_risks()返回Optional[CloseIntent]
        - 自动记录Intent→Rule映射(在check_pair_risks()中完成)

        示例:
        ```python
        # ExecutionManager中:
        intents = risk_manager.check_all_pair_risks(pairs_with_position)
        if intents:
            executed_pair_ids = []
            for intent in intents:
                tickets = order_executor.execute_close(intent)
                if tickets:
                    executed_pair_ids.append(intent.pair_id)
                    tickets_manager.register_tickets(intent.pair_id, tickets, OrderAction.CLOSE)
            # 激活cooldown
            risk_manager.activate_cooldown_for_pairs(executed_pair_ids)
        ```

        注意:
        - 只返回触发风控的配对,未触发的不在列表中
        - 与check_pair_risks()内部调用相同逻辑,只是包装为批量接口
        - 实现Portfolio和Pair风控完全对称的架构
        """
        intents = []

        for pair in pairs_with_position.values():
            intent = self.check_pair_risks(pair)
            if intent:
                intents.append(intent)

        return intents


    def activate_cooldown_for_pairs(self, executed_pair_ids: List[Tuple]) -> None:
        """
        为已执行的Pair层Intent激活对应Rule的cooldown (v7.1.0新增)

        触发时机:
        - ExecutionManager执行完Pair风控的CloseIntent后调用
        - 传入所有成功执行的pair_id列表
        - 根据_pair_intent_to_rule_map查找对应的Rule并激活cooldown

        设计原则:
        - 去重: 同一个Rule只激活一次cooldown(即使生成了多个Intent)
        - 容错: 如果pair_id不在映射中(理论上不应该),跳过不报错
        - 日志: 每个激活的Rule都记录日志,方便追踪

        Args:
            executed_pair_ids: 已成功执行的pair_id列表
                              (ExecutionManager根据OrderTicket执行结果传入)

        示例:
        ```python
        # ExecutionManager中:
        intents = self.risk_manager.check_all_pair_risks(pairs_with_position)
        if intents:
            executed_pair_ids = []
            for intent in intents:
                tickets = self.executor.execute_close(intent)
                if tickets:
                    executed_pair_ids.append(intent.pair_id)
            # 激活cooldown
            self.risk_manager.activate_cooldown_for_pairs(executed_pair_ids)
        ```

        v7.1.0变更:
        - 新增方法,替代原check_pair_risks()中的cooldown激活逻辑
        - cooldown激活延后到Intent执行成功后,而非检测时
        - 支持部分执行场景(只为成功执行的Intent激活cooldown)

        注意:
        - Pair规则通常不使用cooldown(订单锁已提供保护)
        - 但架构上保持与Portfolio层完全对称,便于未来扩展
        """
        activated_rules = set()  # 用于去重,防止同一Rule被多次激活

        for pair_id in executed_pair_ids:
            # 查找该pair_id对应的Rule
            if pair_id in self._pair_intent_to_rule_map:
                rule = self._pair_intent_to_rule_map[pair_id]

                # 检查是否已激活过(去重)
                if rule not in activated_rules:
                    rule.activate_cooldown()
                    activated_rules.add(rule)

                    # 记录日志
                    self.algorithm.Debug(
                        f"[Pair风控] {rule.__class__.__name__} "
                        f"冷却期激活至 {rule.cooldown_until}"
                    )

        # 清空映射(防止下次OnData误用旧映射)
        self._pair_intent_to_rule_map.clear()


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
