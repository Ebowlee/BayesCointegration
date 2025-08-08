# region imports
from AlgorithmImports import *
from typing import Dict, List, Optional, Tuple, Set, TYPE_CHECKING

if TYPE_CHECKING:
    from AlgorithmImports import Symbol
from datetime import datetime, timedelta
# endregion


class OrderInfo:
    """订单信息 - 极简设计"""
    
    def __init__(self, order_id: int, symbol, quantity: float, 
                 submit_time: datetime, order_type: str):
        self.order_id = order_id
        self.symbol = symbol
        self.quantity = quantity
        self.submit_time = submit_time
        self.order_type = order_type  # 'entry' or 'exit'
        
        # 状态信息
        self.status = OrderStatus.Submitted
        self.fill_time: Optional[datetime] = None
        self.pair_id: Optional[str] = None
    
    def update(self, order_event, current_time: datetime):
        """更新订单状态"""
        self.status = order_event.Status
        if order_event.Status == OrderStatus.Filled:
            self.fill_time = current_time
    
    @property
    def is_filled(self) -> bool:
        return self.status == OrderStatus.Filled
    
    @property
    def is_pending(self) -> bool:
        return self.status in [OrderStatus.Submitted, OrderStatus.PartiallyFilled, 
                              OrderStatus.UpdateSubmitted]


class PairOrderInfo:
    """配对订单信息 - 只记录必要信息"""
    
    def __init__(self, pair_id: str, symbol1, symbol2):
        self.pair_id = pair_id
        self.symbol1 = symbol1
        self.symbol2 = symbol2
        
        # 订单列表
        self.orders: List[OrderInfo] = []
        
        # 时间记录
        self.entry_time: Optional[datetime] = None
        self.exit_time: Optional[datetime] = None
    
    def add_order(self, order: OrderInfo):
        """添加订单"""
        self.orders.append(order)
        
    def is_normal(self) -> bool:
        """
        判断配对是否正常
        正常：两边都Filled 或 两边都Pending
        """
        # 按symbol分组订单
        s1_orders = [o for o in self.orders if o.symbol == self.symbol1]
        s2_orders = [o for o in self.orders if o.symbol == self.symbol2]
        
        if not s1_orders or not s2_orders:
            return False
        
        # 检查两边状态
        s1_filled = all(o.is_filled for o in s1_orders)
        s2_filled = all(o.is_filled for o in s2_orders)
        s1_pending = any(o.is_pending for o in s1_orders)
        s2_pending = any(o.is_pending for o in s2_orders)
        
        # 正常：两边都filled 或 两边都有pending
        return (s1_filled and s2_filled) or (s1_pending and s2_pending)
    
    def update_times(self):
        """更新时间记录"""
        # 获取entry和exit订单
        entry_orders = [o for o in self.orders if o.order_type == 'entry' and o.is_filled]
        exit_orders = [o for o in self.orders if o.order_type == 'exit' and o.is_filled]
        
        # 先检查exit时间（需要两边都成交）
        s1_exit = next((o for o in exit_orders if o.symbol == self.symbol1), None)
        s2_exit = next((o for o in exit_orders if o.symbol == self.symbol2), None)
        
        if s1_exit and s2_exit and s1_exit.fill_time and s2_exit.fill_time:
            new_exit_time = max(s1_exit.fill_time, s2_exit.fill_time)
            
            # 如果是新的平仓（时间更晚），重置entry_time
            if self.exit_time is None or new_exit_time > self.exit_time:
                self.exit_time = new_exit_time
                self.entry_time = None  # 重置entry_time，为下次建仓准备
        
        # 检查entry时间（需要两边都成交）
        # v3.6.0修复：只考虑最近一次平仓后的entry订单
        if self.entry_time is None or (self.exit_time and self.entry_time and self.entry_time < self.exit_time):
            # 筛选出exit_time之后的entry订单（如果有exit_time的话）
            valid_entry_orders = entry_orders
            if self.exit_time:
                valid_entry_orders = [o for o in entry_orders if o.fill_time and o.fill_time > self.exit_time]
            
            # 查找两边的entry订单
            s1_entry = next((o for o in valid_entry_orders if o.symbol == self.symbol1), None)
            s2_entry = next((o for o in valid_entry_orders if o.symbol == self.symbol2), None)
            
            if s1_entry and s2_entry and s1_entry.fill_time and s2_entry.fill_time:
                self.entry_time = max(s1_entry.fill_time, s2_entry.fill_time)


class OrderTracker:
    """
    订单追踪器 - 极简版本
    
    仅提供3个功能：
    1. 记录订单状态
    2. 记录配对时间
    3. 供风控查询
    """
    
    def __init__(self, algorithm, central_pair_manager=None):
        self.algorithm = algorithm
        self.central_pair_manager = central_pair_manager
        
        # 数据存储
        self.orders: Dict[int, OrderInfo] = {}
        self.pair_orders: Dict[str, PairOrderInfo] = {}
        
        self.algorithm.Debug("[OrderTracker] 初始化完成")
    
    def on_order_event(self, order_event):
        """处理订单事件"""
        order_id = order_event.OrderId
        
        # 获取订单
        order = self.algorithm.Transactions.GetOrderById(order_id)
        if not order:
            return
        
        # 新订单
        if order_id not in self.orders:
            self._create_order_info(order, order_event)
        
        # 更新状态
        order_info = self.orders[order_id]
        order_info.update(order_event, self.algorithm.Time)
        
        # 更新配对时间
        if order_info.pair_id and order_info.is_filled:
            pair_info = self.pair_orders.get(order_info.pair_id)
            if pair_info:
                pair_info.update_times()
    
    def _create_order_info(self, order, order_event):
        """创建订单信息"""
        symbol = order.Symbol
        position_before = self.algorithm.Portfolio[symbol].Quantity
        
        # 创建订单
        order_info = OrderInfo(
            order_id=order.Id,
            symbol=symbol,
            quantity=order.Quantity,
            submit_time=order.Time,
            order_type='entry' if position_before == 0 else 'exit'
        )
        
        # 查找配对（使用CPM如果可用）
        pair = None
        if self.central_pair_manager:
            paired_symbol = self.central_pair_manager.get_paired_symbol(symbol)
            if paired_symbol:
                pair = (symbol, paired_symbol)
        
        if pair:
            # 生成配对ID
            pair_id = self._get_pair_id(pair[0], pair[1])
            order_info.pair_id = pair_id
            
            # 创建或更新配对信息
            if pair_id not in self.pair_orders:
                self.pair_orders[pair_id] = PairOrderInfo(pair_id, pair[0], pair[1])
            
            self.pair_orders[pair_id].add_order(order_info)
        
        self.orders[order.Id] = order_info
    
    def _get_pair_id(self, symbol1, symbol2) -> str:
        """生成配对ID"""
        if symbol1.Value < symbol2.Value:
            return f"{symbol1.Value}&{symbol2.Value}"
        return f"{symbol2.Value}&{symbol1.Value}"
    
    # ============= 风控查询接口 =============
    
    def get_abnormal_pairs(self) -> List[Tuple]:
        """获取异常配对"""
        abnormal = []
        for pair_info in self.pair_orders.values():
            if not pair_info.is_normal():
                abnormal.append((pair_info.symbol1, pair_info.symbol2))
        return abnormal
    
    def get_pair_entry_time(self, symbol1, symbol2) -> Optional[datetime]:
        """获取配对建仓时间"""
        pair_id = self._get_pair_id(symbol1, symbol2)
        if pair_id in self.pair_orders:
            return self.pair_orders[pair_id].entry_time
        return None
    
    def get_pair_exit_time(self, symbol1, symbol2) -> Optional[datetime]:
        """获取配对平仓时间"""
        pair_id = self._get_pair_id(symbol1, symbol2)
        if pair_id in self.pair_orders:
            return self.pair_orders[pair_id].exit_time
        return None
    
    def is_in_cooldown(self, symbol1, symbol2, cooldown_days: int) -> bool:
        """检查是否在冷却期"""
        exit_time = self.get_pair_exit_time(symbol1, symbol2)
        if exit_time:
            return (self.algorithm.Time - exit_time).days < cooldown_days
        return False
    
    def get_holding_period(self, symbol1, symbol2) -> Optional[int]:
        """
        获取持仓天数
        
        v3.6.0修复：确保只计算当前持仓段的时间，不累计历史持仓
        """
        # 首先检查是否真的有持仓
        if not (self.algorithm.Portfolio[symbol1].Invested and 
                self.algorithm.Portfolio[symbol2].Invested):
            return None
        
        # 获取配对的时间信息
        entry_time = self.get_pair_entry_time(symbol1, symbol2)
        exit_time = self.get_pair_exit_time(symbol1, symbol2)
        
        # 如果有entry_time
        if entry_time:
            # 确保entry_time在exit_time之后（如果有exit_time）
            if exit_time and exit_time >= entry_time:
                # 数据异常：已平仓但仍有持仓，可能是时间记录未更新
                # 返回None让风控模块使用默认值
                self.algorithm.Debug(
                    f"[OrderTracker] 警告：{symbol1.Value},{symbol2.Value} "
                    f"时间记录异常 (exit={exit_time}, entry={entry_time})"
                )
                return None
            
            return (self.algorithm.Time - entry_time).days
        
        return None
    
    def has_pending_orders(self, symbol) -> bool:
        """检查是否有待成交订单"""
        return any(o.symbol == symbol and o.is_pending for o in self.orders.values())
    
    def clear_old_records(self, days_to_keep: int = 65):
        """清理旧记录"""
        cutoff_time = self.algorithm.Time - timedelta(days=days_to_keep)
        
        # 清理旧订单
        old_orders = [oid for oid, o in self.orders.items() 
                     if o.submit_time < cutoff_time]
        for oid in old_orders:
            del self.orders[oid]
        
        # 清理旧配对（已平仓且超期）
        old_pairs = [pid for pid, p in self.pair_orders.items() 
                    if p.exit_time and p.exit_time < cutoff_time]
        for pid in old_pairs:
            del self.pair_orders[pid]
        
        if old_orders or old_pairs:
            self.algorithm.Debug(
                f"[OrderTracker] 清理完成: {len(old_orders)}个订单, {len(old_pairs)}个配对"
            )