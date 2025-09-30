# region imports
from AlgorithmImports import *
from typing import Generator
from datetime import timedelta
from collections import deque
import numpy as np
from src.Pairs import PositionMode
# endregion


class PortfolioLevelRiskManager:
    """组合层面风控管理器"""

    def __init__(self, algorithm, config):
        self.algorithm = algorithm
        self.config = config
        self.portfolio_high_water_mark = config.main['cash']

        # 市场波动率滚动窗口
        self.market_window_size = 20  
        self.market_price_window = deque(maxlen=self.market_window_size)
        self.market_window_initialized = False  


    def is_account_blowup(self) -> bool:
        """检查是否触发爆仓线"""
        portfolio_value = self.algorithm.Portfolio.TotalPortfolioValue
        initial_capital = self.config.main['cash']
        loss_ratio = (initial_capital - portfolio_value) / initial_capital
        return loss_ratio > self.config.risk_management['blowup_threshold']


    def is_excessive_drawdown(self) -> bool:
        """检查是否超过最大回撤"""
        portfolio_value = self.algorithm.Portfolio.TotalPortfolioValue
        self.portfolio_high_water_mark = max(self.portfolio_high_water_mark, portfolio_value)

        if self.portfolio_high_water_mark > 0:
            drawdown = (self.portfolio_high_water_mark - portfolio_value) / self.portfolio_high_water_mark
            return drawdown > self.config.risk_management['drawdown_threshold']
        return False


    def _initialize_market_window(self):
        """初始化市场价格窗口（首次调用时）"""
        if self.market_window_initialized:
            return

        history = self.algorithm.History(self.algorithm.market_benchmark, self.market_window_size, Resolution.Daily)

        if not history.empty and 'close' in history:
            closes = history['close'].values
            for price in closes:
                self.market_price_window.append(price)
            self.market_window_initialized = True


    def is_high_market_volatility(self) -> bool:
        """
        检查市场剧烈波动 - 使用滚动窗口计算历史波动率
        """
        if not self.algorithm.Securities.ContainsKey(self.algorithm.market_benchmark):
            return False

        spy = self.algorithm.Securities[self.algorithm.market_benchmark]

        # 首次运行时初始化窗口
        if not self.market_window_initialized:
            self._initialize_market_window()
            if not self.market_window_initialized:
                return False

        # 更新滚动窗口（添加最新收盘价）
        if spy.Close > 0:
            self.market_price_window.append(spy.Close)

        # 需要完整的窗口数据（20个数据点）
        if len(self.market_price_window) < self.market_window_size:
            return False

        # 计算历史波动率
        prices = np.array(self.market_price_window)
        returns = np.diff(np.log(prices))  
        annualized_vol = np.std(returns) * np.sqrt(252)

        MARKET_VOLATILITY_THRESHOLD = self.config.risk_management['market_volatility_threshold']

        return annualized_vol > MARKET_VOLATILITY_THRESHOLD


    def check_sector_concentration(self) -> dict:
        """
        检查行业集中度是否超限
        返回: {sector: {'concentration': float, 'pairs': list, 'target_ratio': float}}
        只返回需要调整的行业
        """
        sectors_to_adjust = {}
        sector_concentrations = self.algorithm.pairs_manager.get_sector_concentration()
        threshold = self.config.risk_management['sector_exposure_threshold']
        target = self.config.risk_management['sector_target_exposure']

        for sector, info in sector_concentrations.items():
            if info['concentration'] > threshold:
                # 计算目标调整比例
                target_ratio = target / info['concentration']
                sectors_to_adjust[sector] = {
                    'concentration': info['concentration'],
                    'pairs': info['pairs'],
                    'target_ratio': target_ratio
                }

        return sectors_to_adjust


class PairLevelRiskManager:
    """配对层面风控管理器"""

    def __init__(self, algorithm, config):
        self.algorithm = algorithm
        self.pair_high_water_marks = {}  
        self.config = config


    def check_holding_timeout(self, pair) -> bool:
        """
        检查持仓是否超期
        返回: 是否超期
        """
        holding_days = pair.get_pair_holding_days()
        if holding_days is not None and holding_days > pair.max_holding_days:
            return True
        return False

    def check_position_anomaly(self, pair) -> tuple:
        """
        检查持仓异常
        返回: (是否异常, 异常描述)
        """
        position_info = pair.get_position_info()
        mode = position_info['position_mode']

        if mode == PositionMode.PARTIAL_LEG1:
            return True, f"单边持仓LEG1(qty={position_info['qty1']:+.0f})"
        elif mode == PositionMode.PARTIAL_LEG2:
            return True, f"单边持仓LEG2(qty={position_info['qty2']:+.0f})"
        elif mode == PositionMode.ANOMALY_SAME:
            return True, f"同向交易({position_info['qty1']:+.0f}/{position_info['qty2']:+.0f})"

        return False, ""

    def check_pair_drawdown(self, pair) -> bool:
        """
        检查配对回撤是否超限
        返回: 是否超过最大回撤
        """
        if not pair.has_normal_position():
            return False

        pair_value = pair.get_position_value()
        pair_id = pair.pair_id

        # 更新HWM
        if pair_id not in self.pair_high_water_marks:
            self.pair_high_water_marks[pair_id] = pair_value
        else:
            self.pair_high_water_marks[pair_id] = max(
                self.pair_high_water_marks[pair_id], pair_value
            )

        # 检查回撤
        hwm = self.pair_high_water_marks[pair_id]
        if hwm > 0:
            drawdown = (hwm - pair_value) / hwm
            if drawdown > self.config['max_pair_drawdown']:
                return True

        return False


    def clear_pair_history(self, pair_id):
        """清理配对的历史记录（清仓后调用）"""
        if pair_id in self.pair_high_water_marks:
            del self.pair_high_water_marks[pair_id]