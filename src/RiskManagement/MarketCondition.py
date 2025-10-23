"""
MarketCondition - 市场条件检查器

检查市场环境是否适合开仓（不继承RiskRule基类）

职责:
- 获取VIX恐慌指数（前瞻性指标）
- 计算SPY年化波动率（滞后性指标）
- 使用OR逻辑判断是否允许开仓
- 不影响平仓逻辑

特性:
- 无冷却期（实时计算）
- 返回bool而非(triggered, description)
- 数据不足时默认允许开仓
- OR逻辑：VIX > 30 或 HistVol > 25% 任一触发即阻止
"""

from AlgorithmImports import *
from typing import Optional
import numpy as np


class MarketCondition:
    """
    市场条件检查器

    不继承RiskRule基类，使用独立接口

    设计原则:
    - 开仓前置条件：只影响新开仓，不影响平仓
    - 实时计算：无状态，无冷却期
    - OR逻辑：领先指标(VIX) + 滞后指标(HistVol)
    - 数据容错：数据不足时默认允许开仓

    使用示例:
    ```python
    # 在main.py的OnData()中
    if not self.risk_manager.is_safe_to_open_positions():
        return  # 高波动时阻止开仓，但允许平仓继续
    ```
    """

    def __init__(self, algorithm, config):
        """
        初始化市场条件检查器

        Args:
            algorithm: QuantConnect算法实例
            config: StrategyConfig配置对象
        """
        self.algorithm = algorithm
        self.config = config

        # 从config.risk_management['market_condition']读取配置
        mc_config = config.risk_management.get('market_condition', {})
        self.enabled = mc_config.get('enabled', True)
        self.vix_threshold = mc_config.get('vix_threshold', 30)
        self.hist_vol_threshold = mc_config.get('spy_volatility_threshold', 0.25)
        self.window_size = mc_config.get('spy_volatility_window', 20)

        self.algorithm.Debug(
            f"[MarketCondition] 初始化: "
            f"enabled={self.enabled}, VIX阈值={self.vix_threshold}, "
            f"HistVol阈值={self.hist_vol_threshold*100:.0f}%, 窗口={self.window_size}天"
        )


    def is_safe_to_open_positions(self) -> bool:
        """
        判断当前市场条件是否适合开仓

        OR逻辑:
        - VIX >= vix_threshold (默认30)
        - HistVol >= hist_vol_threshold (默认0.25)
        - 任一条件触发 → return False

        Returns:
            True: 市场条件良好，允许开仓
            False: 市场条件不佳，禁止开仓

        逻辑流程:
        1. 检查enabled开关
        2. 获取VIX值
        3. 计算SPY年化波动率
        4. OR逻辑判断
        5. 记录日志
        6. 返回判断结果

        注意:
        - 数据不足时默认返回True（允许开仓）
        - 只在debug_mode=True时输出详细指标值
        - 触发阻断时必然输出警告日志
        """
        # 全局禁用时，直接允许
        if not self.enabled:
            return True

        # 获取VIX值
        vix = self._get_vix_value()

        # 获取历史波动率
        hist_vol = self._get_spy_annualized_volatility()

        # 数据完全不足时，保守起见阻止开仓
        if vix is None and hist_vol is None:
            self.algorithm.Debug(
                f"[MarketCondition] VIX和HistVol数据均不足，保守起见阻止开仓"
            )
            return False

        # Debug模式下输出详细指标值
        if self.config.main.get('debug_mode', False):
            vix_str = f"{vix:.1f}" if vix is not None else "N/A"
            vol_str = f"{hist_vol*100:.1f}%" if hist_vol is not None else "N/A"
            self.algorithm.Debug(
                f"[MarketCondition] VIX={vix_str}, HistVol={vol_str}"
            )

        # === OR逻辑判断 ===

        # 判断1：VIX恐慌触发
        if vix is not None and vix >= self.vix_threshold:
            self.algorithm.Debug(
                f"[MarketCondition] VIX恐慌，暂停开仓: "
                f"VIX={vix:.1f} >= {self.vix_threshold}"
            )
            return False

        # 判断2：历史波动率触发
        if hist_vol is not None and hist_vol >= self.hist_vol_threshold:
            self.algorithm.Debug(
                f"[MarketCondition] 波动率过高，暂停开仓: "
                f"{hist_vol*100:.1f}% >= {self.hist_vol_threshold*100:.0f}%"
            )
            return False

        # 两个条件都未触发，允许开仓
        return True


    def _get_vix_value(self) -> Optional[float]:
        """
        获取最新VIX值

        Returns:
            VIX值（如30.5），数据不足时返回None

        实现方式:
        1. 通过algorithm.vix_symbol访问VIX数据
        2. 使用History获取最近1天的收盘价
        3. 返回最新VIX值

        注意:
        - VIX是指数，直接读取close价格
        - 数据不足或异常时返回None
        - 需要main.py在Initialize()中订阅VIX
        """
        try:
            # 检查algorithm是否有vix_symbol属性
            if not hasattr(self.algorithm, 'vix_symbol'):
                return None

            vix_symbol = self.algorithm.vix_symbol

            # 获取最近1天的VIX数据
            history = self.algorithm.History(vix_symbol, 1, Resolution.Daily)

            # 检查数据完整性
            if history.empty:
                return None

            # 提取VIX收盘价（VIX本身就是波动率指数）
            vix_value = history['close'].iloc[-1]

            return float(vix_value)

        except Exception as e:
            # VIX数据异常时记录日志并返回None
            if self.config.main.get('debug_mode', False):
                self.algorithm.Debug(
                    f"[MarketCondition] VIX获取异常: {str(e)}"
                )
            return None


    def _get_spy_annualized_volatility(self) -> Optional[float]:
        """
        获取SPY年化波动率

        Returns:
            年化波动率（小数形式，如0.25表示25%）
            数据不足时返回None

        计算方法:
        1. 获取SPY最近(window_size+1)天的收盘价
        2. 计算日收益率: returns = (price[t] - price[t-1]) / price[t-1]
        3. 计算日收益率标准差: daily_std = std(returns)
        4. 年化: annualized_vol = daily_std * sqrt(252)

        注意:
        - 需要window_size+1天数据（计算window_size个收益率）
        - 使用252个交易日年化（美股标准）
        - 数据不足或异常时返回None
        """
        try:
            # 获取SPY历史数据（需要window_size+1天计算window_size个收益率）
            spy = self.algorithm.market_benchmark
            history = self.algorithm.History(spy, self.window_size + 1, Resolution.Daily)

            # 检查数据完整性
            if history.empty or len(history) < self.window_size + 1:
                return None

            # 提取收盘价
            prices = history['close'].values

            # 计算日收益率: (price[t] - price[t-1]) / price[t-1]
            returns = np.diff(prices) / prices[:-1]

            # 计算标准差
            daily_std = np.std(returns)

            # 年化（252个交易日）
            annualized_vol = daily_std * np.sqrt(252)

            return annualized_vol

        except Exception as e:
            # 计算异常时记录日志并返回None（默认允许开仓）
            if self.config.main.get('debug_mode', False):
                self.algorithm.Debug(
                    f"[MarketCondition] 波动率计算异常: {str(e)}"
                )
            return None
