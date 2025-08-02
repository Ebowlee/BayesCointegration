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
        
        # 持仓时间跟踪
        self.entry_time = None         # 建仓时间（None表示无持仓）
        self.last_exit_time = None     # 最近平仓时间（用于冷却期）
        
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
        if not self.entry_time:
            return 0
        
        # 实时检查是否有持仓
        h1 = algorithm.Portfolio[self.symbol1]
        h2 = algorithm.Portfolio[self.symbol2]
        if not (h1.Invested and h2.Invested):
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
        if not self.last_exit_time:
            return 0
            
        # 实时检查是否有持仓
        h1 = algorithm.Portfolio[self.symbol1]
        h2 = algorithm.Portfolio[self.symbol2]
        if h1.Invested and h2.Invested:
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
        
        # 实时判断是否有持仓
        has_position = h1.Invested and h2.Invested
        
        # 自动管理entry_time和last_exit_time
        if has_position and not self.entry_time:
            # 刚建仓
            self.entry_time = algorithm.Time
        elif not has_position and self.entry_time:
            # 刚平仓
            if self.entry_time:
                holding_days = (algorithm.Time - self.entry_time).days
                self.holding_days_total += holding_days
                self.trade_count += 1
            self.entry_time = None
            self.last_exit_time = algorithm.Time
        
        # 计算整对的持仓价值
        total_value = abs(h1.HoldingsValue) + abs(h2.HoldingsValue)
        
        return {
            'has_position': has_position,
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
        
        # 所有配对信息 {(symbol1_value, symbol2_value): PairInfo}
        # 使用Symbol.Value作为键，避免Symbol对象身份比较问题
        self.all_pairs: Dict[Tuple[str, str], PairInfo] = {}
        
        # 配对双向映射 {symbol_value: symbol_value} - 快速查找配对关系
        self.symbol_map: Dict[str, str] = {}
    
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
        new_discovered_pairs = []  # 记录新发现的配对
        
        for symbol1, symbol2 in new_pairs:
            pair_key = self._get_pair_key(symbol1, symbol2)
            current_round_pairs.add(pair_key)
            
            # 新配对
            if pair_key not in self.all_pairs:
                # 传递原始Symbol对象给PairInfo
                self.all_pairs[pair_key] = PairInfo(symbol1, symbol2)
                new_discovered_pairs.append(f"{pair_key[0]}-{pair_key[1]}")
            
            # 更新发现状态
            self.all_pairs[pair_key].is_current_round = True
            self.all_pairs[pair_key].discovery_count += 1
        
        # 清理长期未被发现且无持仓的配对（可选）
        # self._cleanup_dormant_pairs(current_round_pairs)
        
        # 重建symbol映射
        self._rebuild_symbol_map()
        
        # 构建持续追踪的配对列表（有持仓的配对）
        tracking_pairs = []
        for pair_info in self.all_pairs.values():
            if pair_info.get_position_status(self.algorithm)['has_position']:
                tracking_pairs.append(f"{pair_info.symbol1.Value}-{pair_info.symbol2.Value}")
        
        # 优化日志输出
        if new_discovered_pairs:
            new_pairs_str = f"本轮新发现[{', '.join(new_discovered_pairs)}]"
        else:
            new_pairs_str = "本轮无新发现"
            
        if tracking_pairs:
            tracking_str = f", 持续追踪[{', '.join(tracking_pairs)}]"
        else:
            tracking_str = ""
            
        self.algorithm.Debug(
            f"[PairLedger] {new_pairs_str}{tracking_str}, "
            f"总计管理{len(self.all_pairs)}对"
        )
    
    def get_risk_control_data(self) -> List[Dict]:
        """
        获取所有配对的风控数据
        
        供RiskManagementModel进行风控检查
        
        Returns:
            List[Dict]: 所有持仓配对的风控相关数据，按持仓天数降序
        """
        risk_data = []
        
        for pair_info in self.all_pairs.values():
            status = pair_info.get_position_status(self.algorithm)
            
            # 只返回有持仓的配对
            if not status['has_position']:
                continue
            
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
        count = 0
        for pair_info in self.all_pairs.values():
            status = pair_info.get_position_status(self.algorithm)
            if status['has_position']:
                count += 1
        return count
    
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
        # 注释掉此检查，因为选股是每月一次，但信号生成是每天进行
        # AlphaModel只会对当前管理的配对生成信号，所以这个检查是多余的
        # if not pair_info.is_current_round:
        #     return False
        
        # 检查2: 不能已有持仓（实时检查）
        status = pair_info.get_position_status(self.algorithm)
        if status['has_position']:
            return False
        
        # 检查3: 冷却期
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
        result = self.all_pairs.get(pair_key)
        return result
    
    def get_paired_symbol(self, symbol: Symbol) -> Optional[str]:
        """
        获取配对的另一只股票
        
        快速查找某只股票的配对股票
        
        Args:
            symbol: 股票代码
            
        Returns:
            str: 配对的另一只股票的Value，不存在返回None
        """
        return self.symbol_map.get(symbol.Value)
    
    def _get_pair_key(self, symbol1: Symbol, symbol2: Symbol) -> Tuple[str, str]:
        """
        获取标准化的配对键（按字母顺序）
        
        确保(A,B)和(B,A)映射到同一个键
        
        Args:
            symbol1, symbol2: 配对的两只股票
            
        Returns:
            Tuple[str, str]: 标准化的配对键（使用Symbol.Value）
        """
        value1, value2 = symbol1.Value, symbol2.Value
        if value1 > value2:
            return (value2, value1)
        return (value1, value2)
    
    def _rebuild_symbol_map(self):
        """重建双向映射，便于快速查找配对关系"""
        self.symbol_map.clear()
        for value1, value2 in self.all_pairs.keys():
            self.symbol_map[value1] = value2
            self.symbol_map[value2] = value1
    
    def get_summary_stats(self) -> Dict:
        """
        获取账本汇总统计
        
        Returns:
            Dict: 各类统计信息
        """
        stats = {
            'total_pairs': len(self.all_pairs),
            'current_round_pairs': sum(1 for p in self.all_pairs.values() if p.is_current_round),
            'active_pairs': self.get_active_pairs_count(),
            'avg_discovery_count': sum(p.discovery_count for p in self.all_pairs.values()) / len(self.all_pairs) if self.all_pairs else 0,
            'avg_trade_count': sum(p.trade_count for p in self.all_pairs.values()) / len(self.all_pairs) if self.all_pairs else 0
        }
        return stats