"""
RiskManager - 风控调度器 

统一管理Portfolio和Pair层面的风控规则，检测风险并生成CloseIntent对象。
- 规则只负责检测(check)，不再提供get_action()方法
- RiskManager统一生成CloseIntent对象(reason基于Rule→Reason映射)
- cooldown激活延后到Intent执行成功后(activate_cooldown_for_portfolio/pairs)
- 完全分离: Rule检测 → RiskManager生成Intent → ExecutionManager执行 → RiskManager激活cooldown
"""

from AlgorithmImports import *
from .RiskBaseRule import RiskRule
from .PortfolioAccountBlowup import AccountBlowupRule
from .PortfolioDrawdown import PortfolioDrawdownRule
from .MarketCondition import MarketCondition
from .PairHoldingTimeout import PairHoldingTimeoutRule
from .PairAnomaly import PairAnomalyRule
from .PairDrawdown import PairDrawdownRule
from src.execution.OrderIntent import CloseIntent  
from typing import List, Tuple, Optional


class RiskManager:
    """
    风控调度器 

    职责:
    - 注册和管理所有Portfolio和Pair层面的风控规则
    - 按优先级检查规则，生成CloseIntent对象
    - 为执行成功的Intent激活对应Rule的cooldown
    - 不执行实际操作（由ExecutionManager执行）

    设计原则:
    - 配置驱动：从config自动注册规则
    - Intent生成：Rule检测 → RiskManager生成Intent → ExecutionManager执行
    - 映射机制：Rule类名→reason字符串, pair_id→Rule实例
    - 延迟cooldown：Intent执行成功后再激活cooldown
    - 优先级排序：始终返回最高优先级规则的Intent
    - 分层检查：Portfolio和Pair分开调度，API完全对称
    - check_portfolio_risks() → List[CloseIntent] (旧: (action, triggered_rules))
    - check_pair_risks(pair) → Optional[CloseIntent] (旧: (action, triggered_rules))
    - check_all_pair_risks() → List[CloseIntent] (旧: Dict[pair_id, (action, triggered)])
    - activate_cooldown_for_portfolio(executed_pair_ids) (新增)
    - activate_cooldown_for_pairs(executed_pair_ids) (新增)

    使用示例:
    ```python
    # 在main.py的Initialize()中
    from src.risk import RiskManager
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
        self.pairs_manager = pairs_manager  

        # 全局开关
        self.enabled = config.risk_management.get('enabled', True)

        # 注册Portfolio层面规则
        self.portfolio_rules = self._register_portfolio_rules()

        # 注册Pair层面规则
        self.pair_rules = self._register_pair_rules()

        # 初始化市场条件检查器（独立于风控规则）
        self.market_condition = MarketCondition(algorithm, config)

        # Rule类名 → CloseIntent的reason字符串
        self._portfolio_rule_to_reason_map = {
            'AccountBlowupRule': 'PORTFOLIO BLOW UP',
            'PortfolioDrawdownRule': 'PORTFOLIO DRAWDOWN',
        }

        # v7.1.0 新增: Pair层映射机制(Intent Pattern)
        # Rule类名 → CloseIntent的reason字符串
        self._pair_rule_to_reason_map = {
            'PairHoldingTimeoutRule': 'PAIR TIMEOUT',
            'PairAnomalyRule': 'PAIR ANOMALY',
            'PairDrawdownRule': 'PAIR DRAWDOWN',
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
            'portfolio_drawdown': PortfolioDrawdownRule,
        }

        # 从config读取规则配置
        portfolio_rule_configs = self.config.risk_management['portfolio_rules']

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
        - PairHoldingTimeoutRule: 持仓超时检测
        - (PairAnomalyRule: Step 2实现)
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
            'holding_timeout': PairHoldingTimeoutRule,
            'pair_anomaly': PairAnomalyRule,
            'pair_drawdown': PairDrawdownRule,
        }

        # 从config读取规则配置
        pair_rule_configs = self.config.risk_management['pair_rules']

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


    def check_portfolio_risks(self) -> Tuple[List[CloseIntent], Optional[RiskRule]]:
        """
        检查Portfolio层面风控,返回需要平仓的CloseIntent列表和触发的规则

        调度逻辑:
        1. 检查全局enabled开关
        2. 遍历所有Portfolio规则(已按priority降序排序)
        3. 跳过disabled规则
        4. 调用rule.check()检测是否触发
        5. 如果触发,生成所有持仓的CloseIntent
        6. **立即返回Intent列表和触发的规则**(排他性:只执行最高优先级规则)
        7. 如果所有规则都未触发,返回空列表和None

        排他性设计:
        - Portfolio规则按priority降序排序(优先级高的先检查)
        - 第一个触发的规则生成Intent后**立即返回**
        - 不会继续检查优先级更低的规则
        - 例如:AccountBlowup(priority=100)触发后,不会再检查PortfolioDrawdown(priority=90)
        - 理由:Portfolio规则都是全局性的(清仓/停止交易),执行一个就足够

        Returns:
            Tuple[List[CloseIntent], Optional[RiskRule]]:
            - List[CloseIntent]: 需要平仓的Intent列表,空列表表示未触发
            - Optional[RiskRule]: 触发的规则实例,None表示未触发
        - 返回值从(action, triggered_rules)改为List[CloseIntent]
        - RiskManager负责生成Intent(不再依赖Rule.get_action())
        - 返回值改为Tuple[List[CloseIntent], Optional[RiskRule]]
        - 移除Intent→Rule映射机制(不再需要)
        - 移除重复的cooldown检查(main.py已前置检查)
        - 简化cooldown激活逻辑(直接传递触发的规则)

        使用示例:
            intents, triggered_rule = self.risk_manager.check_portfolio_risks()
            if intents and triggered_rule:
                self.execution_manager.handle_portfolio_risk_intents(intents, triggered_rule, self.risk_manager)
        """
        # 全局禁用时直接返回
        if not self.enabled:
            return [], None

        # 遍历所有Portfolio规则(已按priority降序排序)
        for rule in self.portfolio_rules:
            # 跳过禁用的规则
            if not rule.enabled:
                continue

            try:
                triggered, description = rule.check()
                if triggered:
                    self.algorithm.Debug(
                        f"[Portfolio风控] {rule.__class__.__name__} "
                        f"(priority={rule.priority}): {description}"
                    )

                    # 获取reason字符串
                    reason = self._portfolio_rule_to_reason_map.get(
                        rule.__class__.__name__,
                        rule.__class__.__name__
                    )

                    # 生成所有持仓的CloseIntent
                    pairs = self.pairs_manager.get_pairs_with_position()
                    intents = []
                    for pair in pairs.values():
                        intent = pair.get_close_intent(reason=reason)
                        if intent:
                            intents.append(intent)

                    # Portfolio规则触发后立即返回(排他性:最高优先级)
                    if intents:
                        self.algorithm.Debug(
                            f"[Portfolio风控] 生成{len(intents)}个CloseIntent"
                        )
                        return intents, rule

            except Exception as e:
                self.algorithm.Debug(
                    f"[RiskManager] Portfolio规则检查异常 {rule.__class__.__name__}: {str(e)}"
                )

        # 没有规则触发,返回空列表和None
        return [], None


    def activate_cooldown_for_portfolio(self, triggered_rule: RiskRule) -> None:
        """
        为触发的Portfolio规则激活cooldown

        触发时机:
        - ExecutionManager执行完Portfolio风控的CloseIntent后调用
        - 直接传入触发的规则实例

        设计原则:
        - 简化参数: 直接传入规则实例,不再通过pair_id映射查找
        - 语义清晰: Portfolio规则是全局性的,与具体pair_id无关
        - 职责单一: 只负责激活cooldown,不负责映射管理
        - 无条件激活: 无论订单执行成功与否,都激活cooldown(防止继续交易)

        Args:
            triggered_rule: 触发的规则实例

        示例:
        ```python
        # ExecutionManager中:
        intents, triggered_rule = self.risk_manager.check_portfolio_risks()
        if intents and triggered_rule:
            # 执行平仓Intent(尝试清空所有持仓)
            self.executor.execute_intents(intents)
            # 无论成功与否,都激活cooldown
            self.risk_manager.activate_cooldown_for_portfolio(triggered_rule)
        ```
        - 参数从 List[Tuple] 改为 RiskRule
        - 移除 pair_id→rule 映射查找逻辑
        - 移除去重逻辑(单个规则实例,无需去重)
        - 移除映射清理逻辑(不再维护映射)
        - 无条件激活(不再依赖执行成功与否)
        """
        if triggered_rule is None:
            return  # 防御性检查

        # 直接激活规则的cooldown
        triggered_rule.activate_cooldown()

        # 记录日志
        self.algorithm.Debug(
            f"[Portfolio风控] {triggered_rule.__class__.__name__} "
            f"冷却期激活至 {triggered_rule.cooldown_until}"
        )


    def is_portfolio_in_risk_cooldown(self) -> bool:
        """
        检查Portfolio是否在风险冷却期

        用途:
        在OnData中检查，确保冷却期内不执行任何交易逻辑。
        这是防止风控触发后继续交易的关键机制。

        Returns:
            True: Portfolio在风险冷却期，应阻止所有交易
            False: Portfolio不在冷却期，可以继续交易

        示例:
        ```python
        # 在main.py的OnData()中
        if self.risk_manager.is_portfolio_in_risk_cooldown():
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
        清理配对的 HWM 状态 

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



    def check_pair_risks(self, pair) -> Optional[CloseIntent]:
        """
        检查Pair层面风控,返回CloseIntent或None

        调度逻辑:
        1. 检查全局enabled开关
        2. 遍历所有Pair规则（已按priority降序排序）
        3. Rule.check()内部检查该配对的cooldown状态
        4. 找到第一个触发的规则立即返回Intent（排他性: 最高优先级）
        5. 生成CloseIntent并记录映射(用于后续cooldown激活)

        Args:
            pair: Pairs对象,必须包含pair_opened_time等属性

        Returns:
            Optional[CloseIntent]:
            - CloseIntent对象: 包含pair_id, symbols, quantities, reason, tag
            - None: 未触发任何规则

        排他性设计:
        - 同一配对多规则触发: 只执行最高优先级规则
        - 不同配对独立检查: (AAPL,MSFT)和(GOOGL,META)可触发不同规则
        - Cooldown作用域: per-pair (只影响触发的配对,不影响其他配对)

        示例:
        - (AAPL,MSFT)同时满足Anomaly+Timeout → 只触发Anomaly (priority=100)
        - (AAPL,MSFT)触发Drawdown → 该配对30天内不再触发Drawdown
        - (GOOGL,META)仍可触发Drawdown (不受影响)
        - 返回类型从 (action, triggered_rules) 改为 Optional[CloseIntent]
        - Rule不再提供get_action(),RiskManager统一生成Intent
        - 触发时立即返回(最高优先级),不再收集所有触发规则
        - 记录Intent→Rule映射,供activate_cooldown_for_pairs()使用
        - Rule.check()内部检查per-pair cooldown (不再需要外部检查)
        - 注释更新: 明确排他性作用域和cooldown作用域

        注意:
        - 本方法不修改任何状态,纯粹的检查和返回
        - Intent执行后由ExecutionManager调用activate_cooldown_for_pairs()

        当前支持的规则:
        - PairAnomalyRule: 配对异常检测 (priority=100)
        - PairHoldingTimeoutRule: 持仓超时检测 (priority=60)
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

            # Rule.check()内部会检查per-pair cooldown
            try:
                triggered, description = rule.check(pair=pair)
                if triggered:
                    # 找到触发规则,立即生成Intent并返回(排他性: 最高优先级)
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



    def activate_cooldown_for_pairs(self, executed_pair_ids: List[Tuple]) -> None:
        """
        为已执行的Pair层Intent激活对应Rule的per-pair cooldown

        触发时机:
        - ExecutionManager执行完Pair风控的CloseIntent后调用
        - 传入所有成功执行的pair_id列表
        - 根据_pair_intent_to_rule_map查找对应的Rule并激活per-pair cooldown

        设计原则:
        - Per-Pair Cooldown: rule.activate_cooldown(pair_id) 只冷却该配对
        - 不去重Rule: 同一Rule可能为多个配对激活cooldown (例如: (AAPL,MSFT)和(GOOGL,META)都触发PairDrawdownRule)
        - 容错: 如果pair_id不在映射中(理论上不应该),跳过不报错
        - 批量日志: 按Rule汇总显示哪些配对被激活了cooldown

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
            # 激活per-pair cooldown
            self.risk_manager.activate_cooldown_for_pairs(executed_pair_ids)
        ```
        - 新增方法,替代原check_pair_risks()中的cooldown激活逻辑
        - cooldown激活延后到Intent执行成功后,而非检测时
        - 支持部分执行场景(只为成功执行的Intent激活cooldown)
        - 从全局cooldown改为per-pair cooldown
        - 移除Rule去重逻辑(同一Rule可对多个配对激活cooldown)
        - 批量日志输出,按Rule汇总pair_id列表
        """
        activated_rules = {}  # {rule: [(pair_id, cooldown_days)]} 用于批量日志

        for pair_id in executed_pair_ids:
            # 查找该pair_id对应的Rule
            if pair_id in self._pair_intent_to_rule_map:
                rule = self._pair_intent_to_rule_map[pair_id]

                # v7.3.1: PairDrawdownRule支持动态冷却期
                if rule.__class__.__name__ == 'PairDrawdownRule':
                    # 获取pair对象
                    pair = self.pairs_manager.get_pair_by_id(pair_id)
                    if pair:
                        # 根据PnL状态动态确定冷却期
                        cooldown_days = rule.get_cooldown_days(pair)
                        rule.activate_cooldown(pair_id=pair_id, days=cooldown_days)

                        # 记录用于批量日志
                        if rule not in activated_rules:
                            activated_rules[rule] = []
                        activated_rules[rule].append((pair_id, cooldown_days))
                    else:
                        # 容错: 找不到pair对象时使用默认值
                        rule.activate_cooldown(pair_id=pair_id)
                        if rule not in activated_rules:
                            activated_rules[rule] = []
                        activated_rules[rule].append((pair_id, None))
                else:
                    # 其他Rule使用默认cooldown_days
                    rule.activate_cooldown(pair_id=pair_id)

                    # 记录用于批量日志
                    if rule not in activated_rules:
                        activated_rules[rule] = []
                    cooldown_days = rule.config.get('cooldown_days', 0)
                    activated_rules[rule].append((pair_id, cooldown_days))

        # 批量日志输出 (v7.3.1: 支持per-pair显示冷却期)
        for rule, pair_cooldowns in activated_rules.items():
            if rule.__class__.__name__ == 'PairDrawdownRule':
                # PairDrawdownRule: 显示每个配对的冷却期(可能不同)
                pairs_str = ", ".join(
                    f"{pair_id}({days}天)" for pair_id, days in pair_cooldowns
                )
                self.algorithm.Debug(
                    f"[Pair风控] {rule.__class__.__name__} 激活{len(pair_cooldowns)}个配对的动态冷却期: {pairs_str}"
                )
            else:
                # 其他Rule: 统一冷却期
                pair_ids = [pair_id for pair_id, _ in pair_cooldowns]
                cooldown_days = pair_cooldowns[0][1] if pair_cooldowns else 0
                self.algorithm.Debug(
                    f"[Pair风控] {rule.__class__.__name__} 激活{len(pair_ids)}个配对的冷却期 "
                    f"({cooldown_days}天): {pair_ids}"
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
