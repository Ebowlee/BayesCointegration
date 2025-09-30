# region imports
from AlgorithmImports import *
import pandas as pd
from typing import Dict, List, Tuple
from collections import defaultdict
# endregion


class DataProcessor:
    """数据处理器 - 负责历史数据的获取和预处理"""

    def __init__(self, algorithm, config: dict):
        self.algorithm = algorithm
        self.lookback_days = config['lookback_days']
        self.data_completeness_ratio = config['data_completeness_ratio']


    def process(self, symbols: List[Symbol]) -> Dict:
        """
        执行数据处理流程
        返回包含clean_data, valid_symbols, statistics的字典
        """
        # 使用defaultdict简化统计
        statistics = defaultdict(int, total=len(symbols))

        # 下载历史OHLCV数据
        historical_ohlcv_data = self._download_historical_data(symbols)
        if historical_ohlcv_data is None:
            return {'clean_data': {}, 'valid_symbols': [], 'statistics': dict(statistics)}

        cleaned_data_dict = {}
        validated_symbols = []

        # 验证并清洗每个股票的OHLCV数据
        for symbol in symbols:
            processed_ohlcv = self._process_symbol(symbol, historical_ohlcv_data, statistics)
            if processed_ohlcv is not None:
                cleaned_data_dict[symbol] = processed_ohlcv
                validated_symbols.append(symbol)

        statistics['final_valid'] = len(validated_symbols)
        self._log_statistics(dict(statistics))

        return {'clean_data': cleaned_data_dict, 'valid_symbols': validated_symbols, 'statistics': dict(statistics)}


    def _process_symbol(self, symbol: Symbol, historical_ohlcv_data, statistics: dict):
        """
        处理单个股票的OHLCV数据
        返回处理后的DataFrame或None（失败时）
        """
        try:
            # 检查数据存在性
            if symbol not in historical_ohlcv_data.index.get_level_values(0):
                statistics['data_missing'] += 1
                return None

            symbol_ohlcv = historical_ohlcv_data.loc[symbol]

            # 验证数据完整性和合理性
            is_valid, reason = self._validate_data(symbol_ohlcv)
            if not is_valid:
                statistics[reason] += 1
                return None

            # 填补缺失值
            symbol_ohlcv = self._fill_missing_values(symbol_ohlcv)

            return symbol_ohlcv

        except Exception as e:
            self.algorithm.Debug(f"[DataProcessor] 处理{symbol.Value}失败: {str(e)}", 2)
            statistics['data_missing'] += 1
            return None


    def _download_historical_data(self, symbols: List[Symbol]):
        """
        下载历史OHLCV数据
        返回多级索引DataFrame或None（失败时）
        """
        try:
            return self.algorithm.History(symbols, self.lookback_days, Resolution.Daily)
        except Exception as e:
            self.algorithm.Debug(f"[DataProcessor] OHLCV数据下载失败: {str(e)}", 1)
            return None


    def _validate_data(self, data: pd.DataFrame) -> Tuple[bool, str]:
        """
        验证数据完整性和合理性
        返回(是否有效, 失败原因)
        """
        # 检查close列
        if 'close' not in data.columns:
            return False, 'data_missing'

        # 检查完整性
        required_length = int(self.lookback_days * self.data_completeness_ratio)
        if len(data) < required_length:
            return False, 'incomplete'

        # 检查合理性
        if (data['close'] <= 0).any():
            return False, 'invalid_values'

        return True, ''


    def _fill_missing_values(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        填补close列的缺失值
        使用线性插值和前后填充
        """
        if data['close'].isnull().any():
            data['close'] = (data['close'].interpolate(method='linear', limit_direction='both').ffill().bfill())
        return data


    def _log_statistics(self, stats: dict):
        """
        输出数据处理统计信息到Debug日志
        """
        failure_reasons = [
            f"{reason}({count})"
            for reason, count in stats.items()
            if reason not in ['total', 'final_valid'] and count > 0
        ]

        self.algorithm.Debug(
            f"[DataProcessor] 数据处理: {stats['total']}→{stats['final_valid']}只 ({', '.join(failure_reasons)})", 2
        )