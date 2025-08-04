# region imports
from AlgorithmImports import *
from typing import Dict, List, Tuple
# endregion


class PairInfo:
    """
    配对信息类 - 跟踪单个配对的持仓状态
    
    主要功能:
    1. 跟踪配对的持仓状态（是否有仓位）
    2. 计算持仓时间用于风控
    3. 获取实时盈亏信息
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
        
        # 持仓时间跟踪
        self.entry_time = None         # 建仓时间（None表示无持仓）
    
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
        
        # 简化的时间管理：只在首次发现持仓时记录
        if has_position and not self.entry_time:
            # 首次发现有持仓
            self.entry_time = algorithm.Time
            algorithm.Debug(f"[PairLedger] 首次发现持仓 [{self.symbol1.Value},{self.symbol2.Value}]")
        elif not has_position and self.entry_time:
            # 检测到平仓
            holding_days = (algorithm.Time - self.entry_time).days
            self.entry_time = None
            algorithm.Debug(f"[PairLedger] 检测到平仓 [{self.symbol1.Value},{self.symbol2.Value}], 持仓{holding_days}天")
        
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
    
    def update_from_selection(self, new_pairs: List[Tuple[Symbol, Symbol]]):
        """
        更新配对信息
        
        由AlphaModel在每轮选股后调用
        
        Args:
            new_pairs: 新选出的配对列表，每个元素为 (symbol1, symbol2)
        """
        # 处理新发现的配对
        new_discovered_pairs = []  # 记录新发现的配对
        
        for symbol1, symbol2 in new_pairs:
            pair_key = self._get_pair_key(symbol1, symbol2)
            
            # 新配对
            if pair_key not in self.all_pairs:
                # 传递原始Symbol对象给PairInfo
                self.all_pairs[pair_key] = PairInfo(symbol1, symbol2)
                new_discovered_pairs.append(f"{pair_key[0]}-{pair_key[1]}")
        
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
                'pair_info': pair_info,  # 引用
                'holding_days': status['holding_days'],
                'total_pnl': status['total_pnl'],
                'total_pnl_percent': status['total_pnl_percent'],
                'details': status
            })
        
        # 按持仓天数降序排列
        return sorted(risk_data, key=lambda x: x['holding_days'], reverse=True)
    
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
