"""
ExecutionManager - 统一执行器 (v7.0.0: Intent模式重构)

职责:
- 执行风控响应(Portfolio和Pair层面)
- 执行正常交易(信号驱动的开仓和平仓)
- 协调 Pairs(意图生成) + OrderExecutor(订单执行)
- 与 pairs_manager, tickets_manager, order_executor 交互

设计原则:
- 职责单一: 只负责协调,不负责检测或信号生成
- 依赖注入: 通过构造函数注入所需依赖
- Intent模式: Pairs生成意图,OrderExecutor执行订单
- 统一接口: 风控和正常交易统一管理
"""

from AlgorithmImports import *
from src.Pairs import OrderAction, TradingSignal
from src.execution.OrderIntent import CloseIntent  # v7.1.0: Intent Pattern
from typing import List


class ExecutionManager:
    """
    统一执行器 (v7.0.0: Intent模式重构)

    负责协调所有交易动作,包括:
    - 风控执行: Portfolio层面(全部清仓、减仓等) + Pair层面(配对平仓)
    - 正常交易: 信号驱动的开仓和平仓

    设计特点(v7.0.0):
    - 与RiskManager配合使用(检测与执行分离)
    - 与Pairs配合使用(信号生成与执行分离)
    - 与OrderExecutor配合使用(意图与执行分离)
    - 依赖注入: algorithm, pairs_manager, tickets_manager, order_executor
    - 完全统一的执行接口
    """

    def __init__(self, algorithm, pairs_manager, tickets_manager, order_executor):
        """
        初始化统一执行器 (v7.0.0: 新增order_executor依赖)

        Args:
            algorithm: QuantConnect算法实例
            pairs_manager: 配对管理器
            tickets_manager: 订单追踪管理器
            order_executor: 订单执行器(v7.0.0新增)
        """
        self.algorithm = algorithm
        self.pairs_manager = pairs_manager
        self.tickets_manager = tickets_manager
        self.order_executor = order_executor


    def handle_portfolio_risk_action(self, action: str, triggered_rules: list):
        """
        处理Portfolio层面风控动作

        Args:
            action: 风控动作 ('portfolio_liquidate_all'等)
            triggered_rules: 触发的规则列表 [(rule, description), ...]

        执行流程:
        1. 记录所有触发规则的详细日志
        2. 根据action字符串分发到具体处理方法
        3. 激活所有触发规则的冷却期

        注意:
        - 当前只实现'portfolio_liquidate_all'(全部清仓)
        - 其他action作为占位,未来实现
        """
        # 记录所有触发的规则
        for rule, description in triggered_rules:
            self.algorithm.Debug(f"[风控触发] {rule.__class__.__name__}: {description}")

        # 根据action执行相应操作
        if action == 'portfolio_liquidate_all':
            self.liquidate_all_positions()

        elif action == 'portfolio_reduce_exposure_50':
            # 未来实现: 减仓50%
            self.algorithm.Debug(f"[风控] 减仓50%模式(暂未实现)")

        elif action == 'portfolio_rebalance_sectors':
            # 未来实现: 行业再平衡
            self.algorithm.Debug(f"[风控] 行业再平衡模式(暂未实现)")

        else:
            self.algorithm.Debug(f"[风控] 未知动作: {action}")

        # 激活所有触发规则的冷却期
        for rule, _ in triggered_rules:
            rule.activate_cooldown()
            self.algorithm.Debug(f"[风控] {rule.__class__.__name__} 冷却至 {rule.cooldown_until}")


    def handle_portfolio_risk_intents(self, intents: List[CloseIntent], risk_manager) -> None:
        """
        处理Portfolio层面风控Intent列表 (v7.1.0 Intent Pattern)

        Args:
            intents: CloseIntent列表（所有持仓配对的平仓Intent）
            risk_manager: RiskManager实例（用于激活cooldown）

        执行流程:
        1. 记录Portfolio风控触发信息
        2. 遍历所有Intent，检查订单锁状态
        3. 通过order_executor执行平仓Intent
        4. 注册订单到tickets_manager
        5. 记录成功执行的pair_id列表
        6. 调用risk_manager激活触发规则的cooldown

        v7.1.0设计原则:
        - Intent统一执行：Portfolio和Pair使用相同的Intent执行逻辑
        - cooldown延迟激活：由RiskManager在Intent执行成功后激活
        - 订单追踪完整：所有订单都通过tickets_manager追踪
        - 失败容错：部分订单失败不影响其他订单，只为成功的激活cooldown

        注意:
        - 替代旧的handle_portfolio_risk_action(action, triggered_rules)
        - 不再需要action字符串分发逻辑
        - cooldown激活由RiskManager统一管理
        """
        self.algorithm.Debug(f"[Portfolio风控] 触发Intent执行: 共{len(intents)}个配对需要平仓")

        executed_pair_ids = []  # 记录成功执行的pair_id

        for intent in intents:
            # 订单锁定检查（防止重复下单）
            if self.tickets_manager.is_pair_locked(intent.pair_id):
                self.algorithm.Debug(
                    f"[Portfolio风控] {intent.pair_id} 订单处理中,跳过"
                )
                continue

            # 通过order_executor执行平仓Intent
            tickets = self.order_executor.execute_close(intent)
            if tickets:
                # 注册订单到tickets_manager
                self.tickets_manager.register_tickets(
                    intent.pair_id,
                    tickets,
                    OrderAction.CLOSE
                )
                executed_pair_ids.append(intent.pair_id)
                self.algorithm.Debug(
                    f"[Portfolio风控] {intent.pair_id} 平仓订单已提交 (reason={intent.reason})"
                )
            else:
                self.algorithm.Error(
                    f"[Portfolio风控] {intent.pair_id} 平仓失败"
                )

        # 激活触发规则的cooldown（只为成功执行的Intent激活）
        if executed_pair_ids:
            risk_manager.activate_cooldown_for_portfolio(executed_pair_ids)
            self.algorithm.Debug(
                f"[Portfolio风控] 完成: 成功平仓{len(executed_pair_ids)}/{len(intents)}个配对"
            )
        else:
            self.algorithm.Debug(f"[Portfolio风控] 所有配对平仓失败或被跳过")


    def handle_pair_risk_intents(self, intents: List[CloseIntent], risk_manager) -> None:
        """
        处理Pair层面风控Intent列表 (v7.1.0 Intent Pattern重构)

        Args:
            intents: CloseIntent列表（触发风控的配对）
            risk_manager: RiskManager实例（用于激活cooldown和清理HWM）

        执行流程:
        1. 记录Pair风控触发信息
        2. 遍历所有Intent，检查订单锁状态
        3. 通过order_executor执行平仓Intent
        4. 注册订单到tickets_manager
        5. 记录成功执行的pair_id列表
        6. 调用risk_manager激活触发规则的cooldown
        7. 调用risk_manager清理平仓配对的HWM

        v7.1.0设计原则:
        - 与Portfolio风控完全对称的Intent执行逻辑
        - cooldown延迟激活：由RiskManager在Intent执行成功后激活
        - HWM自动清理：平仓后立即清理PairDrawdownRule的HWM状态
        - 订单追踪完整：所有订单都通过tickets_manager追踪
        - 失败容错：部分订单失败不影响其他订单

        注意:
        - 替代旧的handle_pair_risk_actions(pair_risk_actions: dict)
        - 不再需要action字符串判断逻辑
        - cooldown和HWM管理统一由RiskManager负责
        """
        if not intents:
            return

        self.algorithm.Debug(f"[Pair风控] 触发Intent执行: 共{len(intents)}个配对需要平仓")

        executed_pair_ids = []  # 记录成功执行的pair_id

        for intent in intents:
            # 订单锁定检查（防止重复下单）
            if self.tickets_manager.is_pair_locked(intent.pair_id):
                self.algorithm.Debug(
                    f"[Pair风控] {intent.pair_id} 订单处理中,跳过"
                )
                continue

            # 通过order_executor执行平仓Intent
            tickets = self.order_executor.execute_close(intent)
            if tickets:
                # 注册订单到tickets_manager
                self.tickets_manager.register_tickets(
                    intent.pair_id,
                    tickets,
                    OrderAction.CLOSE
                )
                executed_pair_ids.append(intent.pair_id)
                self.algorithm.Debug(
                    f"[Pair风控] {intent.pair_id} 平仓订单已提交 (reason={intent.reason})"
                )

                # 清理该配对的HWM状态（PairDrawdownRule）
                risk_manager.cleanup_pair_hwm(intent.pair_id)

            else:
                self.algorithm.Error(
                    f"[Pair风控] {intent.pair_id} 平仓失败"
                )

        # 激活触发规则的cooldown（只为成功执行的Intent激活）
        if executed_pair_ids:
            risk_manager.activate_cooldown_for_pairs(executed_pair_ids)
            self.algorithm.Debug(
                f"[Pair风控] 完成: 成功平仓{len(executed_pair_ids)}/{len(intents)}个配对"
            )
        else:
            self.algorithm.Debug(f"[Pair风控] 所有配对平仓失败或被跳过")


    def handle_pair_risk_actions(self, pair_risk_actions: dict):
        """
        处理Pair层面风控动作 (Deprecated - 保留向后兼容)

        ⚠️ Deprecated in v7.1.0: 请使用handle_pair_risk_intents()

        Args:
            pair_risk_actions: {pair_id: (action, triggered_rules), ...}
        """
        self.algorithm.Debug("[Deprecated] handle_pair_risk_actions() 已废弃,请使用 handle_pair_risk_intents()")

        # 为向后兼容，保留基本功能
        for pair_id, (action, triggered_rules) in pair_risk_actions.items():
            pair = self.pairs_manager.get_pair_by_id(pair_id)
            if self.tickets_manager.is_pair_locked(pair_id):
                continue

            if action == 'pair_close':
                self.algorithm.Debug(f"[Pair风控] {pair_id} 触发平仓风控")
                intent = pair.get_close_intent(reason='RISK_TRIGGER')
                if intent:
                    self.order_executor.execute_close(intent)
                    for _, desc in triggered_rules:
                        self.algorithm.Debug(f"  └─ {desc}")


    def liquidate_all_positions(self):
        """
        清空所有持仓(仅通过pairs_manager,不使用Liquidate)

        执行流程:
        1. 获取所有有持仓的配对
        2. 检查订单锁定状态(is_pair_locked)
        3. 通过pair.get_close_intent()生成意图,order_executor执行(保持订单追踪)
        4. 注册订单到tickets_manager
        5. 记录详细日志

        设计决策:
        - 不使用QC的Liquidate()方法,原因:
          1. 绕过Intent模式和订单追踪
          2. 破坏tickets_manager订单追踪体系
          3. 可能导致重复下单
        - 只通过pairs_manager管理的配对进行平仓
        - 保持TicketsManager的订单追踪完整性
        """
        self.algorithm.Debug(f"[风控清仓] 开始清空所有持仓...")

        # 获取所有有持仓的配对
        pairs_with_position = self.pairs_manager.get_pairs_with_position()

        if not pairs_with_position:
            self.algorithm.Debug(f"[风控清仓] 无持仓,跳过")
            return

        closed_count = 0
        for pair in pairs_with_position.values():
            # 订单锁定检查(防止重复下单)
            if self.tickets_manager.is_pair_locked(pair.pair_id):
                self.algorithm.Debug(f"[风控清仓] {pair.pair_id} 订单处理中,跳过")
                continue

            # 通过pair平仓(保持订单追踪) - v7.0.0: Intent模式, v7.1.0: 自动注册简化
            intent = pair.get_close_intent(reason='RISK_TRIGGER')
            if intent:
                self.order_executor.execute_close(intent)  # 自动注册,无返回值
                closed_count += 1
                self.algorithm.Debug(f"[风控清仓] {pair.pair_id} 已提交平仓订单")

        self.algorithm.Debug(f"[风控清仓] 完成: 平仓{closed_count}/{len(pairs_with_position)}个配对")


    # ===== 正常交易执行方法 =====

    def handle_signal_closings(self, pairs_with_position, data):
        """
        处理信号驱动的正常平仓

        职责: 主动交易管理(非风控)

        Args:
            pairs_with_position: 有持仓的配对字典 {pair_id: Pairs}
            data: 数据切片

        执行流程:
        1. 遍历所有有持仓配对
        2. 检查订单锁(跳过风控已处理或订单执行中的配对)
        3. 获取交易信号(pair.get_signal(data))
        4. 处理CLOSE和STOP_LOSS信号
        5. 生成Intent并通过order_executor执行,注册订单

        设计特点:
        - 完全独立于风控平仓
        - 自动跳过风控已处理的配对(通过订单锁)
        - 只负责执行,信号生成由Pairs负责
        """
        for pair in pairs_with_position.values():
            # 订单锁定检查(跳过已被风控处理或订单执行中的配对)
            if self.tickets_manager.is_pair_locked(pair.pair_id):
                continue

            # 获取交易信号
            signal = pair.get_signal(data)

            # 处理平仓信号 (v7.0.0: Intent模式, v7.1.0: 自动注册简化)
            if signal == TradingSignal.CLOSE:
                self.algorithm.Debug(f"[平仓] {pair.pair_id} Z-score回归")
                intent = pair.get_close_intent(reason='CLOSE')
                if intent:
                    self.order_executor.execute_close(intent)  # 自动注册,无返回值

            elif signal == TradingSignal.STOP_LOSS:
                self.algorithm.Debug(f"[止损] {pair.pair_id} Z-score超限")
                intent = pair.get_close_intent(reason='STOP_LOSS')
                if intent:
                    self.order_executor.execute_close(intent)  # 自动注册,无返回值


    def get_entry_candidates(self, pairs_without_position: dict, data) -> list:
        """
        获取所有有开仓信号的配对，按质量分数降序排序

        从 PairsManager.get_sequenced_entry_candidates() 迁移而来 (v6.9.3)

        职责: ExecutionManager 负责"执行准备"逻辑
        - 遍历配对获取信号（调用 Pairs.get_signal()）
        - 过滤开仓信号
        - 计算计划分配比例
        - 按质量分数排序

        Args:
            pairs_without_position: 无持仓的可交易配对字典 {pair_id: Pairs对象}
            data: 数据切片

        Returns:
            List[(pair, signal, quality_score, planned_pct), ...] 按质量降序排序

        设计理念:
            - 信号聚合属于"执行准备"，不属于"配对管理"
            - 调用 pair.get_signal() 等业务逻辑是执行器的职责
            - PairsManager 只负责存储和分类，不应调用业务逻辑
        """
        candidates = []

        for pair in pairs_without_position.values():
            signal = pair.get_signal(data)
            if signal in [TradingSignal.LONG_SPREAD, TradingSignal.SHORT_SPREAD]:
                planned_pct = pair.get_planned_allocation_pct()
                candidates.append((pair, signal, pair.quality_score, planned_pct))

        # 按质量分数降序排序
        candidates.sort(key=lambda x: x[2], reverse=True)
        return candidates


    def handle_position_openings(self, pairs_without_position, data):
        """
        处理正常开仓逻辑

        职责: 资金管理和开仓执行

        Args:
            pairs_without_position: 无持仓配对字典 {pair_id: Pairs}
            data: 数据切片

        执行流程:
        1. 获取开仓候选(get_entry_candidates) - v6.9.3更新
        2. 计算可用保证金(MarginRemaining * 0.95)
        3. 动态分配保证金给各配对(质量分数驱动)
        4. 逐个执行开仓(三重检查: 订单锁/最小投资/保证金充足)
        5. 注册订单到tickets_manager

        设计特点:
        - 完整的资金管理逻辑
        - 动态缩放保证金分配(公平性)
        - 质量分数驱动的分配比例
        """
        # 获取开仓候选(已按质量降序) - v6.9.3: 改为调用自己的方法
        entry_candidates = self.get_entry_candidates(pairs_without_position, data)
        if not entry_candidates:
            return

        # 使用95%的MarginRemaining,保留5%作为动态缓冲
        config = self.algorithm.config.pairs_trading
        initial_margin = self.algorithm.Portfolio.MarginRemaining * config['margin_usage_ratio']
        buffer = self.algorithm.Portfolio.MarginRemaining * (1 - config['margin_usage_ratio'])

        # 检查是否低于最小阈值
        if initial_margin < self.algorithm.min_investment:
            return

        # 提取计划分配
        planned_allocations = {}  # {pair_id: pct}
        opening_signals = {}      # {pair_id: signal}

        for pair, signal, _, pct in entry_candidates:
            planned_allocations[pair.pair_id] = pct
            opening_signals[pair.pair_id] = signal

        # === 逐个开仓(动态缩放保持公平) ===
        actual_opened = 0
        for pair_id, pct in planned_allocations.items():
            pair = self.pairs_manager.get_pair_by_id(pair_id)
            signal = opening_signals[pair_id]

            # 动态缩放计算
            current_margin = (self.algorithm.Portfolio.MarginRemaining - buffer) * pct
            if current_margin <= 0:
                break

            scale_factor = initial_margin / (self.algorithm.Portfolio.MarginRemaining - buffer)
            amount_allocated = current_margin * scale_factor

            # 检查1: 订单锁定检查
            if self.tickets_manager.is_pair_locked(pair_id):
                continue

            # 检查2: 最小投资额
            if amount_allocated < self.algorithm.min_investment:
                continue

            # 检查3: 资金是否仍足够
            available_for_check = self.algorithm.Portfolio.MarginRemaining - buffer
            if amount_allocated > available_for_check:
                self.algorithm.Debug(f"[开仓失败] {pair_id} 资金不足: 需要${amount_allocated:,.0f}, 可用${available_for_check:,.0f}")
                continue

            # 执行开仓并注册订单追踪 (v7.0.0: Intent模式, v7.1.0: 自动注册简化为2行)
            intent = pair.get_open_intent(amount_allocated, data)
            if intent:
                self.order_executor.execute_open(intent)  # 自动注册,无返回值
                actual_opened += 1

        # 执行结果
        if actual_opened > 0:
            self.algorithm.Debug(f"[开仓] 成功开仓{actual_opened}/{len(entry_candidates)}个配对")
