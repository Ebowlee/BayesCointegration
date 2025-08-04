# region imports
from AlgorithmImports import *
from typing import List, Tuple, Set
# endregion


class PairRegistry:
    """
    配对注册表 - 维护当前活跃的配对列表
    
    职责单一：
    1. 存储当前选股周期的配对列表
    2. 提供配对查询接口
    3. 不负责追踪持仓状态或交易历史
    
    使用者：
    - AlphaModel: 每月更新配对列表
    - RiskManagement: 查询当前配对，识别孤儿持仓
    """
    
    def __init__(self, algorithm):
        """
        初始化配对注册表
        
        Args:
            algorithm: 主算法实例
        """
        self.algorithm = algorithm
        # 当前活跃的配对列表 [(symbol1, symbol2), ...]
        self.active_pairs: List[Tuple[Symbol, Symbol]] = []
        # 最后更新时间
        self.last_update_time = None
    
    def update_pairs(self, new_pairs: List[Tuple[Symbol, Symbol]]):
        """
        更新配对列表
        
        由AlphaModel在每轮选股后调用
        
        Args:
            new_pairs: 新选出的配对列表，每个元素为 (symbol1, symbol2)
        """
        self.active_pairs = new_pairs.copy()
        self.last_update_time = self.algorithm.Time
        
        # 输出更新日志
        pairs_str = ", ".join([f"[{s1.Value},{s2.Value}]" for s1, s2 in new_pairs])
        self.algorithm.Debug(
            f"[PairRegistry] 更新配对列表({len(new_pairs)}对): {pairs_str}"
        )
    
    def get_active_pairs(self) -> List[Tuple[Symbol, Symbol]]:
        """
        获取当前活跃的配对列表
        
        Returns:
            List[Tuple[Symbol, Symbol]]: 配对列表
        """
        return self.active_pairs.copy()
    
    def get_all_symbols_in_pairs(self) -> Set[Symbol]:
        """
        获取所有在配对中的股票
        
        Returns:
            Set[Symbol]: 所有参与配对的股票集合
        """
        symbols = set()
        for symbol1, symbol2 in self.active_pairs:
            symbols.add(symbol1)
            symbols.add(symbol2)
        return symbols
    
    def is_symbol_in_pairs(self, symbol: Symbol) -> bool:
        """
        检查股票是否在任何配对中
        
        Args:
            symbol: 要检查的股票
            
        Returns:
            bool: 是否在配对中
        """
        for symbol1, symbol2 in self.active_pairs:
            if symbol == symbol1 or symbol == symbol2:
                return True
        return False
    
    def get_pair_for_symbol(self, symbol: Symbol) -> Optional[Tuple[Symbol, Symbol]]:
        """
        获取包含指定股票的配对
        
        Args:
            symbol: 股票代码
            
        Returns:
            Optional[Tuple[Symbol, Symbol]]: 包含该股票的配对，不存在返回None
        """
        for symbol1, symbol2 in self.active_pairs:
            if symbol == symbol1 or symbol == symbol2:
                return (symbol1, symbol2)
        return None
    
    def get_paired_symbol(self, symbol: Symbol) -> Optional[Symbol]:
        """
        获取配对的另一只股票
        
        Args:
            symbol: 股票代码
            
        Returns:
            Optional[Symbol]: 配对的另一只股票，不存在返回None
        """
        for symbol1, symbol2 in self.active_pairs:
            if symbol == symbol1:
                return symbol2
            elif symbol == symbol2:
                return symbol1
        return None
    
    def contains_pair(self, symbol1: Symbol, symbol2: Symbol) -> bool:
        """
        检查是否包含指定的配对
        
        Args:
            symbol1, symbol2: 配对的两只股票
            
        Returns:
            bool: 是否包含该配对
        """
        return ((symbol1, symbol2) in self.active_pairs or 
                (symbol2, symbol1) in self.active_pairs)