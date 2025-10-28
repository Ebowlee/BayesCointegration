# region imports
from AlgorithmImports import *
from typing import Dict, List, Set
from src.constants import OrderAction  # v7.2.21: 修复导入遗漏
# endregion


class TicketsManager:
    """
    订单生命周期管理器(Order Lifecycle Tracker)

    设计目的(单一职责):
    防止同一个配对在订单执行期间重复提交新订单

    核心机制:
    1. 订单注册: 建立OrderId→pair_id映射
    2. 状态推导: 从OrderTicket.Status实时计算配对状态
    3. 订单锁定: PENDING状态时阻止新订单提交
    4. 异常检测: 识别Canceled/Invalid订单

    职责边界:
    ✅ 负责: 订单状态追踪、重复下单防护、异常检测
    ❌ 不负责: 配对的时间记录、收益计算、持仓分析

    设计原则:
    - 单一数据源: 状态完全由OrderTicket.Status推导
    - 实时计算: 不存储状态,每次查询都重新计算
    - 最小职责: 只做订单追踪,不做业务分析

    使用示例:
        # 开仓前检查锁定
        if not self.tickets_manager.is_pair_locked(pair.pair_id):
            tickets = pair.open_position(signal, margin, data)
            if tickets:
                self.tickets_manager.register_tickets(pair.pair_id, tickets)

        # OnOrderEvent中检查异常
        anomaly_pairs = self.tickets_manager.get_anomaly_pairs()
        for pair_id in anomaly_pairs:
            # 交由风控模块处理
            self.Debug(f"[订单异常] {pair_id} 检测到单腿失败")
    """

    # ========== 初始化 ==========

    def __init__(self, algorithm, pairs_manager):
        """
        初始化订单管理器

        Args:
            algorithm: QCAlgorithm实例,用于Debug日志
            pairs_manager: PairsManager引用,用于获取Pairs对象进行回调
        """
        self.algorithm = algorithm
        self.pairs_manager = pairs_manager

        # === 核心数据结构 ===
        # OrderId → pair_id 映射(O(1)查找,供OnOrderEvent使用)
        # 例: {123: "(AAPL, MSFT)", 124: "(AAPL, MSFT)", 125: "(GOOGL, AMZN)"}
        self.order_to_pair: Dict[int, str] = {}

        # pair_id → [OrderTicket] 映射(存储当前订单引用)
        # 例: {"(AAPL, MSFT)": [<OrderTicket#123>, <OrderTicket#124>]}
        self.pair_tickets: Dict[str, List[OrderTicket]] = {}

        # pair_id → action 映射(记录订单动作类型,用于回调Pairs)
        # 例: {"(AAPL, MSFT)": "OPEN", ("GOOGL", "AMZN")": "CLOSE"}
        self.pair_actions: Dict[str, str] = {}

        # pair_id → reason 映射(v7.2.21: 记录平仓原因,用于传递给Pairs)
        # 例: {"(AAPL, MSFT)": "STOP_LOSS", ("GOOGL", "AMZN")": "CLOSE"}
        self._pair_close_reasons: Dict[str, str] = {}


    # ========== 公共接口 ==========

    def register_tickets(self, pair_id: str, tickets: List[OrderTicket], action: str, reason: str = None):
        """
        注册订单到管理器 (v7.2.21: 新增reason参数)

        触发时机:
        - pair.open_position() 返回tickets后
        - pair.close_position() 返回tickets后

        效果:
        - 建立OrderId→pair_id映射
        - 激活订单锁定(状态变为PENDING)
        - 记录动作类型(用于COMPLETED时回调Pairs)
        - 存储平仓原因(v7.2.21: 用于动态冷却期)

        Args:
            pair_id: 配对ID,格式如 "(AAPL, MSFT)"
            tickets: OrderTicket列表,通常包含2个元素(long + short)
            action: OrderAction.OPEN 或 OrderAction.CLOSE
            reason: 平仓原因 (仅当action=CLOSE时有效) - v7.2.21
                   例: 'CLOSE', 'STOP_LOSS', 'TIMEOUT', 'RISK_TRIGGER'

        注意:
            - 如果tickets为空,不做任何操作
            - 注册后配对状态自动为PENDING
            - reason会在COMPLETED时传递给Pairs.on_position_filled()
        """
        if not tickets:
            return

        # 存储订单引用和动作类型
        self.pair_tickets[pair_id] = tickets
        self.pair_actions[pair_id] = action

        # v7.2.21: 存储平仓原因(仅对CLOSE动作有效)
        if action == OrderAction.CLOSE and reason:
            self._pair_close_reasons[pair_id] = reason

        # 建立OrderId→pair_id映射
        for ticket in tickets:
            if ticket is not None:
                self.order_to_pair[ticket.OrderId] = pair_id

        # 简化日志
        self.algorithm.Debug(
            f"[TM注册] {pair_id} {action} {len(tickets)}个订单 "
            f"状态:{self.get_pair_status(pair_id)}"
        )


    def is_pair_locked(self, pair_id: str) -> bool:
        """
        检查配对是否被订单锁定

        目的: 防止重复下单的核心判断

        返回:
            True - 有PENDING订单,不能提交新订单
            False - 无订单或订单已完成,可以提交新订单

        使用场景:
            # 在每次交易前检查
            if self.tickets_manager.is_pair_locked(pair.pair_id):
                continue  # 跳过,防止重复下单

            # 执行交易
            tickets = pair.close_position()
            if tickets:
                self.tickets_manager.register_tickets(pair.pair_id, tickets)
        """
        return self.get_pair_status(pair_id) == "PENDING"


    def on_order_event(self, event: OrderEvent):
        """
        处理QuantConnect的订单事件回调

        目的:
        1. 通过OrderId找到pair_id
        2. 记录关键状态变化
        3. 驱动状态更新(通过get_pair_status实时计算)
        4. COMPLETED时回调Pairs记录双腿成交时间

        状态转换:
            注册订单 → PENDING → OnOrderEvent触发 → COMPLETED/ANOMALY
                                                    ↓
                                            回调Pairs.on_position_filled()

        Args:
            event: QuantConnect的OrderEvent对象
        """
        order_id = event.OrderId

        # 查找所属配对
        pair_id = self.order_to_pair.get(order_id)
        if pair_id is None:
            # 不是配对订单,忽略
            return

        # 获取实时状态
        current_status = self.get_pair_status(pair_id)

        # 只在异常时打印日志（减少噪音）
        if current_status == "ANOMALY":
            self.algorithm.Debug(
                f"[OOE异常] {pair_id} OrderId={order_id} "
                f"Status={event.Status} 需风控介入")
            # 异常订单也需要清理映射
            self._cleanup_order_to_pair(pair_id)

        elif current_status == "COMPLETED":
            # 获取动作类型和Pairs对象
            action = self.pair_actions.get(pair_id)
            pairs_obj = self.pairs_manager.get_pair_by_id(pair_id)

            if pairs_obj and action:
                # 获取最后一条腿成交的时间(确保双腿都已成交)
                tickets = self.pair_tickets[pair_id]
                fill_time = max(
                    t.Time for t in tickets
                    if t is not None and t.Status == OrderStatus.Filled
                )

                # v7.2.21: 获取平仓原因(如果有)
                reason = self._pair_close_reasons.get(pair_id, None)

                # 回调Pairs记录时间和数量(v7.2.21: 新增reason参数)
                pairs_obj.on_position_filled(action, fill_time, tickets, reason)

                # 平仓完成后清理 HWM（委托给 RiskManager）
                if action == "CLOSE" and hasattr(self.algorithm, 'risk_manager'):
                    self.algorithm.risk_manager.cleanup_pair_hwm(pair_id)

                # v7.2.21: 清理平仓原因存储(防止内存泄漏)
                if action == OrderAction.CLOSE:
                    self._pair_close_reasons.pop(pair_id, None)

            # 清理已完成订单的映射（防止内存泄漏）
            self._cleanup_order_to_pair(pair_id)


    def get_anomaly_pairs(self) -> Set[str]:
        """
        获取所有有异常订单的配对

        目的: 检测单腿失败
        - 配对交易有两条腿(long + short)
        - 可能出现一条成功,另一条Canceled/Invalid
        - 需要标记异常,交给风控模块处理

        返回:
            Set[pair_id] - 有异常订单的配对ID集合

        使用场景:
            # 在OnOrderEvent后检查
            anomaly_pairs = self.tickets_manager.get_anomaly_pairs()
            for pair_id in anomaly_pairs:
                self.Debug(f"[订单异常] {pair_id} 检测到单腿失败")
                # 风控模块会通过check_pair_anomaly()处理
        """
        return {
            pair_id for pair_id in self.pair_tickets.keys()
            if self.get_pair_status(pair_id) == "ANOMALY"
        }


    # ========== 内部实现 ==========

    def get_pair_status(self, pair_id: str) -> str:
        """
        实时计算配对的订单状态

        设计原则: 单一数据源
        - 状态完全由OrderTicket.Status推导
        - 不存储状态(避免状态过期)
        - 每次查询都实时计算

        状态映射规则:
        - 无订单记录 → "NONE"
        - 所有订单Filled → "COMPLETED"
        - 任一订单Canceled/Invalid → "ANOMALY"
        - 其他(Submitted/PartiallyFilled) → "PENDING"

        Args:
            pair_id: 配对ID,格式如 "(AAPL, MSFT)"

        Returns:
            "NONE" | "PENDING" | "COMPLETED" | "ANOMALY"
        """
        # 检查是否有订单记录
        tickets = self.pair_tickets.get(pair_id)
        if not tickets:
            return "NONE"

        # 过滤有效订单
        valid_tickets = [t for t in tickets if t is not None]
        if not valid_tickets:
            return "NONE"

        # 状态推导逻辑
        all_filled = all(t.Status == OrderStatus.Filled for t in valid_tickets)
        has_canceled = any(
            t.Status in [OrderStatus.Canceled, OrderStatus.Invalid]
            for t in valid_tickets
        )

        if all_filled:
            return "COMPLETED"
        elif has_canceled:
            return "ANOMALY"
        else:
            return "PENDING"


    def _cleanup_order_to_pair(self, pair_id: str):
        """
        清理已完成配对的order_to_pair映射

        目的: 防止内存泄漏
        - 当订单状态变为COMPLETED/ANOMALY后,清理order_to_pair映射
        - 保留pair_tickets和pair_actions,供后续查询使用

        清理时机:
        - COMPLETED: 订单全部成交,不再需要OnOrderEvent追踪
        - ANOMALY: 订单异常,已标记需要风控处理,不再需要追踪

        设计原则:
        - 只清理order_to_pair映射(用于OnOrderEvent查找)
        - 不清理pair_tickets(可能需要查询订单详情)
        - 不清理pair_actions(可能需要查询动作类型)

        Args:
            pair_id: 配对ID,格式如 "(AAPL, MSFT)"
        """
        if pair_id not in self.pair_tickets:
            return

        tickets = self.pair_tickets[pair_id]
        cleaned_count = 0

        for ticket in tickets:
            if ticket is not None and ticket.OrderId in self.order_to_pair:
                del self.order_to_pair[ticket.OrderId]
                cleaned_count += 1

        # 删除日志输出（减少噪音）
