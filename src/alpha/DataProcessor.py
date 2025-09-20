# region imports
from AlgorithmImports import *
import pandas as pd
from typing import Dict, List, Tuple, Symbol
from collections import defaultdict
# endregion


class DataProcessor:
    """数据处理器 - 负责历史数据的获取和预处理"""

    def __init__(self, algorithm, config: dict):
        self.algorithm = algorithm
        self.lookback_period = config['lookback_period']
        self.min_data_completeness_ratio = config['min_data_completeness_ratio']

    def process(self, symbols: List[Symbol]) -> Dict:
        """执行数据处理流程"""
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
        """处理单个股票"""
        try:
            # 检查数据存在性
            if symbol not in all_histories.index.get_level_values(0):
                statistics['data_missing'] += 1
                return None

            symbol_data = all_histories.loc[symbol]

            # 数据验证
            is_valid, reason = self._validate_data(symbol_data)
            if not is_valid:
                statistics[reason] += 1
                return None

            # 填补缺失值
            symbol_data = self._fill_missing_values(symbol_data)

            return symbol_data

        except Exception:
            statistics['data_missing'] += 1
            return None

    def _download_historical_data(self, symbols: List[Symbol]):
        """下载历史数据"""
        try:
            return self.algorithm.History(symbols, self.lookback_period, Resolution.Daily)
        except Exception as e:
            self.algorithm.Debug(f"[错误] 数据下载失败: {str(e)}")
            return None

    def _validate_data(self, data: pd.DataFrame) -> Tuple[bool, str]:
        """验证数据完整性和合理性"""
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
        """填补空缺值"""
        if data['close'].isnull().any():
            data['close'] = (data['close']
                            .interpolate(method='linear', limit_direction='both')
                            .fillna(method='pad')
                            .fillna(method='bfill'))
        return data

    def _log_statistics(self, stats: dict):
        """输出统计信息"""
        failed_details = [
            f"{reason}({count})"
            for reason, count in stats.items()
            if reason not in ['total', 'final_valid'] and count > 0
        ]

        self.algorithm.Debug(
            f"[AlphaModel.Data] 数据处理: {stats['total']}→{stats['final_valid']}只 ({', '.join(failed_details)})"
        )