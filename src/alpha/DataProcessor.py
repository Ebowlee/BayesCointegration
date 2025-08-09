# region imports
from AlgorithmImports import *
import pandas as pd
from typing import Dict, List, Tuple
from collections import defaultdict
# endregion


# =============================================================================
# 数据处理模块 - DataProcessor
# =============================================================================
class DataProcessor:
    """
    数据处理器类 - 负责历史数据的获取和预处理
    
    该类是AlphaModel的第一道防线, 确保后续分析基于高质量的数据。
    主要职责包括数据下载、完整性检查和异常值处理。
    
    处理流程:
    1. 数据下载: 获取lookback_period天的历史OHLCV数据
    2. 完整性检查: 要求至少98%的数据点存在
    3. 合理性检查: 剔除价格为负或零的异常数据
    4. 缺失值填补: 使用线性插值和前向/后向填充
    
    配置参数:
    - lookback_period: 历史数据回望天数(默认252天)
    - min_data_completeness_ratio: 最低数据完整性要求(默认0.98)
    
    使用示例:
        processor = DataProcessor(algorithm, config)
        result = processor.process(symbols)
        clean_data = result['clean_data']  # 清洗后的数据
        valid_symbols = result['valid_symbols']  # 通过筛选的股票
    
    注意事项:
    - 数据下载可能因网络或API限制失败, 需要容错处理
    - 填补方法的顺序很重要：先插值, 后填充
    """
    
    def __init__(self, algorithm, config: dict):
        """
        初始化数据处理器
        
        Args:
            algorithm: QuantConnect算法实例
            config: 包含数据处理相关配置的字典
        """
        self.algorithm = algorithm
        self.lookback_period = config['lookback_period']
        self.min_data_completeness_ratio = config['min_data_completeness_ratio']
    
    def process(self, symbols: List[Symbol]) -> Dict:
        """
        执行完整的数据处理流程
        
        处理步骤:
        1. 下载历史数据
        2. 数据完整性检查 (98%规则)
        3. 数据合理性检查 (无负值)
        4. 空缺填补
        
        Args:
            symbols: 待处理的股票列表
            
        Returns:
            dict: 包含clean_data, valid_symbols和statistics的字典
        """
        # 使用defaultdict简化统计
        statistics = defaultdict(int, total=len(symbols))
        
        # 下载历史数据
        all_histories = self._download_historical_data(symbols)
        if all_histories is None:
            return {'clean_data': {}, 'valid_symbols': [], 'statistics': dict(statistics)}
        
        clean_data = {}
        valid_symbols = []
        
        # 处理每个symbol
        for symbol in symbols:
            result = self._process_symbol(symbol, all_histories, statistics)
            if result is not None:
                clean_data[symbol] = result
                valid_symbols.append(symbol)
        
        statistics['final_valid'] = len(valid_symbols)
        self._log_statistics(dict(statistics))
        
        return {
            'clean_data': clean_data,
            'valid_symbols': valid_symbols,
            'statistics': dict(statistics)
        }
    
    def _process_symbol(self, symbol: Symbol, all_histories, statistics: dict):
        """
        处理单个股票, 返回处理后的数据或None
        """
        try:
            # 检查数据存在性
            if symbol not in all_histories.index.get_level_values(0):
                statistics['data_missing'] += 1
                return None
            
            symbol_data = all_histories.loc[symbol]
            
            # 统一的数据验证
            is_valid, reason = self._validate_data(symbol_data)
            if not is_valid:
                statistics[reason] += 1
                return None
            
            # 填补缺失值
            symbol_data = self._fill_missing_values(symbol_data)
            
            return symbol_data
            
        except Exception as e:
            pass  # 静默处理单个股票错误
            statistics['data_missing'] += 1
            return None
    
    def _download_historical_data(self, symbols: List[Symbol]):
        """
        下载历史数据
        """
        try:
            return self.algorithm.History(symbols, self.lookback_period, Resolution.Daily)
        except Exception as e:
            self.algorithm.Debug(f"[错误] 数据下载失败: {str(e)}")
            return None
    
    def _validate_data(self, data: pd.DataFrame) -> Tuple[bool, str]:
        """
        验证数据完整性和合理性
        
        Returns:
            (是否有效, 失败原因)
        """
        # 检查close列
        if 'close' not in data.columns:
            return False, 'data_missing'
        
        # 检查完整性
        required_length = int(self.lookback_period * self.min_data_completeness_ratio)
        if len(data) < required_length:
            return False, 'incomplete'
        
        # 检查合理性
        if (data['close'] <= 0).any():
            return False, 'invalid_values'
        
        return True, ''
    
    def _fill_missing_values(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        填补空缺值
        """
        if data['close'].isnull().any():
            # 一次性完成所有填充操作
            data['close'] = (data['close']
                            .interpolate(method='linear', limit_direction='both')
                            .fillna(method='pad')
                            .fillna(method='bfill'))
        return data
    
    def _log_statistics(self, stats: dict):
        """
        输出统计信息
        """
        failed_details = [
            f"{reason}({count})" 
            for reason, count in stats.items() 
            if reason not in ['total', 'final_valid'] and count > 0
        ]
        
        self.algorithm.Debug(
            f"[AlphaModel.Data] 数据处理: {stats['total']}→{stats['final_valid']}只 ({', '.join(failed_details)})"
        )