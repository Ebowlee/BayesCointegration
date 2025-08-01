# region imports
from AlgorithmImports import *
from typing import Dict, List, Tuple, Optional
from datetime import datetime
# endregion


class PairInfo:
    """
    配对信息类 - 跟踪单个配对的状态和历史
    
    主要功能:
    1. 跟踪配对的发现状态（本轮是否被选中）
    2. 跟踪配对的持仓状态（是否有仓位）
    3. 计算持仓时间用于风控
    4. 获取实时盈亏信息
    """
    
    def __init__(self, symbol1: Symbol, symbol2: Symbol):
        """
        初始化配对信息
        
        Args:
            symbol1: 配对中的第一只股票
            symbol2: 配对中的第二只股票
        """
        self.symbol1 = symbol1
        self.symbol2 = symbol2
        
        # 发现状态
        self.is_current_round = False  # 本轮选股是否发现
        self.discovery_count = 0       # 历史被发现总次数
        
        # 持仓状态
        self.has_position = False      # 是否有持仓
        self.entry_time = None         # 建仓时间（None表示无持仓）
        self.last_exit_time = None     # 最近平仓时间（用于冷却期）
        
        # 风控状态
        self.risk_triggered = False    # 是否触发风控
        self.risk_type = None         # 风控类型：TIMEOUT/STOP_LOSS/TAKE_PROFIT
        
        # 跨周期统计
        self.trade_count = 0          # 完整交易次数
        self.holding_days_total = 0   # 累计持仓天数
    
    def get_holding_days(self, algorithm) -> int:
        """
        获取当前持仓天数
        
        Args:
            algorithm: 主算法实例，用于获取当前时间
            
        Returns:
            int: 持仓天数，无持仓返回0
        """
        if not self.has_position or not self.entry_time:
            return 0
        return (algorithm.Time - self.entry_time).days
    
    def get_idle_days(self, algorithm) -> int:
        """
        获取空仓天数（用于冷却期判断）
        
        Args:
            algorithm: 主算法实例
            
        Returns:
            int: 空仓天数，有持仓或从未交易返回0
        """
        if self.has_position or not self.last_exit_time:
            return 0
        return (algorithm.Time - self.last_exit_time).days
    
    def get_position_status(self, algorithm) -> dict:
        """
        获取实时持仓状态（利用框架数据）
        
        Args:
            algorithm: 主算法实例
            
        Returns:
            dict: 包含持仓状态、盈亏、持仓天数等信息
        """
        h1 = algorithm.Portfolio[self.symbol1]
        h2 = algorithm.Portfolio[self.symbol2]
        
        # 计算整对的持仓价值
        total_value = abs(h1.HoldingsValue) + abs(h2.HoldingsValue)
        
        return {
            'has_position': h1.Invested and h2.Invested,
            'holding_days': self.get_holding_days(algorithm),
            
            # 单边信息
            self.symbol1.Value: {
                'quantity': h1.Quantity,
                'average_price': h1.AveragePrice,
                'current_price': h1.Price,
                'unrealized_pnl': h1.UnrealizedProfit,
                'pnl_percent': h1.UnrealizedProfitPercent
            },
            self.symbol2.Value: {
                'quantity': h2.Quantity,
                'average_price': h2.AveragePrice,
                'current_price': h2.Price,
                'unrealized_pnl': h2.UnrealizedProfit,
                'pnl_percent': h2.UnrealizedProfitPercent
            },
            
            # 整对盈亏
            'total_pnl': h1.UnrealizedProfit + h2.UnrealizedProfit,
            'total_pnl_percent': (h1.UnrealizedProfit + h2.UnrealizedProfit) / total_value if total_value > 0 else 0
        }
    
    @property
    def avg_holding_days(self) -> float:
        """平均持仓天数"""
        return self.holding_days_total / self.trade_count if self.trade_count > 0 else 0


class PairLedger:
    """
    配对交易账本 - 跨模块共享的配对状态管理器
    
    主要职责:
    1. 维护所有配对的发现和持仓状态
    2. 提供风控所需的实时数据
    3. 跟踪配对的生命周期
    4. 协调各模块间的状态同步
    
    使用者:
    - AlphaModel: 更新发现状态，查询可交易性
    - PortfolioConstruction: 查询配对状态和限制
    - CustomRiskManager: 更新持仓状态，设置风控标记
    """
    
    def __init__(self, algorithm):
        """
        初始化配对账本
        
        Args:
            algorithm: 主算法实例
        """
        self.algorithm = algorithm
        
        # 所有配对信息 {(symbol1, symbol2): PairInfo}
        self.all_pairs: Dict[Tuple[Symbol, Symbol], PairInfo] = {}
        
        # 配对双向映射 {Symbol: Symbol} - 快速查找配对关系
        self.symbol_map: Dict[Symbol, Symbol] = {}
    
    def update_from_selection(self, new_pairs: List[Tuple[Symbol, Symbol]]):
        """
        更新本轮选股发现的配对
        
        由AlphaModel在每轮选股后调用
        
        Args:
            new_pairs: 新选出的配对列表，每个元素为 (symbol1, symbol2)
        """
        # 重置所有配对的当轮发现状态
        for pair_info in self.all_pairs.values():
            pair_info.is_current_round = False
        
        # 处理新发现的配对
        current_round_pairs = set()
        for symbol1, symbol2 in new_pairs:
            pair_key = self._get_pair_key(symbol1, symbol2)
            current_round_pairs.add(pair_key)
            
            # 新配对
            if pair_key not in self.all_pairs:
                self.all_pairs[pair_key] = PairInfo(*pair_key)
                self.algorithm.Debug(f"[PairLedger] 发现新配对: {pair_key[0].Value}-{pair_key[1].Value}")
            
            # 更新发现状态
            self.all_pairs[pair_key].is_current_round = True
            self.all_pairs[pair_key].discovery_count += 1
        
        # 清理长期未被发现且无持仓的配对（可选）
        # self._cleanup_dormant_pairs(current_round_pairs)
        
        # 重建symbol映射
        self._rebuild_symbol_map()
        
        self.algorithm.Debug(
            f"[PairLedger] 本轮发现{len(current_round_pairs)}对，"
            f"总计跟踪{len(self.all_pairs)}对"
        )
    
    def update_position_status_from_order(self, order_event):
        """
        从订单事件更新持仓状态
        
        由CustomRiskManager在OnOrderEvent中调用
        
        Args:
            order_event: QuantConnect的订单事件
        """
        if order_event.Status != OrderStatus.Filled:
            return
        
        symbol = order_event.Symbol
        
        # 查找包含此symbol的所有配对
        for pair_key, pair_info in self.all_pairs.items():
            if symbol in pair_key:
                # 检查配对的两边是否都有持仓
                h1 = self.algorithm.Portfolio[pair_info.symbol1]
                h2 = self.algorithm.Portfolio[pair_info.symbol2]
                
                was_holding = pair_info.has_position
                is_holding = h1.Invested and h2.Invested
                
                # 状态变化：空仓→持仓
                if not was_holding and is_holding:
                    pair_info.has_position = True
                    pair_info.entry_time = self.algorithm.Time
                    self.algorithm.Debug(
                        f"[PairLedger] 配对建仓: {pair_key[0].Value}-{pair_key[1].Value}"
                    )
                
                # 状态变化：持仓→空仓
                elif was_holding and not is_holding:
                    # 更新统计
                    if pair_info.entry_time:
                        holding_days = (self.algorithm.Time - pair_info.entry_time).days
                        pair_info.holding_days_total += holding_days
                        pair_info.trade_count += 1
                    
                    # 更新状态
                    pair_info.has_position = False
                    pair_info.entry_time = None
                    pair_info.last_exit_time = self.algorithm.Time
                    
                    # 清除风控标记
                    if pair_info.risk_triggered:
                        pair_info.risk_triggered = False
                        pair_info.risk_type = None
                    
                    self.algorithm.Debug(
                        f"[PairLedger] 配对平仓: {pair_key[0].Value}-{pair_key[1].Value}, "
                        f"持仓{holding_days}天"
                    )
    
    def get_risk_control_data(self) -> List[Dict]:
        """
        获取所有配对的风控数据
        
        供CustomRiskManager进行风控检查
        
        Returns:
            List[Dict]: 所有持仓配对的风控相关数据，按持仓天数降序
        """
        risk_data = []
        
        for pair_info in self.all_pairs.values():
            if not pair_info.has_position:
                continue
            
            status = pair_info.get_position_status(self.algorithm)
            
            risk_data.append({
                'pair': (pair_info.symbol1, pair_info.symbol2),
                'pair_info': pair_info,  # 引用，便于设置风控标记
                'holding_days': status['holding_days'],
                'total_pnl': status['total_pnl'],
                'total_pnl_percent': status['total_pnl_percent'],
                'details': status
            })
        
        # 按持仓天数降序排列
        return sorted(risk_data, key=lambda x: x['holding_days'], reverse=True)
    
    def get_active_pairs_count(self) -> int:
        """
        获取当前活跃配对数量
        
        供PortfolioConstruction检查最大配对数限制
        
        Returns:
            int: 有持仓的配对数量
        """
        return sum(1 for pair in self.all_pairs.values() if pair.has_position)
    
    def is_tradeable(self, symbol1: Symbol, symbol2: Symbol, cooldown_days: int = 0) -> bool:
        """
        检查配对是否可交易
        
        供PortfolioConstruction检查交易限制
        
        Args:
            symbol1, symbol2: 配对的两只股票
            cooldown_days: 冷却期天数
            
        Returns:
            bool: 是否可以交易
        """
        pair_info = self.get_pair_info(symbol1, symbol2)
        if not pair_info:
            return False
        
        # 检查1: 必须是本轮发现的
        if not pair_info.is_current_round:
            return False
        
        # 检查2: 不能已有持仓
        if pair_info.has_position:
            return False
        
        # 检查3: 不能触发风控
        if pair_info.risk_triggered:
            return False
        
        # 检查4: 冷却期
        if cooldown_days > 0 and pair_info.get_idle_days(self.algorithm) < cooldown_days:
            return False
        
        return True
    
    def get_pair_info(self, symbol1: Symbol, symbol2: Symbol) -> Optional[PairInfo]:
        """
        获取配对信息
        
        Args:
            symbol1, symbol2: 配对的两只股票
            
        Returns:
            PairInfo: 配对信息，不存在返回None
        """
        pair_key = self._get_pair_key(symbol1, symbol2)
        return self.all_pairs.get(pair_key)
    
    def get_paired_symbol(self, symbol: Symbol) -> Optional[Symbol]:
        """
        获取配对的另一只股票
        
        快速查找某只股票的配对股票
        
        Args:
            symbol: 股票代码
            
        Returns:
            Symbol: 配对的另一只股票，不存在返回None
        """
        return self.symbol_map.get(symbol)
    
    def _get_pair_key(self, symbol1: Symbol, symbol2: Symbol) -> Tuple[Symbol, Symbol]:
        """
        获取标准化的配对键（按字母顺序）
        
        确保(A,B)和(B,A)映射到同一个键
        
        Args:
            symbol1, symbol2: 配对的两只股票
            
        Returns:
            Tuple[Symbol, Symbol]: 标准化的配对键
        """
        if symbol1.Value > symbol2.Value:
            return (symbol2, symbol1)
        return (symbol1, symbol2)
    
    def _rebuild_symbol_map(self):
        """重建双向映射，便于快速查找配对关系"""
        self.symbol_map.clear()
        for symbol1, symbol2 in self.all_pairs.keys():
            self.symbol_map[symbol1] = symbol2
            self.symbol_map[symbol2] = symbol1
    
    def get_summary_stats(self) -> Dict:
        """
        获取账本汇总统计
        
        Returns:
            Dict: 各类统计信息
        """
        stats = {
            'total_pairs': len(self.all_pairs),
            'current_round_pairs': sum(1 for p in self.all_pairs.values() if p.is_current_round),
            'active_pairs': sum(1 for p in self.all_pairs.values() if p.has_position),
            'risk_triggered_pairs': sum(1 for p in self.all_pairs.values() if p.risk_triggered),
            'avg_discovery_count': sum(p.discovery_count for p in self.all_pairs.values()) / len(self.all_pairs) if self.all_pairs else 0,
            'avg_trade_count': sum(p.trade_count for p in self.all_pairs.values()) / len(self.all_pairs) if self.all_pairs else 0
        }
        return stats