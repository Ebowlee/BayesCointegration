"""
MarketCondition - 市场条件检查器 (v7.28.1 VIX-Only前瞻指标)

检查市场环境是否适合开仓（不继承RiskRule基类）

职责:
- 获取VIX恐慌指数（前瞻性指标）
- 判断VIX是否超过阈值（35）
- 不影响平仓逻辑

特性:
- 无冷却期（实时计算）
- 返回bool而非(triggered, description)
- VIX无数据时默认允许开仓（激进策略）
- 单一指标：VIX >= 35 阻止开仓，VIX >= 30 警告
- v7.28.1移除：HistVol后置指标（滞后，无预测价值）
"""

from AlgorithmImports import *
from typing import Optional


class MarketCondition:
    """
    市场条件检查器 (v7.28.1 VIX-Only)

    不继承RiskRule基类，使用独立接口

    设计原则:
    - 开仓前置条件：只影响新开仓，不影响平仓
    - 实时计算：无状态，无冷却期
    - VIX-Only前瞻指标：移除后置HistVol
    - 数据容错：VIX无数据时默认允许开仓（激进策略）

    使用示例:
    ```python
    # 在main.py的OnData()中
    if not self.risk_manager.market_condition.is_safe_to_open_positions():
        return  # VIX恐慌时阻止开仓，但允许平仓继续
    ```
    """

    def __init__(self, algorithm, config):
        """
        初始化市场条件检查器 (v7.28.1 简化配置)

        Args:
            algorithm: QuantConnect算法实例
            config: StrategyConfig配置对象
        """
        self.algorithm = algorithm
        self.config = config

        # 从config.risk_management['market_condition']读取配置
        mc_config = config.risk_management['market_condition']
        self.enabled = mc_config['enabled']
        self.vix_threshold = mc_config['vix_threshold']              # v7.28.1: 阻止开仓阈值=35
        self.vix_warning_threshold = mc_config['vix_warning_threshold']  # v7.28.1: 警告阈值=30



    def is_safe_to_open_positions(self) -> bool:
        """
        判断当前市场条件是否适合开仓 (v7.28.1 VIX-Only)

        单一指标逻辑:
        - VIX >= vix_threshold (默认35): 阻止开仓
        - vix_warning_threshold <= VIX < vix_threshold (默认30-35): 警告 + 允许开仓
        - VIX < vix_warning_threshold (默认30): 正常开仓
        - VIX无数据: 允许开仓（激进策略）

        Returns:
            True: 市场条件良好，允许开仓
            False: 市场恐慌，禁止开仓

        逻辑流程:
        1. 检查enabled开关
        2. 获取VIX值
        3. VIX无数据 → 允许开仓
        4. VIX >= vix_threshold → 阻止开仓（level 0日志）
        5. vix_warning_threshold <= VIX < vix_threshold → 警告 + 允许开仓（level 0日志）
        6. VIX < vix_warning_threshold → 正常开仓

        注意:
        - v7.28.1移除HistVol检查（后置指标无预测价值）
        - 阈值和警告线均可配置（默认35和30）
        - 所有日志为level 0（风控级别）
        """
        # 全局禁用时，直接允许
        if not self.enabled:
            return True

        # 获取VIX值
        vix = self._get_vix_value()

        # VIX无数据时，激进策略：允许开仓
        if vix is None:
            return True

        # VIX >= vix_threshold: 阻止开仓（level 0日志）
        if vix >= self.vix_threshold:
            self.algorithm.Debug(
                f"[MarketCondition] VIX恐慌，暂停开仓: "
                f"VIX={vix:.1f} >= {self.vix_threshold}"
            )
            return False

        # vix_warning_threshold <= VIX < vix_threshold: 警告 + 允许开仓（level 0日志）
        elif vix >= self.vix_warning_threshold:
            self.algorithm.Debug(
                f"[MarketCondition] 警告: VIX={vix:.1f} 接近阈值{self.vix_threshold} "
                f"(警告线{self.vix_warning_threshold})"
            )

        # VIX < vix_warning_threshold: 正常开仓（无日志，减少噪音）
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
            if getattr(self.config.main, 'debug_mode', False):
                self.algorithm.Debug(
                    f"[MarketCondition] VIX获取异常: {str(e)}"
                )
            return None
