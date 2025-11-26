"""
RiskManager - Portfolio级风控管理器 (v7.98.2)

职责:
    - Portfolio级风控: MarketCondition (VIX) + PortfolioDrawdown (HWM)
    - 状态管理: HWM追踪 + 冷却期追踪
    - 不负责Pair级健康检查 (由 PairsManager.check_pairs_health() 处理)

设计原则 (v7.98.2: 单一职责):
    - 检测方法只负责检测，不含冷却期判断
    - 冷却期查询作为独立公开方法
    - 由 main.py 协调调用顺序和执行决策
"""

from AlgorithmImports import *
from datetime import timedelta
from typing import Optional, Tuple


class RiskManager:
    """Portfolio级风控管理器"""

    def __init__(self, algorithm, config):
        """
        初始化风控管理器

        Args:
            algorithm: QCAlgorithm实例
            config: StrategyConfig配置对象
        """
        self.algorithm = algorithm
        self.config = config

        # === 从配置读取参数 (v7.98.1: 扁平化) ===
        cfg = config.risk_manager
        self.vix_enabled = cfg.vix_enabled
        self.vix_threshold = cfg.vix_threshold
        self.drawdown_enabled = cfg.drawdown_enabled
        self.drawdown_threshold = cfg.drawdown_threshold          # 0.15 = 15%
        self.drawdown_cooldown_days = cfg.drawdown_cooldown_days  # 360

        # === 状态变量 ===
        # Portfolio Drawdown: HWM追踪 (初始化为初始资金)
        self.high_water_mark = config.main.cash

        # 冷却期状态
        self.portfolio_cooldown_until: Optional[datetime] = None

    # =========================================================================
    # 公开方法: Portfolio级检查
    # =========================================================================

    def check_portfolio_drawdown(self) -> Tuple[bool, str]:
        """
        检查Portfolio回撤是否触发全仓平仓 (v7.98.2: 纯检测)

        逻辑:
            1. 更新HWM (如果当前净值更高)
            2. 计算回撤 = (HWM - 当前净值) / HWM
            3. 回撤 >= 阈值 → 触发 + 重置HWM

        注意: 调用者应先检查 is_in_portfolio_cooldown()

        Returns:
            (triggered, description)
            - triggered: 是否触发
            - description: 触发描述（用于日志）
        """
        if not self.drawdown_enabled:
            return False, ""

        # v7.98.2: 移除冷却期检查 (由调用者负责)

        portfolio_value = self.algorithm.Portfolio.TotalPortfolioValue

        # 更新HWM
        if portfolio_value > self.high_water_mark:
            self.high_water_mark = portfolio_value

        # 计算回撤
        if self.high_water_mark <= 0:
            return False, ""

        drawdown = (self.high_water_mark - portfolio_value) / self.high_water_mark

        # 判断是否触发
        if drawdown >= self.drawdown_threshold:
            description = (
                f"Portfolio回撤触发: {drawdown*100:.1f}% >= {self.drawdown_threshold*100:.0f}% "
                f"(HWM: ${self.high_water_mark:,.0f} → 当前: ${portfolio_value:,.0f})"
            )
            # 重置HWM为当前净值 (避免冷却期后循环触发)
            self.high_water_mark = portfolio_value
            return True, description

        return False, ""

    
    def is_in_portfolio_cooldown(self) -> bool:
        """
        查询是否在Portfolio冷却期内 (v7.98.2: 公开方法)

        Returns:
            True: 在冷却期内
            False: 不在冷却期
        """
        if self.portfolio_cooldown_until is None:
            return False
        return self.algorithm.Time < self.portfolio_cooldown_until

    
    def is_safe_to_open(self) -> bool:
        """
        检查是否允许开新仓

        检查项 (按顺序):
            1. Portfolio冷却期 (drawdown触发后360天内禁止开仓)
            2. VIX恐慌检查 (VIX >= 35 禁止开仓)

        Returns:
            True: 允许开仓
            False: 禁止开仓
        """
        # 检查1: Portfolio冷却期
        if self.is_in_portfolio_cooldown():
            return False

        # 检查2: VIX恐慌
        if self.vix_enabled and not self._check_vix_safe():
            return False

        return True

    def activate_portfolio_cooldown(self):
        """
        激活Portfolio冷却期

        由main.py在执行全仓平仓后调用
        """
        cooldown_end = self.algorithm.Time + timedelta(days=self.drawdown_cooldown_days)
        self.portfolio_cooldown_until = cooldown_end
        self.algorithm.Debug(
            f"[RiskManager] Portfolio冷却期激活: 至 {cooldown_end.strftime('%Y-%m-%d')} "
            f"({self.drawdown_cooldown_days}天)"
        )

    # =========================================================================
    # 私有方法
    # =========================================================================

    def _check_vix_safe(self) -> bool:
        """
        检查VIX是否安全

        逻辑:
            - VIX无数据 → 允许开仓（激进策略）
            - VIX >= vix_threshold (35) → 禁止开仓
            - VIX < vix_threshold → 允许开仓

        Returns:
            True: 安全，允许开仓
            False: 恐慌，禁止开仓
        """
        vix = self._get_vix_value()

        # VIX无数据时，激进策略：允许开仓
        if vix is None:
            return True

        if vix >= self.vix_threshold:
            self.algorithm.Debug(
                f"[RiskManager] VIX恐慌，暂停开仓: VIX={vix:.1f} >= {self.vix_threshold}"
            )
            return False

        return True

    def _get_vix_value(self) -> Optional[float]:
        """
        获取最新VIX值

        Returns:
            VIX值 (如30.5)，数据不足时返回None
        """
        try:
            # 检查algorithm是否有vix_symbol属性
            if not hasattr(self.algorithm, 'vix_symbol'):
                return None

            vix_symbol = self.algorithm.vix_symbol

            # 获取最近1天的VIX数据
            history = self.algorithm.History(vix_symbol, 1, Resolution.Daily)

            if history.empty:
                return None

            return float(history['close'].iloc[-1])

        except Exception:
            return None
