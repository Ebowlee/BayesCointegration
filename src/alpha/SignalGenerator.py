# region imports
from AlgorithmImports import *
import numpy as np
from typing import Dict, List
from datetime import timedelta
from .AlphaState import AlphaModelState
# endregion


class SignalGenerator:
    """信号生成器 - 将统计模型转化为可执行的交易信号"""

    def __init__(self, algorithm, config: dict, state: AlphaModelState):
        self.algorithm = algorithm
        self.entry_threshold = config['entry_threshold']
        self.exit_threshold = config['exit_threshold']
        self.upper_limit = config['upper_limit']
        self.lower_limit = config['lower_limit']
        self.flat_signal_duration_days = config['flat_signal_duration_days']
        self.entry_signal_duration_days = config['entry_signal_duration_days']
        self.state = state

        # EMA平滑系数
        self.ema_alpha = 0.8

        # 市场风控参数
        self.market_severe_threshold = config['market_severe_threshold']
        self.market_cooldown_days = config['market_cooldown_days']
        self.market_cooldown_until = None
        self.spy_symbol = None

    def _make_pair_key(self, symbol1_value: str, symbol2_value: str) -> tuple:
        """生成规范化的pair_key"""
        return tuple(sorted([symbol1_value, symbol2_value]))

    def _has_pair_position(self, symbol1: Symbol, symbol2: Symbol) -> bool:
        """直接查询Portfolio判断配对是否有持仓"""
        try:
            holding1 = self.algorithm.Portfolio[symbol1]
            holding2 = self.algorithm.Portfolio[symbol2]
            return holding1.Invested and holding2.Invested
        except:
            return False

    def generate_signals(self, modeled_pairs: List[Dict], data) -> List:
        """为所有建模配对生成交易信号"""
        # 检查并更新市场状态
        self._check_market_condition()

        # 判断是否在市场冷静期
        is_market_cooldown = self._is_market_in_cooldown()

        insights = []
        flat_count = 0

        for pair in modeled_pairs:
            pair_with_zscore = self._calculate_zscore(pair, data)
            if pair_with_zscore:
                # 生成信号（市场冷静期会强制平仓）
                pair_insights = self._generate_pair_signals(pair_with_zscore, is_market_cooldown)
                if pair_insights:
                    insights.extend(pair_insights)
                    # 统计平仓信号
                    if len(pair_insights) >= 2 and pair_insights[0].Direction == InsightDirection.Flat:
                        flat_count += 1

        # 输出市场冷静期汇总信息
        if is_market_cooldown and flat_count > 0:
            self.algorithm.Debug(
                f"[SignalGenerator] 市场冷静期：强制平仓{flat_count}对配对，"
                f"剩余冷静期至{self.market_cooldown_until}"
            )

        return insights

    def _calculate_zscore(self, pair: Dict, data) -> Dict:
        """计算配对的当前z-score并应用EMA平滑"""
        symbol1, symbol2 = pair['symbol1'], pair['symbol2']

        # 验证并获取价格
        if not all([data.ContainsKey(s) and data[s] for s in [symbol1, symbol2]]):
            return None

        current_price1 = float(data[symbol1].Close)
        current_price2 = float(data[symbol2].Close)

        # 计算原始z-score
        log_price1 = np.log(current_price1)
        log_price2 = np.log(current_price2)

        # 基于贝叶斯模型计算期望价格关系
        expected = pair['alpha_mean'] + pair['beta_mean'] * log_price2
        residual = log_price1 - expected - pair['residual_mean']

        # 标准化得到z-score
        raw_zscore = residual / pair['residual_std'] if pair['residual_std'] > 0 else 0

        # EMA平滑处理
        pair_key = tuple(sorted([symbol1, symbol2]))
        zscore_ema = self.state.persistent['zscore_ema']

        if pair_key not in zscore_ema:
            smoothed_zscore = raw_zscore
        else:
            # EMA公式：α * current + (1-α) * previous
            smoothed_zscore = self.ema_alpha * raw_zscore + (1 - self.ema_alpha) * zscore_ema[pair_key]

        # 更新EMA存储
        self.state.persistent['zscore_ema'][pair_key] = smoothed_zscore

        # 更新配对信息
        pair.update({
            'zscore': smoothed_zscore,
            'raw_zscore': raw_zscore,
            'current_price1': current_price1,
            'current_price2': current_price2
        })

        return pair

    def _create_insight_group(self, symbol1: Symbol, symbol2: Symbol,
                             direction1: InsightDirection, direction2: InsightDirection,
                             duration_days: int, tag: str):
        """创建配对的Insight组"""
        return Insight.Group(
            Insight.Price(symbol1, timedelta(days=duration_days), direction1,
                         None, None, None, None, tag),
            Insight.Price(symbol2, timedelta(days=duration_days), direction2,
                         None, None, None, None, tag)
        )

    def _generate_pair_signals(self, pair: Dict, is_market_cooldown: bool = False) -> List:
        """基于z-score为单个配对生成信号"""
        symbol1, symbol2 = pair['symbol1'], pair['symbol2']
        zscore = pair['zscore']

        # 构建标签
        quality_score = pair.get('quality_score', 0.5)
        tag = f"{symbol1.Value}&{symbol2.Value}|{pair['alpha_mean']:.4f}|{pair['beta_mean']:.4f}|{zscore:.2f}|{quality_score:.3f}"

        # 市场冷静期：强制平仓
        if is_market_cooldown:
            if self._has_pair_position(symbol1, symbol2):
                self.algorithm.Debug(
                    f"[SignalGenerator-市场风控] 强制平仓: {symbol1.Value}&{symbol2.Value} "
                    f"(z-score={zscore:.2f})"
                )
                return self._create_insight_group(
                    symbol1, symbol2,
                    InsightDirection.Flat, InsightDirection.Flat,
                    self.flat_signal_duration_days, tag
                )
            return []

        # 极端偏离检查
        if abs(zscore) > self.upper_limit:
            self.algorithm.Debug(
                f"[SignalGenerator-极端偏离] 平仓: {symbol1.Value}&{symbol2.Value} "
                f"(z-score={zscore:.2f} 超过±{self.upper_limit})"
            )
            return self._create_insight_group(
                symbol1, symbol2,
                InsightDirection.Flat, InsightDirection.Flat,
                self.flat_signal_duration_days, tag
            )

        # 建仓信号
        if abs(zscore) > self.entry_threshold:
            if not self._has_pair_position(symbol1, symbol2):
                # z>0: 股票1高估，做空1做多2
                # z<0: 股票1低估，做多1做空2
                if zscore > 0:
                    direction1, direction2 = InsightDirection.Down, InsightDirection.Up
                else:
                    direction1, direction2 = InsightDirection.Up, InsightDirection.Down

                return self._create_insight_group(
                    symbol1, symbol2, direction1, direction2,
                    self.entry_signal_duration_days, tag
                )
            return []

        # 平仓信号
        if abs(zscore) < self.exit_threshold:
            if self._has_pair_position(symbol1, symbol2):
                return self._create_insight_group(
                    symbol1, symbol2,
                    InsightDirection.Flat, InsightDirection.Flat,
                    self.flat_signal_duration_days, tag
                )
            return []

        return []

    def _check_market_condition(self):
        """检查市场条件，判断是否需要启动冷静期"""
        # 初始化SPY
        if self.spy_symbol is None:
            try:
                self.spy_symbol = self.algorithm.AddEquity("SPY", Resolution.Daily).Symbol
                self.algorithm.Debug("[SignalGenerator] 初始化SPY市场监控")
            except:
                return

        try:
            # 获取最近2天的历史数据
            history = self.algorithm.History(self.spy_symbol, 2, Resolution.Daily)

            if history.empty or len(history) < 2:
                return

            # 计算前一交易日的涨跌幅
            prev_close = history['close'].iloc[-2]
            last_close = history['close'].iloc[-1]

            if prev_close > 0:
                daily_change = (last_close - prev_close) / prev_close

                # 检查是否触发冷静期
                if daily_change <= -self.market_severe_threshold:
                    from datetime import timedelta
                    self.market_cooldown_until = self.algorithm.Time.date() + timedelta(days=self.market_cooldown_days)

                    self.algorithm.Debug(
                        f"[SignalGenerator] 市场风险预警: SPY单日下跌{-daily_change:.2%}，"
                        f"启动{self.market_cooldown_days}天冷静期至{self.market_cooldown_until}"
                    )

        except Exception:
            pass

    def _is_market_in_cooldown(self) -> bool:
        """检查是否在市场冷静期内"""
        if self.market_cooldown_until and self.algorithm.Time.date() <= self.market_cooldown_until:
            return True
        return False