# region imports
from AlgorithmImports import *
import pandas as pd
from typing import Dict, List, Tuple
from collections import defaultdict
# endregion


class DataProcessor:
    """数据处理器 - 负责历史数据的获取和预处理"""

    def __init__(self, algorithm, shared_config: dict, module_config: dict):
        """
        初始化数据处理器

        Args:
            algorithm: QCAlgorithm实例
            shared_config: 共享配置(analysis_shared)
            module_config: 模块配置(data_processor)
        """
        self.algorithm = algorithm
        self.lookback_days = shared_config['lookback_days']
        self.data_completeness_ratio = module_config['data_completeness_ratio']


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
            self.algorithm.Debug(f"[DataProcessor] 处理{symbol.Value}失败: {str(e)}")
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
            self.algorithm.Debug(f"[DataProcessor] OHLCV数据下载失败: {str(e)}")
            return None


    def _validate_data(self, data: pd.DataFrame) -> Tuple[bool, str]:
        """
        验证数据完整性和合理性

        要求:
        1. 必须有close列
        2. 恰好252天数据
        3. 无任何缺失值(NaN)
        4. 所有价格>0

        Returns:
            (是否有效, 失败原因)
        """
        # 检查close列存在
        if 'close' not in data.columns:
            return False, 'data_missing'

        # 检查长度(恰好252天)
        if len(data) != self.lookback_days:
            return False, 'incomplete'

        close_series = data['close']

        # 检查缺失值(严格模式: 不允许任何NaN)
        if close_series.isnull().any():
            return False, 'has_missing_values'

        # 检查价格合理性(所有价格必须>0)
        if (close_series <= 0).any():
            return False, 'invalid_values'

        return True, ''


    def _fill_missing_values(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        填补缺失值

        注意: 严格模式(data_completeness_ratio=1.0)下,
        有NaN的股票在_validate_data阶段已被过滤,
        此方法实际不执行任何操作。

        保留此方法是为了:
        1. 代码结构完整性
        2. 未来可能放宽策略时使用

        Returns:
            原样返回data(严格模式下无修改)
        """
        # 严格模式: 不填补,直接返回
        # 理由: data_completeness_ratio=1.0要求100%完整,拒绝有NaN的数据
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
            f"[DataProcessor] 数据处理: {stats['total']}→{stats['final_valid']}只 ({', '.join(failure_reasons)})"
        )