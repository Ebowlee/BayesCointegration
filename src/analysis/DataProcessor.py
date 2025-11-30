# region imports
from AlgorithmImports import *
import numpy as np
import pandas as pd
from typing import Dict, List, Tuple
from collections import defaultdict
# endregion


class DataProcessor:
    """数据处理器 - 负责历史数据的获取和预处理 (v8.4.0: 下载312天数据)"""

    def __init__(self, algorithm, analysis_config):
        """
        初始化数据处理器 (v8.4.0: 适配时间窗口切割)

        Args:
            algorithm: QCAlgorithm实例
            analysis_config: DataProcessorConfig dataclass实例
        """
        self.algorithm = algorithm
        self.total_lookback_days = analysis_config.total_lookback_days  # v8.4.0: 312天总窗口
        self.data_completeness_ratio = analysis_config.data_completeness_ratio
        self.max_annualized_volatility = analysis_config.max_annualized_volatility
        self.max_daily_drawdown = analysis_config.max_daily_drawdown


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

            return symbol_ohlcv

        except Exception as e:
            self.algorithm.Debug(f"[DataProcessor] 处理{symbol.Value}失败: {str(e)}")
            statistics['data_missing'] += 1
            return None


    def _download_historical_data(self, symbols: List[Symbol]):
        """
        下载历史OHLCV数据 (v8.4.0: 下载312天数据用于时间窗口切割)
        返回多级索引DataFrame或None（失败时）
        """
        try:
            return self.algorithm.History(symbols, self.total_lookback_days, Resolution.Daily)
        except Exception as e:
            self.algorithm.Debug(f"[DataProcessor] OHLCV数据下载失败: {str(e)}")
            return None


    def _validate_data(self, data: pd.DataFrame) -> Tuple[bool, str]:
        """
        验证数据完整性和合理性 (v8.4.0: 验证312天数据)

        要求:
        1. 必须有close列
        2. 恰好312天数据 (v8.4.0: 协整252天 + MCMC 60天)
        3. 无任何缺失值(NaN)
        4. 所有价格>0
        5. 年化波动率 <= 0.8 (80%)
        6. 单日最大跌幅 >= -0.20 (-20%)

        Returns:
            (是否有效, 失败原因)
        """
        # 检查close列存在
        if 'close' not in data.columns:
            return False, 'data_missing'

        # 检查长度 (v8.4.0: 恰好312天)
        if len(data) != self.total_lookback_days:
            return False, 'incomplete'

        close_series = data['close']

        # 检查缺失值(严格模式: 不允许任何NaN)
        if close_series.isnull().any():
            return False, 'has_missing_values'

        # 检查价格合理性(所有价格必须>0)
        if (close_series <= 0).any():
            return False, 'invalid_values'

        # 检查5: 年化波动率
        daily_returns = close_series.pct_change().dropna()
        if len(daily_returns) > 0:
            annualized_volatility = daily_returns.std() * np.sqrt(252)
            if annualized_volatility > self.max_annualized_volatility:
                return False, 'high_volatility'

        # 检查6: 单日极端跌幅
        if len(daily_returns) > 0:
            min_return = daily_returns.min()
            if min_return < self.max_daily_drawdown:
                return False, 'extreme_drawdown'

        return True, ''