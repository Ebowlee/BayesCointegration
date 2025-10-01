# region imports
from AlgorithmImports import *
# endregion


class TicketsManager:
    """
    订单生命周期管理器

    职责:
    1. 追踪所有配对订单的执行状态
    2. 通过OrderId快速定位所属配对
    3. 检测订单异常(单腿失败、部分成交等)
    4. 防止重复下单(通过订单锁定机制)

    核心机制:
    - PENDING状态锁定配对,阻止重复下单
    - COMPLETED状态解锁配对,允许新操作
    - ANOMALY状态标记异常,交由风控处理
    """

    def __init__(self, algorithm):
        """
        初始化订单管理器

        Args:
            algorithm: QCAlgorithm实例,用于Debug日志
        """
        self.algorithm = algorithm

        # === 核心数据结构 ===
        # OrderId → pair_id (O(1)查找,供OnOrderEvent使用)
        self.order_to_pair = {}

        # pair_id → [OrderTicket1, OrderTicket2]
        self.pair_tickets = {}

        # pair_id → 状态 ("PENDING"/"COMPLETED"/"ANOMALY")
        self.pair_status = {}

        # 异常配对集合 (单腿失败、订单取消等)
        self.anomaly_pairs = set()


    def register_tickets(self, pair_id: str, tickets: list):
        """
        注册一对订单

        在调用Pairs.open_position()或close_position()后立即调用
        将订单与配对关联,并设置PENDING状态锁定该配对

        Args:
            pair_id: 配对ID,格式如 "(AAPL, MSFT)"
            tickets: OrderTicket列表,通常包含两个元素

        注意:
            - 如果tickets为空或None,不做任何操作
            - 注册后配对立即进入PENDING状态,阻止重复下单
        """
        if not tickets:
            return

        # 保存tickets
        self.pair_tickets[pair_id] = tickets

        # 建立OrderId → pair_id映射
        for ticket in tickets:
            if ticket is not None:
                self.order_to_pair[ticket.OrderId] = pair_id

        # 设置PENDING状态
        self.pair_status[pair_id] = "PENDING"

        self.algorithm.Debug(
            f"[TicketsManager] {pair_id} 注册订单: "
            f"{len(tickets)}个OrderTicket, OrderId={[t.OrderId for t in tickets if t]}",
            2
        )


    def on_order_event(self, event):
        """
        OnOrderEvent回调入口

        接收QCAlgorithm的OnOrderEvent事件,更新订单状态
        检查配对的两腿是否全部Filled或存在异常

        Args:
            event: OrderEvent对象,包含OrderId, Status等信息

        状态转换:
            - 全部Filled → COMPLETED
            - 任一Canceled/Invalid → ANOMALY
            - 其他(Submitted/PartiallyFilled) → 保持PENDING
        """
        order_id = event.OrderId

        # 查找所属配对
        pair_id = self.order_to_pair.get(order_id)
        if pair_id is None:
            # 非配对订单,忽略
            return

        # 获取该配对的所有tickets
        tickets = self.pair_tickets.get(pair_id)
        if not tickets:
            return

        # 记录事件
        self.algorithm.Debug(
            f"[OOE] {pair_id} OrderId={order_id} "
            f"Status={event.Status} FillQty={event.FillQuantity}",
            2
        )

        # === 状态判断逻辑 ===
        all_filled = all(t.Status == OrderStatus.Filled for t in tickets if t)
        has_canceled = any(
            t.Status in [OrderStatus.Canceled, OrderStatus.Invalid]
            for t in tickets if t
        )

        # 场景1: 全部成交 → COMPLETED
        if all_filled:
            self.pair_status[pair_id] = "COMPLETED"
            self.algorithm.Debug(f"[OOE] {pair_id} 订单全部成交,解锁配对", 1)
            # 可选: 自动清理记录
            # self.cleanup_completed(pair_id)

        # 场景2: 存在取消/失败 → ANOMALY
        elif has_canceled:
            self.pair_status[pair_id] = "ANOMALY"
            self.anomaly_pairs.add(pair_id)
            self.algorithm.Debug(f"[OOE] {pair_id} 订单异常(Canceled/Invalid),标记异常", 1)

        # 场景3: 部分成交或提交中 → 保持PENDING,等待broker继续处理
        # (QuantConnect的GTC订单会自动继续尝试成交)


    def is_pair_locked(self, pair_id: str) -> bool:
        """
        检查配对是否被订单锁定

        在执行开仓/平仓操作前调用,防止重复下单

        Args:
            pair_id: 配对ID

        Returns:
            True: 配对正在处理订单(PENDING状态),应跳过
            False: 配对空闲或已完成,可以下单
        """
        status = self.pair_status.get(pair_id)
        return status == "PENDING"


    def get_anomaly_pairs(self) -> list:
        """
        获取所有异常配对

        返回所有标记为ANOMALY状态的配对ID列表
        通常在OnOrderEvent后检查,交由风控模块处理单腿持仓

        Returns:
            异常配对ID列表,如 ["(AAPL, MSFT)", "(GOOGL, AMZN)"]
        """
        return list(self.anomaly_pairs)


    def cleanup_completed(self, pair_id: str):
        """
        清理已完成订单的记录

        释放内存,避免历史订单记录持续积累
        可在订单全部Filled后自动调用,或定期批量清理

        Args:
            pair_id: 配对ID

        注意:
            - 只清理COMPLETED状态的配对
            - PENDING和ANOMALY状态不清理,保留追踪
        """
        status = self.pair_status.get(pair_id)
        if status != "COMPLETED":
            return

        # 清理OrderId映射
        tickets = self.pair_tickets.get(pair_id, [])
        for ticket in tickets:
            if ticket and ticket.OrderId in self.order_to_pair:
                del self.order_to_pair[ticket.OrderId]

        # 清理配对记录
        if pair_id in self.pair_tickets:
            del self.pair_tickets[pair_id]
        if pair_id in self.pair_status:
            del self.pair_status[pair_id]

        self.algorithm.Debug(f"[TicketsManager] {pair_id} 已清理完成订单记录", 2)


    def get_status_summary(self) -> dict:
        """
        获取当前订单状态摘要

        用于调试和监控

        Returns:
            {
                "total_pairs": 总配对数,
                "pending": PENDING数量,
                "completed": COMPLETED数量,
                "anomaly": ANOMALY数量
            }
        """
        total = len(self.pair_status)
        pending = sum(1 for s in self.pair_status.values() if s == "PENDING")
        completed = sum(1 for s in self.pair_status.values() if s == "COMPLETED")
        anomaly = len(self.anomaly_pairs)

        return {
            "total_pairs": total,
            "pending": pending,
            "completed": completed,
            "anomaly": anomaly
        }
