# region imports
from AlgorithmImports import *
from typing import Dict
# endregion


# =============================================================================
# 状态管理类 - AlphaModelState
# =============================================================================
class AlphaModelState:
    """
    集中管理 AlphaModel 的所有状态
    
    将状态分为三类：
    1. persistent: 持久状态，跨周期保持（如活跃配对、历史后验）
    2. temporary: 临时状态，选股日使用后清理（如原始数据、中间结果）
    3. control: 控制状态，管理流程控制（如选股标志）
    """
    
    def __init__(self):
        # 持久状态 - 每天都需要的数据
        self.persistent = {
            'modeled_pairs': [],           # 当前活跃的配对（带模型参数）
            'previous_modeled_pairs': [],  # 上期活跃的配对（用于检测过期）
            'historical_posteriors': {},   # 历史后验参数 {(symbol1, symbol2): {...}}
            'zscore_ema': {}              # Z-score EMA值 {(symbol1, symbol2): float}
        }
        
        # 临时状态 - 选股日的中间数据
        self.temporary = {
            'clean_data': {},             # 处理后的历史数据 {Symbol: DataFrame}
            'valid_symbols': [],          # 通过筛选的股票列表
            'cointegrated_pairs': []      # 协整配对（未建模）
        }
        
        # 控制状态 - 流程控制
        self.control = {
            'is_selection_day': False,    # 是否为选股日
            'symbols': []                 # 当前跟踪的股票列表
        }
    
    def clear_temporary(self):
        """选股完成后清理临时数据，释放内存"""
        self.temporary = {
            'clean_data': {},
            'valid_symbols': [],
            'cointegrated_pairs': []
        }
    
    def get_required_data(self) -> Dict:
        """只保留活跃配对所需的数据"""
        required_symbols = set()
        
        # 收集所有活跃配对的股票
        for pair in self.persistent['modeled_pairs']:
            required_symbols.add(pair['symbol1'])
            required_symbols.add(pair['symbol2'])
        
        # 只返回这些股票的数据
        return {
            symbol: data 
            for symbol, data in self.temporary['clean_data'].items()
            if symbol in required_symbols
        }
    
    def update_persistent_data(self, key: str, value):
        """更新持久状态"""
        self.persistent[key] = value
    
    def update_temporary_data(self, key: str, value):
        """更新临时状态"""
        self.temporary[key] = value
    
    def update_control_state(self, key: str, value):
        """更新控制状态"""
        self.control[key] = value