# region imports
from AlgorithmImports import *
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from src.OrderExecutor import OpenIntent, CloseIntent
import math
# endregion


class PositionMode:
    """持仓模式常量 - 避免魔法字符串"""
    NONE = 'NONE'                    # 无持仓
    LONG_SPREAD = 'LONG_SPREAD'      # 正常多头价差
    SHORT_SPREAD = 'SHORT_SPREAD'    # 正常空头价差
    PARTIAL_LEG1 = 'PARTIAL_LEG1'    # 异常: 仅LEG1持仓
    PARTIAL_LEG2 = 'PARTIAL_LEG2'    # 异常: 仅LEG2持仓
    ANOMALY_SAME = 'ANOMALY_SAME'    # 异常: 同向持仓


class Pairs:
    """
    配对交易核心对象 - 数据提供 + 信号生成 + 意图生成 + 交易历史追踪

    不负责: 风险检查、资金分配、订单执行 (由 RiskManager/ExecutionManager/OrderExecutor 处理)
    """

    # ===== 1. 初始化与参数管理 =====

    @classmethod
    def from_model_result(cls, algorithm, model_result: Dict, config) -> 'Pairs':
        """工厂方法: 从贝叶斯建模结果创建 Pairs 对象"""
        return cls(algorithm, model_result, config)


    def __init__(self, algorithm, model_data, config):
        """从贝叶斯建模结果初始化配对对象"""
        self.algorithm = algorithm
        self.config = config

        # 基础信息
        self.symbol1 = model_data['symbol1']
        self.symbol2 = model_data['symbol2']
        self.pair_id = (self.symbol1.Value, self.symbol2.Value)
        self.industry_code = int(model_data['industry_code'])

        # 统计参数 (贝叶斯建模结果)
        self.alpha_mean = model_data['alpha_mean']
        self.beta_mean = model_data['beta_mean']
        self.residual_mean = model_data['residual_mean']
        self.residual_std = model_data['residual_std']
        self.quality_score = model_data['quality_score']
        self.half_life = model_data.get('half_life')
        self.half_life_std = model_data.get('half_life_std', 0)

        # 交易阈值
        self.entry_threshold_lower = config.entry_threshold_lower
        self.entry_threshold_upper = config.entry_threshold_upper
        self.exit_threshold = config.exit_threshold
        self.stop_loss_threshold = config.stop_loss_threshold

        # 保证金参数
        self.margin_long = config.margin_requirement_long
        self.margin_short = config.margin_requirement_short

        # 时间追踪
        self.creation_time = algorithm.Time
        self.pair_opened_time = None
        self.pair_closed_time = None
        self.last_close_reason = None

        # 交易历史统计 (加权平均)
        self.trade_count = 0
        self.win_count = 0
        self.pair_accum_realized_pnl = 0.0
        self.pair_past_invested_capital = 0.0
        self.pair_past_total_holding_days = 0.0
        self.trade_history: List[Tuple[datetime, float, float]] = []

        # Z-score追踪 (信号/开仓/平仓三阶段)
        self.entry_zscore = None
        self.fill_zscore_open = None
        self.fill_zscore_close = None

        # 持仓追踪 (OrderTicket-based)
        self.tracked_qty1 = 0
        self.tracked_qty2 = 0

        # 成本追踪
        self.entry_price1 = None
        self.entry_price2 = None
        self.exit_price1 = None
        self.exit_price2 = None

        # 回撤追踪
        self.pair_hwm: float = None


    def update_params(self, new_pair):
        """
        从新的Pairs对象更新统计参数

        调用: PairsManager.update_pairs() 每月选股后
        注意: 持仓检查由调用方处理, 本方法仅负责参数更新
        """
        self.alpha_mean = new_pair.alpha_mean
        self.beta_mean = new_pair.beta_mean
        self.residual_mean = new_pair.residual_mean
        self.residual_std = new_pair.residual_std
        self.quality_score = new_pair.quality_score


    # ===== 2. 纯计算层 (Pure Computation) =====

    @staticmethod
    def _calculate_zscore_pure(price1: float, price2: float,
                                alpha: float, beta: float,
                                residual_mean: float, residual_std: float) -> Optional[float]:
        """
        纯计算: Z-score

        公式: zscore = (ln(p1) - α - β×ln(p2) - μ) / σ
        """
        if price1 <= 0 or price2 <= 0 or residual_std <= 0:
            return None
        try:
            log_residual = np.log(price1) - (alpha + beta * np.log(price2))
            return (log_residual - residual_mean) / residual_std
        except (ValueError, ZeroDivisionError, OverflowError):
            return None

    @staticmethod
    def _calculate_leg_values_pure(
        allocated_amount: float,
        signal: str,
        beta: float,
        margin_long: float,
        margin_short: float
    ) -> Tuple[Optional[float], Optional[float]]:
        """
        纯计算: Beta对冲两腿市值

        Returns: (value_1, value_2) 或 (None, None)
        """
        if allocated_amount <= 0 or beta <= 0:
            return None, None
        if margin_long <= 0 or margin_short <= 1:
            return None, None

        k_S = margin_short - 1.0

        if signal == 'LONG_SPREAD':
            gamma = beta * k_S / margin_long
            x1 = allocated_amount / (1 + gamma)
            x2 = allocated_amount * gamma / (1 + gamma)
            value_1 = x1 / margin_long
            value_2 = x2 / k_S
        elif signal == 'SHORT_SPREAD':
            gamma = beta * margin_long / k_S
            x1 = allocated_amount / (1 + gamma)
            x2 = allocated_amount * gamma / (1 + gamma)
            value_1 = x1 / k_S
            value_2 = x2 / margin_long
        else:
            return None, None

        if x1 <= 0 or x2 <= 0:
            return None, None

        return value_1, value_2

    @staticmethod
    def _calculate_invested_capital_pure(
        qty1: float, qty2: float,
        entry_price1: float, entry_price2: float
    ) -> Optional[float]:
        """
        纯计算: 配对投入资本

        公式: invested_capital = 0.5 × (|qty1×price1| + |qty2×price2|)
        """
        if entry_price1 is None or entry_price2 is None:
            return None
        if entry_price1 <= 0 or entry_price2 <= 0:
            return None

        return 0.5 * (abs(qty1 * entry_price1) + abs(qty2 * entry_price2))


    # ===== 3. 数据访问层 (Data Access) =====

    def get_price_from_bar(self, data):
        """从TradeBar获取Close价格, 返回 (price1, price2) 或 None"""
        if (self.symbol1 in data and self.symbol2 in data and
            data[self.symbol1] is not None and data[self.symbol2] is not None):
            price1 = data[self.symbol1].Close
            price2 = data[self.symbol2].Close
            if price1 > 0 and price2 > 0:
                return (price1, price2)
        return None

    @property
    def position_mode(self):
        """获取当前持仓模式, 返回 PositionMode 常量"""
        qty1, qty2 = self.tracked_qty1, self.tracked_qty2

        if qty1 == 0 and qty2 == 0:
            return PositionMode.NONE
        elif qty1 > 0 and qty2 < 0:
            return PositionMode.LONG_SPREAD
        elif qty1 < 0 and qty2 > 0:
            return PositionMode.SHORT_SPREAD
        elif qty1 != 0 and qty2 == 0:
            self.algorithm.Debug(f"[持仓异常] {self.pair_id} 单边持仓LEG1: qty1={qty1:+.0f}")
            return PositionMode.PARTIAL_LEG1
        elif qty1 == 0 and qty2 != 0:
            self.algorithm.Debug(f"[持仓异常] {self.pair_id} 单边持仓LEG2: qty2={qty2:+.0f}")
            return PositionMode.PARTIAL_LEG2
        else:
            self.algorithm.Debug(f"[持仓异常] {self.pair_id} 同向持仓: qty1={qty1:+.0f}, qty2={qty2:+.0f}")
            return PositionMode.ANOMALY_SAME

    def has_position(self) -> bool:
        """检查是否有持仓"""
        return self.position_mode != PositionMode.NONE

    def has_normal_position(self) -> bool:
        """检查是否有正常持仓 (LONG_SPREAD/SHORT_SPREAD)"""
        return self.position_mode in [PositionMode.LONG_SPREAD, PositionMode.SHORT_SPREAD]

    def has_anomaly_position(self) -> bool:
        """检查是否有异常持仓 (单边或同向)"""
        return self.position_mode in [PositionMode.PARTIAL_LEG1, PositionMode.PARTIAL_LEG2, PositionMode.ANOMALY_SAME]

    def is_in_cooldown(self) -> bool:
        """检查是否在冷却期中"""
        if self.pair_closed_time is None:
            return False

        elapsed_days = (self.algorithm.UtcTime - self.pair_closed_time).days
        reason = self.last_close_reason or 'MEAN_REVERSION'
        cooldown_days = self.algorithm.pairs_manager.get_cooldown_required_days(reason)
        return elapsed_days < cooldown_days

    def get_pair_unrealized_pnl(self) -> Optional[float]:
        """获取配对浮动盈亏 (使用实时价格)"""
        if not self.has_position():
            return None
        if self.entry_price1 is None or self.entry_price2 is None:
            return None

        portfolio = self.algorithm.Portfolio
        price1 = portfolio[self.symbol1].Price
        price2 = portfolio[self.symbol2].Price

        current_value = self.tracked_qty1 * price1 + self.tracked_qty2 * price2
        entry_value = self.tracked_qty1 * self.entry_price1 + self.tracked_qty2 * self.entry_price2

        return current_value - entry_value

    def get_pair_realized_pnl(self) -> Optional[float]:
        """获取配对已实现盈亏 (使用平仓价格)"""
        if self.exit_price1 is None or self.exit_price2 is None:
            return None
        if self.entry_price1 is None or self.entry_price2 is None:
            return None

        exit_value = self.tracked_qty1 * self.exit_price1 + self.tracked_qty2 * self.exit_price2
        entry_value = self.tracked_qty1 * self.entry_price1 + self.tracked_qty2 * self.entry_price2
        return exit_value - entry_value

    def get_pair_current_invested_capital(self) -> Optional[float]:
        """获取配对当前投入资本: 0.5 × (|qty1×price1| + |qty2×price2|)"""
        if not self.has_position():
            return None
        return self._calculate_invested_capital_pure(
            self.tracked_qty1, self.tracked_qty2,
            self.entry_price1, self.entry_price2
        )

    # ===== 4. 业务逻辑层 (Business Logic) =====

    def get_hedge_drift(self) -> Optional[float]:
        """
        计算对冲漂移率: Drift = (val1 + val2) / (|val1| + |val2|)

        数值解读: 0=完美对冲, ±0.25=触发阈值, 正=净多头, 负=净空头
        """
        if not self.has_position():
            return None

        portfolio = self.algorithm.Portfolio
        val1 = self.tracked_qty1 * portfolio[self.symbol1].Price
        val2 = self.tracked_qty2 * portfolio[self.symbol2].Price

        gross_exp = abs(val1) + abs(val2)
        if gross_exp == 0:
            return None
        return (val1 + val2) / gross_exp

    def get_leg_values(self, allocated_amount: float, signal: str, data):
        """获取Beta对冲两腿市值, 返回 (value_1, value_2) 或 (None, None)"""
        prices = self.get_price_from_bar(data)
        if prices is None:
            return None, None
        price_1, price_2 = prices

        if price_1 <= 0 or price_2 <= 0:
            self.algorithm.Debug(f"[计算失败] {self.pair_id} 价格异常: Symbol1={price_1}, Symbol2={price_2}")
            return None, None

        beta = abs(self.beta_mean) if abs(self.beta_mean) != 0 else 1
        result = self._calculate_leg_values_pure(
            allocated_amount, signal, beta,
            self.margin_long, self.margin_short
        )

        if result[0] is None:
            self.algorithm.Debug(f"[计算失败] {self.pair_id} 信号={signal}, 资金={allocated_amount:.2f}")
        return result

    def get_pair_holding_days(self) -> Optional[int]:
        """获取持仓天数"""
        if not self.has_normal_position():
            return None
        if self.pair_opened_time is not None:
            return (self.algorithm.UtcTime - self.pair_opened_time).days
        return None

    def get_pair_drawdown(self) -> Optional[float]:
        """
        计算配对回撤率: (HWM - current_value) / HWM

        HWM 在内部维护, 首次调用初始化, 后续取 max
        """
        if not self.has_position():
            return None

        invested_capital = self.get_pair_current_invested_capital()
        unrealized_pnl = self.get_pair_unrealized_pnl()
        if invested_capital is None or unrealized_pnl is None:
            return None

        current_value = invested_capital + unrealized_pnl

        if self.pair_hwm is None:
            self.pair_hwm = current_value
        else:
            self.pair_hwm = max(self.pair_hwm, current_value)

        if self.pair_hwm <= 0:
            return 0.0
        return max(0, (self.pair_hwm - current_value) / self.pair_hwm)

    def get_pair_cumulative_roi(self) -> Optional[float]:
        """获取累积ROI = pair_accum_realized_pnl / pair_past_invested_capital"""
        if self.pair_past_invested_capital <= 0:
            return None
        return self.pair_accum_realized_pnl / self.pair_past_invested_capital

    def get_avg_return_per_trade(self) -> Optional[float]:
        """计算平均每笔交易回报率 = cumulative_roi / trade_count"""
        if self.trade_count == 0:
            return None
        cumulative_roi = self.get_pair_cumulative_roi()
        if cumulative_roi is None:
            return None
        return cumulative_roi / self.trade_count

    def get_max_holding_days(self) -> Optional[float]:
        """
        计算理论最大持仓天数

        公式: max_days = ln(exit_threshold/entry_zscore) / ln(0.5) × half_life
        """
        if self.entry_zscore is None or self.half_life is None:
            return None

        exit_threshold = self.algorithm.config.pairs.exit_threshold
        entry_zscore = abs(self.entry_zscore)
        n = math.log(exit_threshold / entry_zscore) / math.log(0.5)
        return n * self.half_life

    # ===== 5. 外部接口层 (Public API) =====

    def get_zscore(self, price1: float, price2: float) -> Optional[float]:
        """获取Z-score, 委托给纯计算层"""
        return self._calculate_zscore_pure(
            price1, price2,
            self.alpha_mean, self.beta_mean,
            self.residual_mean, self.residual_std
        )


    def get_signal(self, data):
        """获取交易信号: 无持仓返回入场信号, 有持仓返回出场信号"""
        prices = self.get_price_from_bar(data)
        if prices is None:
            return 'NO_DATA'

        zscore = self.get_zscore(prices[0], prices[1])
        if zscore is None:
            return 'NO_DATA'

        position_mode = self.position_mode

        # 无持仓: 入场信号
        if position_mode == PositionMode.NONE:
            abs_zscore = abs(zscore)
            if self.entry_threshold_lower <= abs_zscore <= self.entry_threshold_upper:
                self.entry_zscore = zscore
                return 'SHORT_SPREAD' if zscore > 0 else 'LONG_SPREAD'
            return 'WAIT'

        # 有持仓: 出场信号 (方向感知止损)
        if position_mode == PositionMode.LONG_SPREAD and zscore < -self.stop_loss_threshold:
            return 'BREAK_LOWER'
        elif position_mode == PositionMode.SHORT_SPREAD and zscore > self.stop_loss_threshold:
            return 'BREAK_UPPER'

        if abs(zscore) < self.exit_threshold:
            return 'CLOSE'

        return 'HOLD'


    def get_open_intent(self, amount_allocated: float, data):
        """生成开仓意图, 返回 OpenIntent 或 None"""
        signal = self.get_signal(data)
        if signal not in ['LONG_SPREAD', 'SHORT_SPREAD']:
            return None

        value1, value2 = self.get_leg_values(amount_allocated, signal, data)
        if value1 is None or value2 is None:
            return None

        prices = self.get_price_from_bar(data)
        if prices is None:
            return None
        price1, price2 = prices

        if signal == 'LONG_SPREAD':
            qty1 = int(value1 / price1)
            qty2 = -int(value2 / price2)
        else:
            qty1 = -int(value1 / price1)
            qty2 = int(value2 / price2)

        if qty1 == 0 or qty2 == 0:
            return None

        return OpenIntent(
            pair_id=self.pair_id,
            symbol1=self.symbol1,
            symbol2=self.symbol2,
            qty1=qty1,
            qty2=qty2,
            signal=signal,
            tag=self.create_order_tag('OPEN')
        )


    def get_close_intent(self, reason='CLOSE', data=None):
        """生成平仓意图, 返回 CloseIntent 或 None(无持仓)"""
        if self.tracked_qty1 == 0 and self.tracked_qty2 == 0:
            return None

        # v8.0.12: 计算当前zscore用于订单tag
        current_zscore = None
        if data is not None:
            prices = self.get_price_from_bar(data)
            if prices:
                current_zscore = self.get_zscore(prices[0], prices[1])

        return CloseIntent(
            pair_id=self.pair_id,
            symbol1=self.symbol1,
            symbol2=self.symbol2,
            qty1=self.tracked_qty1,
            qty2=self.tracked_qty2,
            reason=reason,
            tag=self.create_order_tag('CLOSE', reason, current_zscore)
        )


    def create_order_tag(self, action: str, reason: str = None,
                         current_zscore: float = None):
        """
        创建订单Tag

        格式:
            OPEN:  "pair_id_OPEN"
            CLOSE: "pair_id_CLOSE_{reason}_{entry_z-close_z}"
        """
        if action == 'CLOSE' and reason:
            # v8.0.12: 添加zscore信息 {entry-close}
            entry_z = self.entry_zscore if self.entry_zscore is not None else 0.0
            close_z = current_zscore if current_zscore is not None else 0.0
            return f"{self.pair_id}_{action}_{reason}_{{{entry_z:+.2f}-{close_z:+.2f}}}"
        else:
            return f"{self.pair_id}_{action}"


    # ===== 6. 生命周期回调 (Lifecycle Callbacks) =====
    # 特征: 由外部系统调用的回调方法, 处理状态更新

    def on_position_filled(self, action: str, fill_time, tickets, reason: str = None):
        """
        订单成交回调 (由TicketsManager触发)

        OPEN: 记录开仓价格、数量、fill_zscore_open
        CLOSE: 记录平仓价格、更新统计、输出日志、重置状态
        """
        if action == 'OPEN':
            self.pair_opened_time = fill_time

            # 提取成交价格(用于计算fill_zscore)
            fill_price1 = None
            fill_price2 = None

            # 从OrderTicket提取实际成交数量和均价
            for ticket in tickets:
                if ticket is not None and ticket.Status == OrderStatus.Filled:
                    if ticket.Symbol == self.symbol1:
                        self.tracked_qty1 = ticket.QuantityFilled
                        self.entry_price1 = ticket.AverageFillPrice
                        fill_price1 = ticket.AverageFillPrice  # 用于计算fill_zscore
                    elif ticket.Symbol == self.symbol2:
                        self.tracked_qty2 = ticket.QuantityFilled
                        self.entry_price2 = ticket.AverageFillPrice
                        fill_price2 = ticket.AverageFillPrice  # 用于计算fill_zscore

            # 计算开仓成交时的Z-score(用于滑点分析)
            if fill_price1 and fill_price2:
                self.fill_zscore_open = self.get_zscore(fill_price1, fill_price2)

        elif action == 'CLOSE':
            self.pair_closed_time = fill_time
            self.last_close_reason = reason  # 存储平仓原因(用于动态冷却期判断)

            # 提取成交价格(用于计算fill_zscore)
            fill_price1 = None
            fill_price2 = None

            # 记录平仓价格(用于后续PnL计算)
            for ticket in tickets:
                if ticket is not None and ticket.Status == OrderStatus.Filled:
                    if ticket.Symbol == self.symbol1:
                        self.exit_price1 = ticket.AverageFillPrice
                        fill_price1 = ticket.AverageFillPrice  # 用于计算fill_zscore
                    elif ticket.Symbol == self.symbol2:
                        self.exit_price2 = ticket.AverageFillPrice
                        fill_price2 = ticket.AverageFillPrice  # 用于计算fill_zscore

            # 计算平仓成交时的Z-score(用于滑点分析)
            if fill_price1 and fill_price2:
                self.fill_zscore_close = self.get_zscore(fill_price1, fill_price2)

            # 更新交易历史统计(先更新状态)
            self._update_trade_stats()

            # 输出平仓日志(读取已更新的realized_pnl/cost)
            self._log_close_completion(reason)

            # 清零所有追踪变量
            self.tracked_qty1 = 0
            self.tracked_qty2 = 0
            self.entry_price1 = None
            self.entry_price2 = None
            self.exit_price1 = None
            self.exit_price2 = None
            self.pair_hwm = None                                               # 重置高水位 (v7.86.0)


    def _update_trade_stats(self):
        """更新交易统计: 累加PnL/投入资本/持仓天数, 记录到trade_history"""
        # === 步骤1：使用 get_pair_realized_pnl() 计算已实现PnL ===
        pnl = self.get_pair_realized_pnl()
        if pnl is None:
            self.algorithm.Debug(f"[统计错误] {self.pair_id} 无法计算已实现PnL (缺少价格数据)", 1)
            return

        # === 步骤2：计算投入资本 ===
        invested_capital = self.get_pair_current_invested_capital()

        if invested_capital is None or invested_capital <= 0:
            self.algorithm.Debug(f"[统计错误] {self.pair_id} 投入资本异常: {invested_capital}", 1)
            return

        # === 步骤3：累加到历史统计 ===
        self.pair_accum_realized_pnl += pnl   # 分子：已实现PnL（使用平仓价格）
        self.pair_past_invested_capital += invested_capital  # 分母：已平仓累计投入资本

        # === 步骤5：存储单笔交易记录 (v8.0.0 滚动窗口) ===
        self.trade_history.append((
            self.pair_closed_time,  # [0] exit_time (datetime)
            pnl,                    # [1] 单笔 PnL ($)
            invested_capital        # [2] 单笔投入资本 ($)
        ))

        # === 步骤6：更新计数统计 ===
        self.trade_count += 1
        if pnl > 0:
            self.win_count += 1

        # === 步骤7：累加持仓天数 (v7.57.0) ===
        if self.pair_opened_time and self.pair_closed_time:
            holding_days = (self.pair_closed_time - self.pair_opened_time).days
            self.pair_past_total_holding_days += holding_days

        # === 步骤8：清理过期历史记录 (v8.0.3 防止内存泄漏) ===
        cutoff_time = self.algorithm.Time - timedelta(days=self.config.max_history_days)
        self.trade_history = [t for t in self.trade_history if t[0] >= cutoff_time]


    def _log_close_completion(self, reason: str):
        """输出平仓日志: 配对ID、原因、PnL、zscore轨迹、冷却期"""
        # 计算本次交易PnL (v8.0.5: 使用 get_pair_realized_pnl 代替 unrealized)
        current_pnl = self.get_pair_realized_pnl()
        current_invested = self.get_pair_current_invested_capital()
        current_pnl_pct = (current_pnl / current_invested * 100) if (current_pnl and current_invested and current_invested > 0) else 0

        # 计算累计收益率 (v8.0.5: 使用重命名后的 pair_accum_realized_pnl)
        total_pnl_pct = (self.pair_accum_realized_pnl / self.pair_past_invested_capital * 100) if self.pair_past_invested_capital > 0 else 0

        # 交易序号(此时 trade_count 已在 _update_trade_stats 中递增)
        trade_num = self.trade_count

        # 提取Z-score数据
        entry_z = self.entry_zscore if self.entry_zscore is not None else 0.0
        close_z = self.fill_zscore_close if self.fill_zscore_close is not None else 0.0

        # v7.99.7: 直接使用reason字符串
        reason_text = reason or '未知原因'

        # v7.44.0: 调用PairsManager统一配置查询
        cooldown_days = self.algorithm.pairs_manager.get_cooldown_required_days(reason)

        # 计算持有天数
        holding_days = self.get_pair_holding_days()

        # v7.38.2: 计算理论最大持仓天数
        max_days = self.get_max_holding_days()
        max_days_str = f"{max_days:.0f}" if max_days is not None else "N/A"

        # v7.37.1: 获取行业名称用于日志输出
        industry_names = self.algorithm.config.constants['industry_names']
        industry_name = industry_names.get(int(self.industry_code), '未知') if self.industry_code else '未知'

        # v8.0.4: 优化日志格式 - 调整字段顺序，新增投资额
        self.algorithm.Debug(
            f"[平仓] {self.pair_id} | {industry_name} | {reason_text} | "
            f"第{trade_num}次交易 | 持有{holding_days}/{max_days_str}天 | "
            f"投资${current_invested:,.0f} | PnL=${current_pnl:.2f} ({current_pnl_pct:+.1f}%) | "
            f"累计{total_pnl_pct:+.1f}% | "
            f"{entry_z:+.2f}σ → {close_z:+.2f}σ | "
            f"冷却{cooldown_days}天",
            level=0
        )

        # v7.28.2: 增强诊断 - 价格变化明细
        if self.exit_price1 and self.exit_price2 and self.entry_price1 and self.entry_price2:
            leg1_pnl = self.tracked_qty1 * (self.exit_price1 - self.entry_price1)
            leg2_pnl = self.tracked_qty2 * (self.exit_price2 - self.entry_price2)

            self.algorithm.Debug(
                f"[PnL明细] {self.pair_id} "
                f"| 开仓价=({self.entry_price1:.4f}, {self.entry_price2:.4f}) "
                f"| 平仓价=({self.exit_price1:.4f}, {self.exit_price2:.4f}) "
                f"| 数量=({self.tracked_qty1:+.0f}, {self.tracked_qty2:+.0f}) "
                f"| leg1_pnl=${leg1_pnl:+.2f} "
                f"| leg2_pnl=${leg2_pnl:+.2f}",
                level=1
            )

            # v7.39.0: 对冲漂移诊断 - 分析亏损原因(Alpha风险 vs Beta风险)
            # v7.40.1: 简化后此功能不再输出(平仓后exposure=None)
            hedge_drift = self.get_hedge_drift()
            if hedge_drift is not None:
                self.algorithm.Debug(
                    f"[对冲诊断] {self.pair_id} | 平仓时漂移={hedge_drift:+.2f}%",
                    level=1
                )
