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
# v7.10.6: 常量已移至config.constants统一管理，不再需要constants.py
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

    def __init__(self, algorithm, pairs_manager, risk_manager, tickets_manager, order_executor):
        """
        初始化统一执行器 (v7.62.0: 删除margin_allocator参数,资金分配迁移至PairsManager)

        Args:
            algorithm: QuantConnect算法实例
            pairs_manager: 配对管理器(v7.62.0新增资金分配职责)
            risk_manager: 风控管理器(用于cooldown检查)
            tickets_manager: 订单追踪管理器
            order_executor: 订单执行器
        """
        self.algorithm = algorithm
        self.pairs_manager = pairs_manager
        self.risk_manager = risk_manager
        self.tickets_manager = tickets_manager
        self.order_executor = order_executor


    # ===== Cooldown检查方法 =====

    def is_pair_in_cooldown(self, pair) -> bool:
        """
        统一的配对冷却期检查 (v7.43.0: 更新术语 elapsed/required)

        检查逻辑:
        1. 检查Rule cooldown (风控触发的冷却: TIMEOUT/DRAWDOWN/ANOMALY)
        2. 检查Pairs cooldown (正常交易的冷却: MEAN_REVERSION/PAIR_BREAK)
        3. 任一生效则返回True

        冷却期分类 (基于config.CLOSE_REASONS的category字段):
        - NORMAL_SIGNAL: 信号触发 (MEAN_REVERSION, PAIR_BREAK)
          - 冷却期: 10天
          - 管理: Pairs对象 (pair_closed_time + last_close_reason)

        - PAIR_RISK: Pair风控触发 (TIMEOUT, DRAWDOWN, ANOMALY)
          - 冷却期: 10天/180天/永久
          - 管理: Rule对象 + Pairs对象 (双重同步)

        - PORTFOLIO_RISK: Portfolio风控触发 (PORTFOLIO_DRAWDOWN, ACCOUNT_BLOWUP)
          - 冷却期: 360天/永久
          - 管理: 全局cooldown (不参与per-pair检查)

        术语说明 (v7.43.0):
        - elapsed: 已经过去的天数 (从平仓到现在)
        - required: 需要等待的天数 (从配置读取)
        - 判断逻辑: elapsed < required → 仍在冷却期

        Args:
            pair: Pairs对象

        Returns:
            True: 在冷却期 (任一Rule或Pairs cooldown生效)
            False: 不在冷却期

        设计理由 (v7.16.0):
        - Rule cooldown和Pairs cooldown已通过RiskManager同步,无需分开检查
        - 统一接口简化调用逻辑,消除重复检查
        - 单一检查点,降低数据不一致风险
        """
        pair_id = pair.pair_id

        # 检查1: Rule层面cooldown (TIMEOUT/DRAWDOWN/ANOMALY风控)
        for rule in self.risk_manager.pair_rules:
            if rule.is_in_cooldown(pair_id=pair_id):
                return True

        # 检查2: Pairs层面cooldown (MEAN_REVERSION/PAIR_BREAK信号)
        elapsed = pair.get_cooldown_elapsed_days()
        if elapsed is not None:
            required = self.pairs_manager.get_cooldown_required_days(pair.last_close_reason)
            if elapsed < required:
                return True

        return False


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

        executed_count = 0  # 记录成功执行的配对数量

        for intent in intents:
            # 订单锁定检查（防止重复下单）
            if self.tickets_manager.is_pair_locked(intent.pair_id):
                continue

            # 通过order_executor执行平仓Intent (自动注册到TicketsManager)
            success = self.order_executor.execute_close(intent)
            if success:
                executed_count += 1
                # v7.31.4: 记录平仓原因统计
                self.algorithm.record_close_stat(intent.reason)

        # 无论成功与否,都激活cooldown（防止继续交易）
        risk_manager.activate_cooldown_for_portfolio(triggered_rule)



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


        executed_pair_ids = []  # 记录成功执行的pair_id

        for intent in intents:
            # 订单锁定检查（防止重复下单）
            if self.tickets_manager.is_pair_locked(intent.pair_id):
                continue

            # 通过order_executor执行平仓Intent (自动注册到TicketsManager)
            success = self.order_executor.execute_close(intent)
            if success:
                executed_pair_ids.append(intent.pair_id)

                # v7.31.4: 记录平仓原因统计
                self.algorithm.record_close_stat(intent.reason)

                # 清理该配对的HWM状态（PairDrawdownRule）
                risk_manager.cleanup_pair_hwm(intent.pair_id)

        # 激活触发规则的cooldown（只为成功执行的Intent激活）
        if executed_pair_ids:
            risk_manager.activate_cooldown_for_pairs(executed_pair_ids)


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
                    # v7.31.4: 记录平仓原因统计
                    self.algorithm.record_close_stat(intent.reason)


    # ===== 正常交易执行方法 =====

    def handle_normal_close_intents(self, pairs_with_position, data):
        """
        处理正常交易的平仓Intent

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
        - 命名与handle_*_risk_intents()保持一致(Intent Pattern)
        """
        for pair in pairs_with_position.values():
            # 订单锁定检查(跳过已被风控处理或订单执行中的配对)
            if self.tickets_manager.is_pair_locked(pair.pair_id):
                continue

            # 获取交易信号
            signal = pair.get_signal(data)

            # v7.13.0: 处理平仓信号 - 三分类平仓原因
            if signal == 'CLOSE':
                # 计算当前zscore判断是否为正常回归
                symbol1, symbol2 = pair.pair_id
                if symbol1 in data and symbol2 in data:
                    price1 = data[symbol1].Close
                    price2 = data[symbol2].Close
                    current_zscore = pair.get_zscore(price1, price2)

                    # 判断平仓原因: |zscore| < 0.5 认为是均值回归, 否则可能被止损
                    if current_zscore is not None and abs(current_zscore) < 0.5:
                        reason = 'MEAN_REVERSION'  # 均值回归
                    else:
                        reason = 'PAIR_BREAK'  # Z-score仍然偏离(可能被止损)
                else:
                    reason = 'MEAN_REVERSION'  # 数据缺失时默认为正常回归

                intent = pair.get_close_intent(reason=reason)
                if intent:
                    success = self.order_executor.execute_close(intent)  # 自动注册到TicketsManager
                    # v7.31.4: 记录平仓原因统计
                    if success:
                        self.algorithm.record_close_stat(intent.reason)

            elif signal == 'PAIR_BREAK':  # v7.10.6: 原STOP_LOSS重命名
                intent = pair.get_close_intent(reason='PAIR_BREAK')  # v7.13.0: 协整破裂
                if intent:
                    success = self.order_executor.execute_close(intent)  # 自动注册到TicketsManager
                    # v7.31.4: 记录平仓原因统计
                    if success:
                        self.algorithm.record_close_stat(intent.reason)


    def handle_normal_open_intents(self, allocated_candidates: List[tuple]):
        """
        处理正常交易的开仓Intent (v7.63.0: 简化为纯执行层)

        职责: 接收PairsManager筛选和分配好的候选,执行开仓订单

        Args:
            allocated_candidates: [(pair, signal, allocated_margin), ...]
                已完成筛选+排序+资金分配的候选列表
                - pair: Pairs对象
                - signal: TradingSignal (LONG_SPREAD/SHORT_SPREAD)
                - allocated_margin: 分配的保证金金额

        执行流程:
        1. 遍历已分配的候选配对
        2. 生成开仓Intent (pair.get_open_intent)
        3. 执行订单 (order_executor.execute_open,自动注册到TicketsManager)
        4. 记录开仓日志

        设计特点 (v7.63.0):
        - PairsManager完成筛选+分配 → ExecutionManager只负责执行
        - 删除get_entry_candidates: 职责已迁移至PairsManager
        - 删除资金分配逻辑: 候选已携带allocated_margin
        - 删除订单锁和冷却期检查: PairsManager已过滤
        - 保留开仓日志: 记录行业、质量、Z-score、分配金额
        """
        if not allocated_candidates:
            return

        for pair, signal, allocated_margin in allocated_candidates:
            # 生成开仓Intent
            intent = pair.get_open_intent(allocated_margin, self.algorithm.CurrentSlice)
            if not intent:
                continue

            # 执行订单 (自动注册到TicketsManager)
            success = self.order_executor.execute_open(intent)
            if not success:
                continue

            # 记录开仓日志 (v7.36.1格式: 行业|质量|Z-score|分配金额)
            industry_names = self.algorithm.config.constants['industry_names']
            industry_name = industry_names.get(int(pair.industry_code), f'未知({pair.industry_code})')

            # 计算质量标签
            quality_score = pair.quality_score
            if quality_score >= 0.80:
                quality_label = "Q=≥0.80"
            elif quality_score >= 0.70:
                quality_label = "Q=0.70-0.80"
            elif quality_score >= 0.60:
                quality_label = "Q=0.60-0.70"
            else:
                quality_label = f"Q={quality_score:.2f}"

            # 输出日志
            entry_z = pair.entry_zscore if pair.entry_zscore is not None else 0.0
            self.algorithm.Debug(
                f"[开仓] {pair.pair_id} | {industry_name} | {quality_label} | "
                f"Z-score={entry_z:+.2f}σ | 分配=${allocated_margin:,.0f}"
            )
