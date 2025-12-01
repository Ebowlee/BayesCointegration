# region imports
from AlgorithmImports import *
import numpy as np
from collections import deque
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from src.OrderExecutor import OpenIntent, CloseIntent
from src.KalmanBetaTracker import KalmanBetaTracker
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
        # v8.12.0: quality_score 已删除 (PairSelector v8.9.0+ 改为 pass/fail 筛选)
        self.half_life = model_data.get('half_life')
        self.half_life_std = model_data.get('half_life_std', 0)

        # 卡尔曼滤波参数 (v8.6.0: 从model_data传递)
        self.beta_std = model_data.get('beta_std', 0.1)
        self.sigma_ols = model_data.get('sigma_ols', 0.05)

        # 卡尔曼滤波追踪器 (开仓时实例化，平仓时销毁)
        self.kf_tracker: Optional[KalmanBetaTracker] = None

        # 交易阈值
        self.entry_threshold_lower = config.entry_threshold_lower
        self.entry_threshold_upper = config.entry_threshold_upper
        self.exit_threshold = config.exit_threshold
        # v8.2.1: stop_loss_threshold 已迁移至 PairsManager.check_pairs_health()

        # 保证金参数
        self.margin_long = config.margin_requirement_long
        self.margin_short = config.margin_requirement_short

        # 时间追踪
        self.creation_time = algorithm.Time
        self.pair_opened_time = None
        self.pair_closed_time = None
        self.last_close_reason = None

        # 交易历史 (v8.1.0: 单一事实来源 - 四元组结构)
        # (entry_time, exit_time, pnl, invested_capital)
        self.trade_history: List[Tuple[datetime, datetime, float, float]] = []

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

        # RSI on Z-score (v8.7.0: 动量检测)
        # maxlen = rsi_period + rsi_lookback_for_extreme + 1 (保留足够计算历史RSI的数据)
        rsi_maxlen = config.rsi_period + config.rsi_lookback_for_extreme + 1
        self.zscore_history: deque = deque(maxlen=rsi_maxlen)
        self.rsi_history: deque = deque(maxlen=config.rsi_lookback_for_extreme + 1)

        # 预热Z-score历史 (从clean_data加载)
        self._warmup_zscore_history()


    def _warmup_zscore_history(self):
        """
        从clean_data预热Z-score历史 (v8.7.0)

        设计: 在Pairs初始化时调用，从algorithm.clean_data加载历史价格计算Z-score
        目的: 确保RSI从第一天就可以计算，避免初始盲区
        """
        if not hasattr(self.algorithm, 'clean_data') or self.algorithm.clean_data is None:
            return

        clean_data = self.algorithm.clean_data
        warmup_days = self.config.rsi_warmup_days

        # 检查两个symbol是否都在clean_data中
        if self.symbol1 not in clean_data or self.symbol2 not in clean_data:
            return

        df1 = clean_data[self.symbol1]
        df2 = clean_data[self.symbol2]

        # 取最近N天数据进行预热
        n_days = min(warmup_days, len(df1), len(df2))
        if n_days < 2:
            return

        # 从历史数据计算Z-score并填充deque
        for i in range(-n_days, 0):
            try:
                price1 = df1.iloc[i]['close']
                price2 = df2.iloc[i]['close']
                zscore = self._calculate_zscore_pure(
                    price1, price2,
                    self.alpha_mean, self.beta_mean,
                    self.residual_mean, self.residual_std
                )
                if zscore is not None:
                    self.zscore_history.append(zscore)
            except (KeyError, IndexError):
                continue


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
        # v8.12.0: quality_score 已删除


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

    @staticmethod
    def _calculate_rsi_from_series(zscore_series: list, period: int) -> Optional[float]:
        """
        纯计算: RSI from Z-score series (v8.7.0)

        公式: RSI = 100 - 100/(1 + RS), RS = avg_gain / avg_loss
        输入: zscore_series (需要 period+1 个点来计算 period 个变化量)
        """
        if len(zscore_series) < period + 1:
            return None

        # 取最近 period+1 个数据点
        recent = list(zscore_series)[-(period + 1):]

        # 计算变化量 (differences)
        changes = [recent[i+1] - recent[i] for i in range(period)]

        # 分离涨跌
        gains = [c for c in changes if c > 0]
        losses = [-c for c in changes if c < 0]

        avg_gain = sum(gains) / period if gains else 0
        avg_loss = sum(losses) / period if losses else 0

        # 避免除以零
        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0

        rs = avg_gain / avg_loss
        return 100.0 - 100.0 / (1.0 + rs)


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
        """检查是否在冷却期中 (v8.13.0: 基于半衰期动态计算)"""
        if self.pair_closed_time is None:
            return False

        elapsed_days = (self.algorithm.UtcTime - self.pair_closed_time).days
        reason = self.last_close_reason or 'MEAN_REVERSION'
        # v8.13.0: 传递half_life给PairsManager
        cooldown_days = self.algorithm.pairs_manager.get_cooldown_required_days(reason, self.half_life)
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

    def get_pair_current_invested_capital(self) -> Optional[float]:
        """获取配对当前投入资本: 0.5 × (|qty1×price1| + |qty2×price2|)"""
        if not self.has_position():
            return None
        return self._calculate_invested_capital_pure(
            self.tracked_qty1, self.tracked_qty2,
            self.entry_price1, self.entry_price2
            )

    def _calculate_trade_pnl(self) -> Optional[float]:
        """
        计算单笔交易的已实现PnL (v8.0.19 私有方法)

        注意: 这是内部辅助方法，用于平仓时计算单笔交易盈亏。
        历史累积PnL请使用 get_total_pnl() 方法。
        """
        if self.exit_price1 is None or self.exit_price2 is None:
            return None
        if self.entry_price1 is None or self.entry_price2 is None:
            return None

        exit_value = self.tracked_qty1 * self.exit_price1 + self.tracked_qty2 * self.exit_price2
        entry_value = self.tracked_qty1 * self.entry_price1 + self.tracked_qty2 * self.entry_price2
        return exit_value - entry_value


    # ===== 4. 业务逻辑层 (Business Logic) =====

    # ----- RSI on Z-score 相关方法 (v8.7.0) -----

    def _calculate_rsi(self) -> Optional[float]:
        """
        计算当前RSI值 (v8.7.0)

        流程:
        1. 从zscore_history计算RSI
        2. 将结果追加到rsi_history
        3. 返回当前RSI值
        """
        rsi = self._calculate_rsi_from_series(
            self.zscore_history,
            self.config.rsi_period
        )
        if rsi is not None:
            self.rsi_history.append(rsi)
        return rsi

    def _check_rsi_entry_condition(self, signal: str) -> bool:
        """
        检查RSI入场条件 (v8.7.0 三重AND条件)

        设计逻辑 (橡皮筋理论):
        - 条件A: Z-score在入场区间 [2.0, 2.5] (已由调用方保证)
        - 条件B: 近期RSI曾达到超买/超卖 (历史曾拉伸到极端)
        - 条件C: 当前RSI已回落/反弹 (外力已撤除)

        SHORT_SPREAD (Z > 0): 等待RSI从>80回落到<80
        LONG_SPREAD (Z < 0): 等待RSI从<20反弹到>20

        Returns:
            True: 满足RSI条件，可以入场
            False: 不满足RSI条件，等待
            True: 数据不足时fallback到原始逻辑
        """
        # 数据不足时fallback: 返回True允许入场 (不阻止交易)
        if len(self.rsi_history) < 2:
            return True

        current_rsi = self.rsi_history[-1] if self.rsi_history else None
        if current_rsi is None:
            return True

        overbought = self.config.rsi_overbought
        oversold = self.config.rsi_oversold
        lookback = self.config.rsi_lookback_for_extreme

        # 获取近期RSI历史 (不含当前)
        recent_rsi = list(self.rsi_history)[-(lookback + 1):-1] if len(self.rsi_history) > 1 else []
        if not recent_rsi:
            return True

        if signal == 'SHORT_SPREAD':
            # 条件B: 近期RSI曾>80 (超买)
            was_overbought = any(r > overbought for r in recent_rsi)
            # 条件C: 当前RSI<80 (已回落)
            is_cooled = current_rsi < overbought

            return was_overbought and is_cooled

        elif signal == 'LONG_SPREAD':
            # 条件B: 近期RSI曾<20 (超卖)
            was_oversold = any(r < oversold for r in recent_rsi)
            # 条件C: 当前RSI>20 (已反弹)
            is_recovered = current_rsi > oversold

            return was_oversold and is_recovered

        return True  # 未知信号类型，fallback

    # ----- 卡尔曼滤波相关方法 (v8.6.0) -----

    def _init_kalman_tracker(self):
        """
        开仓时初始化卡尔曼滤波追踪器

        参数来源:
            - beta_init: MCMC后验均值 (beta_mean)
            - beta_std: MCMC后验标准差
            - sigma_ols: OLS残差标准差 (观测噪声)
            - process_noise: 独立配置 (kalman_process_noise)
            - alpha: 协整截距 (alpha_mean)
        """
        self.kf_tracker = KalmanBetaTracker(
            beta_init=self.beta_mean,
            beta_std=self.beta_std,
            sigma_ols=self.sigma_ols,
            process_noise=self.config.kalman_process_noise,
            alpha=self.alpha_mean
        )

    def update_kalman_beta(self, price1: float, price2: float) -> Optional[float]:
        """卡尔曼滤波更新 β (每日健康检查时调用)"""
        if self.kf_tracker is None:
            return None
        return self.kf_tracker.update(price1, price2)

    def get_beta_drift(self) -> Optional[float]:
        """
        获取 β 漂移率 (v8.6.0: 替代旧的 VALUE 漂移检查)

        公式: |β_t - β_initial| / |β_initial|
        """
        if self.kf_tracker is None:
            return None
        return self.kf_tracker.get_drift_ratio()

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

    # ----- 动态聚合方法 (v8.1.0: 单一事实来源) -----

    def get_trade_count(self) -> int:
        """交易次数 (从trade_history动态计算)"""
        return len(self.trade_history)

    def get_win_count(self) -> int:
        """盈利次数 (从trade_history动态计算, pnl > 0)"""
        return sum(1 for r in self.trade_history if r[2] > 0)

    def get_total_pnl(self) -> float:
        """累积PnL (从trade_history动态计算)"""
        return sum(r[2] for r in self.trade_history)

    def get_total_invested_capital(self) -> float:
        """累积投入资本 (从trade_history动态计算)"""
        return sum(r[3] for r in self.trade_history)

    def get_avg_holding_days(self) -> float:
        """
        平均持仓天数 (从trade_history动态计算)

        计算方式: mean((exit_time - entry_time).days)
        """
        if not self.trade_history:
            return 0.0
        total_days = sum((r[1] - r[0]).days for r in self.trade_history)
        return total_days / len(self.trade_history)

    def get_win_rate(self) -> Optional[float]:
        """胜率 (从trade_history动态计算)"""
        trade_count = self.get_trade_count()
        if trade_count == 0:
            return None
        return self.get_win_count() / trade_count

    def get_pair_roi(self) -> Optional[float]:
        """
        获取配对级历史累积ROI (v8.1.0: 改为动态计算)

        公式: total_pnl / total_invested_capital

        含义:
            - 只计算已平仓交易的累积收益
            - 不包含当前持仓的未实现盈亏

        Returns:
            累积ROI (小数形式), 无数据时返回 None
        """
        total_capital = self.get_total_invested_capital()
        if total_capital <= 0:
            return None
        return self.get_total_pnl() / total_capital

    def get_avg_return_per_trade(self) -> Optional[float]:
        """
        平均每笔交易回报率 (v8.1.0: 改为动态计算)

        公式: cumulative_roi / trade_count
        """
        trade_count = self.get_trade_count()
        if trade_count == 0:
            return None
        cumulative_roi = self.get_pair_roi()
        if cumulative_roi is None:
            return None
        return cumulative_roi / trade_count

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
        """
        获取交易信号: 无持仓返回入场信号, 有持仓返回出场信号

        v8.2.0: PAIR_BREAK(方向感知止损)已迁移至PairsManager.check_pairs_health()
        v8.7.0: 增加RSI on Z-score动量检测 (三重AND条件)
        本方法只处理入场信号和正常出场(均值回归)
        """
        prices = self.get_price_from_bar(data)
        if prices is None:
            return 'NO_DATA'

        zscore = self.get_zscore(prices[0], prices[1])
        if zscore is None:
            return 'NO_DATA'

        # v8.7.0: 每次获取信号时更新Z-score历史和RSI
        self.zscore_history.append(zscore)
        self._calculate_rsi()

        position_mode = self.position_mode

        # 无持仓: 入场信号
        if position_mode == PositionMode.NONE:
            abs_zscore = abs(zscore)
            if self.entry_threshold_lower <= abs_zscore <= self.entry_threshold_upper:
                # 确定候选信号
                candidate_signal = 'SHORT_SPREAD' if zscore > 0 else 'LONG_SPREAD'

                # v8.7.0: RSI条件检查 (三重AND的条件B和C)
                if not self._check_rsi_entry_condition(candidate_signal):
                    return 'WAIT'  # RSI未满足，继续等待

                # RSI条件满足，记录入场zscore并发出信号
                self.entry_zscore = zscore
                return candidate_signal
            return 'WAIT'

        # 有持仓: 正常出场信号 (均值回归)
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

        OPEN: 记录开仓价格、数量、fill_zscore_open、初始化卡尔曼追踪器
        CLOSE: 记录平仓价格、更新统计、输出日志、重置状态、销毁卡尔曼追踪器
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

            # v8.6.0: 初始化卡尔曼滤波追踪器
            self._init_kalman_tracker()

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
            self.kf_tracker = None                                             # v8.6.0: 销毁卡尔曼追踪器


    def _update_trade_stats(self):
        """
        更新交易统计 (v8.1.0: 简化为追加四元组)

        四元组结构: (entry_time, exit_time, pnl, invested_capital)
        设计原则: 全周期保留数据，不清理
        """
        # === 步骤1：计算已实现PnL ===
        pnl = self._calculate_trade_pnl()
        if pnl is None:
            self.algorithm.Debug(f"[统计错误] {self.pair_id} 无法计算已实现PnL (缺少价格数据)", 1)
            return

        # === 步骤2：计算投入资本 ===
        invested_capital = self.get_pair_current_invested_capital()
        if invested_capital is None or invested_capital <= 0:
            self.algorithm.Debug(f"[统计错误] {self.pair_id} 投入资本异常: {invested_capital}", 1)
            return

        # === 步骤3：追加四元组记录 (v8.1.0: 单一事实来源) ===
        self.trade_history.append((
            self.pair_opened_time,   # [0] entry_time (开仓时间)
            self.pair_closed_time,   # [1] exit_time (平仓时间)
            pnl,                     # [2] 单笔 PnL ($)
            invested_capital         # [3] 单笔投入资本 ($)
        ))
        # v8.1.0: 删除清理逻辑 - 全周期保留数据


    def _log_close_completion(self, reason: str):
        """输出平仓日志: 配对ID、原因、PnL、zscore轨迹、冷却期"""
        # 计算本次交易PnL (v8.0.5: 使用 _calculate_trade_pnl 代替 unrealized)
        current_pnl = self._calculate_trade_pnl()
        current_invested = self.get_pair_current_invested_capital()
        current_pnl_pct = (current_pnl / current_invested * 100) if (current_pnl and current_invested and current_invested > 0) else 0

        # v8.1.0: 使用动态聚合方法计算累计收益率
        total_capital = self.get_total_invested_capital()
        total_pnl_pct = (self.get_total_pnl() / total_capital * 100) if total_capital > 0 else 0

        # v8.1.0: 使用动态方法获取交易序号
        trade_num = self.get_trade_count()

        # 提取Z-score数据
        entry_z = self.entry_zscore if self.entry_zscore is not None else 0.0
        close_z = self.fill_zscore_close if self.fill_zscore_close is not None else 0.0

        # v7.99.7: 直接使用reason字符串
        reason_text = reason or '未知原因'

        # v8.13.0: 基于半衰期动态计算冷却期
        cooldown_days = self.algorithm.pairs_manager.get_cooldown_required_days(reason, self.half_life)

        # 计算持有天数
        holding_days = self.get_pair_holding_days()

        # v7.38.2: 计算理论最大持仓天数
        max_days = self.get_max_holding_days()
        max_days_str = f"{max_days:.0f}" if max_days is not None else "N/A"

        # v7.37.1: 获取行业名称用于日志输出
        industry_names = self.algorithm.config.constants['industry_names']
        industry_name = industry_names.get(int(self.industry_code), '未知') if self.industry_code else '未知'

        # v8.0.7: 计算平仓时对冲漂移
        drift = self.get_beta_drift()
        drift_pct = (drift * 100) if drift is not None else 0.0

        # v8.0.4: 优化日志格式 - 调整字段顺序，新增投资额
        # v8.0.7: 新增漂移显示 (在zscore和冷却期之间)
        self.algorithm.Debug(
            f"[平仓] {self.pair_id} | {industry_name} | {reason_text} | "
            f"第{trade_num}次交易 | 持有{holding_days}/{max_days_str}天 | "
            f"投资${current_invested:,.0f} | PnL=${current_pnl:.2f} ({current_pnl_pct:+.1f}%) | "
            f"累计{total_pnl_pct:+.1f}% | "
            f"{entry_z:+.2f}σ → {close_z:+.2f}σ | 漂移{drift_pct:+.1f}% | "
            f"冷却{cooldown_days}天",
            level=0
        )

