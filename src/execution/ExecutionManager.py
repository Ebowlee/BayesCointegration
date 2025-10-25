"""
ExecutionManager - 统一执行器 (Intent模式)

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
from src.constants import OrderAction, TradingSignal
from src.execution.OrderIntent import CloseIntent
from typing import List


class ExecutionManager:
    """
    统一执行器 (Intent模式)

    负责协调所有交易动作,包括:
    - 风控执行: Portfolio层面(全部清仓、减仓等) + Pair层面(配对平仓)
    - 正常交易: 信号驱动的开仓和平仓

    设计特点:
    - 与RiskManager配合使用(检测与执行分离)
    - 与Pairs配合使用(信号生成与执行分离)
    - 与OrderExecutor配合使用(意图与执行分离)
    - 依赖注入: algorithm, pairs_manager, tickets_manager, order_executor
    - 完全统一的执行接口
    """

    def __init__(self, algorithm, pairs_manager, risk_manager, tickets_manager, order_executor, margin_allocator):
        """
        初始化统一执行器

        Args:
            algorithm: QuantConnect算法实例
            pairs_manager: 配对管理器
            risk_manager: 风控管理器(用于cooldown检查)
            tickets_manager: 订单追踪管理器
            order_executor: 订单执行器
            margin_allocator: 资金分配器
        """
        self.algorithm = algorithm
        self.pairs_manager = pairs_manager
        self.risk_manager = risk_manager
        self.tickets_manager = tickets_manager
        self.order_executor = order_executor
        self.margin_allocator = margin_allocator

        # 从margin_allocator获取min_investment_amount（避免重复计算）
        self.min_investment_amount = margin_allocator.min_investment_amount


    # ===== Cooldown检查方法 =====

    def is_pair_in_risk_cooldown(self, pair_id: tuple) -> bool:
        """
        检查配对是否在风险冷却期

        风险冷却期由风险规则激活(30天):
        - PairDrawdownRule: 配对回撤触发
        - PairHoldingTimeoutRule: 持仓超时触发
        - PairAnomalyRule: 仓位异常触发

        实现原理:
        - 遍历RiskManager的所有Pair规则
        - 调用每个规则的is_in_cooldown(pair_id)检查
        - 任一规则返回True,则该配对不可开仓

        Args:
            pair_id: 配对标识符

        Returns:
            True: 在冷却期
            False: 不在冷却期

        设计特点:
        - 依赖注入: 通过self.risk_manager访问规则列表
        - 统一接口: 与is_portfolio_in_risk_cooldown()对称
        - 决策集中: cooldown判断逻辑集中在ExecutionManager
        """
        for rule in self.risk_manager.pair_rules:
            if rule.is_in_cooldown(pair_id=pair_id):
                return True
        return False


    def is_pair_in_normal_cooldown(self, pair) -> bool:
        """
        检查配对是否在普通交易冷却期

        普通冷却期由正常平仓激活(10天):
        - 信号回归平仓 (CLOSE)
        - 止损平仓 (STOP_LOSS)

        实现原理:
        - Pairs对象存储 pair_closed_time 和 cooldown_days
        - 通过 get_pair_frozen_days() 查询已冷却天数
        - 如果 frozen_days < cooldown_days, 则仍在冷却期

        Args:
            pair: Pairs对象

        Returns:
            True: 在冷却期
            False: 不在冷却期或无冷却期数据

        设计特点:
        - 数据所有权: Pairs拥有pair_closed_time数据
        - 决策职责: ExecutionManager负责判断逻辑
        - 分离关注: 数据查询(Pairs) vs 决策(ExecutionManager)
        """
        frozen_days = pair.get_pair_frozen_days()
        if frozen_days is None:
            return False  # 无冷却期数据,允许开仓
        return frozen_days < pair.cooldown_days


    # ===== 风控执行方法 =====

    def handle_portfolio_risk_intents(self, intents: List[CloseIntent], triggered_rule, risk_manager) -> None:
        """
        处理Portfolio层面风控Intent列表

        Args:
            intents: CloseIntent列表（所有持仓配对的平仓Intent）
            triggered_rule: 触发的规则实例
            risk_manager: RiskManager实例（用于激活cooldown）

        执行流程:
        1. 记录Portfolio风控触发信息
        2. 遍历所有Intent，检查订单锁状态
        3. 通过order_executor执行平仓Intent
        4. 注册订单到tickets_manager
        5. 记录成功执行的配对数量
        6. 无论成功与否，调用risk_manager激活cooldown（防止继续交易）
        """
        self.algorithm.Debug(f"[Portfolio风控] 触发Intent执行: 共{len(intents)}个配对需要平仓")

        executed_count = 0  # 记录成功执行的配对数量

        for intent in intents:
            # 订单锁定检查（防止重复下单）
            if self.tickets_manager.is_pair_locked(intent.pair_id):
                self.algorithm.Debug(
                    f"[Portfolio风控] {intent.pair_id} 订单处理中,跳过"
                )
                continue

            # 通过order_executor执行平仓Intent (自动注册到TicketsManager)
            success = self.order_executor.execute_close(intent)
            if success:
                executed_count += 1
                self.algorithm.Debug(
                    f"[Portfolio风控] {intent.pair_id} 平仓订单已提交 (reason={intent.reason})"
                )
            else:
                self.algorithm.Error(
                    f"[Portfolio风控] {intent.pair_id} 平仓失败 (无持仓)"
                )

        # 无论成功与否,都激活cooldown（防止继续交易）
        risk_manager.activate_cooldown_for_portfolio(triggered_rule)

        # 执行结果汇报
        self.algorithm.Debug(
            f"[Portfolio风控] 完成: 成功平仓{executed_count}/{len(intents)}个配对"
        )


    def handle_pair_risk_intents(self, intents: List[CloseIntent], risk_manager) -> None:
        """
        处理Pair层面风控Intent列表

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

        设计原则:
        - 与Portfolio风控完全对称的Intent执行逻辑
        - cooldown延迟激活：由RiskManager在Intent执行成功后激活
        - HWM自动清理：平仓后立即清理PairDrawdownRule的HWM状态
        - 订单追踪完整：所有订单都通过tickets_manager追踪
        - 失败容错：部分订单失败不影响其他订单
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

            # 通过order_executor执行平仓Intent (自动注册到TicketsManager)
            success = self.order_executor.execute_close(intent)
            if success:
                executed_pair_ids.append(intent.pair_id)
                self.algorithm.Debug(
                    f"[Pair风控] {intent.pair_id} 平仓订单已提交 (reason={intent.reason})"
                )

                # 清理该配对的HWM状态（PairDrawdownRule）
                risk_manager.cleanup_pair_hwm(intent.pair_id)

            else:
                self.algorithm.Error(
                    f"[Pair风控] {intent.pair_id} 平仓失败 (无持仓)"
                )

        # 激活触发规则的cooldown（只为成功执行的Intent激活）
        if executed_pair_ids:
            risk_manager.activate_cooldown_for_pairs(executed_pair_ids)
            self.algorithm.Debug(
                f"[Pair风控] 完成: 成功平仓{len(executed_pair_ids)}/{len(intents)}个配对"
            )
        else:
            self.algorithm.Debug(f"[Pair风控] 所有配对平仓失败或被跳过")


    def cleanup_remaining_positions(self):
        """
        清理cooldown期间的残留持仓

        触发时机:
        - Portfolio风控触发后,进入cooldown期间
        - 每次OnData检查到cooldown状态时调用

        清理逻辑:
        1. 检查是否有残留持仓(get_pairs_with_position)
        2. 遍历所有残留持仓,检查订单锁
        3. 通过Intent模式尝试平仓(保持订单追踪)
        4. 统计并报告清理数量

        设计原则:
        - 遵循Intent模式: 通过pair.get_close_intent()生成意图
        - 保持订单追踪: 注册到tickets_manager
        - 尊重订单锁: 跳过处理中的订单
        - 持续重试: 每个OnData周期都会尝试清理

        典型场景:
        - Portfolio风控触发,10个配对中8个平仓成功,2个失败
        - 进入cooldown后,每个bar都会尝试清理这2个残留持仓
        - 直到全部清理完成或cooldown到期

        reason标记:
        - 使用'COOLDOWN_CLEANUP'标记,区别于原始风控触发
        - 便于TradeJournal追踪重试记录
        """
        pairs_with_position = self.pairs_manager.get_pairs_with_position()
        if not pairs_with_position:
            return  # 没有残留持仓,无需清理

        self.algorithm.Debug(
            f"[Cooldown清理] 检测到{len(pairs_with_position)}个残留持仓,开始清理"
        )

        cleanup_count = 0
        for pair in pairs_with_position.values():
            # 订单锁定检查(跳过处理中的订单)
            if self.tickets_manager.is_pair_locked(pair.pair_id):
                continue

            # 通过Intent模式平仓(保持追踪,自动注册到TicketsManager)
            intent = pair.get_close_intent(reason='COOLDOWN_CLEANUP')
            if intent:
                success = self.order_executor.execute_close(intent)
                if success:
                    cleanup_count += 1
                    self.algorithm.Debug(
                        f"[Cooldown清理] {pair.pair_id} 已提交平仓订单"
                    )

        if cleanup_count > 0:
            self.algorithm.Debug(
                f"[Cooldown清理] 本轮提交{cleanup_count}个平仓订单"
            )
        else:
            self.algorithm.Debug(
                f"[Cooldown清理] 所有残留持仓订单处理中,等待下一轮"
            )


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

            # 处理平仓信号
            if signal == TradingSignal.CLOSE:
                self.algorithm.Debug(f"[平仓] {pair.pair_id} Z-score回归")
                intent = pair.get_close_intent(reason='CLOSE')
                if intent:
                    self.order_executor.execute_close(intent)  # 自动注册到TicketsManager

            elif signal == TradingSignal.STOP_LOSS:
                self.algorithm.Debug(f"[止损] {pair.pair_id} Z-score超限")
                intent = pair.get_close_intent(reason='STOP_LOSS')
                if intent:
                    self.order_executor.execute_close(intent)  # 自动注册到TicketsManager


    def get_entry_candidates(self, pairs_without_position: dict, data) -> list:
        """
        获取所有有开仓信号的配对，按质量分数降序排序

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

        职责: 开仓协调和执行

        Args:
            pairs_without_position: 无持仓配对字典 {pair_id: Pairs}
            data: 数据切片

        执行流程:
        1. 获取开仓候选(get_entry_candidates)
        2. 使用MarginAllocator分配资金
        3. 逐个执行开仓(检查: 订单锁 + 风险冷却 + 普通冷却)
        4. 注册订单到tickets_manager

        设计特点:
        - 委托MarginAllocator进行资金分配
        - 双重cooldown检查: 风险冷却(30天) + 普通冷却(10天)
        - 质量分数驱动的分配比例
        - 简洁的开仓循环
        """
        # Step 1: 获取开仓候选(已按质量降序)
        entry_candidates = self.get_entry_candidates(pairs_without_position, data)
        if not entry_candidates:
            return

        # Step 2: 使用MarginAllocator分配资金
        allocations = self.margin_allocator.allocate_margin(entry_candidates)
        if not allocations:
            return  # 无可分配资金或候选配对

        # Step 3: 逐个开仓
        actual_opened = 0
        for pair_id, amount_allocated in allocations.items():
            pair = self.pairs_manager.get_pair_by_id(pair_id)

            # 检查1: 订单锁定检查
            if self.tickets_manager.is_pair_locked(pair_id):
                continue

            # 检查2: 风险冷却期检查
            if self.is_pair_in_risk_cooldown(pair_id):
                self.algorithm.Debug(f"[开仓跳过] {pair_id} 在风险冷却期")
                continue

            # 检查3: 普通交易冷却期检查
            if self.is_pair_in_normal_cooldown(pair):
                self.algorithm.Debug(f"[开仓跳过] {pair_id} 在交易冷却期")
                continue

            # 执行开仓并注册订单追踪
            intent = pair.get_open_intent(amount_allocated, data)
            if intent:
                success = self.order_executor.execute_open(intent)  # 自动注册到TicketsManager
                if success:
                    actual_opened += 1

        # Step 4: 执行结果
        if actual_opened > 0:
            self.algorithm.Debug(f"[开仓] 成功开仓{actual_opened}/{len(entry_candidates)}个配对")
