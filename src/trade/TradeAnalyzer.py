"""
交易分析器 - 主协调器

职责:
1. 初始化所有统计收集器
2. 协调analyze_trade()调用
3. 协调log_summary()调用
4. 不包含具体统计逻辑 (委托给Collectors)

设计模式:
- Facade Pattern: 对外提供简洁接口
- Delegation Pattern: 委托具体统计给Collectors
"""

import json
from typing import TYPE_CHECKING
from .StatsCollectors import (
    ReasonStatsCollector,
    HoldingBucketCollector,
    PairStatsCollector,
    ConsecutiveStatsCollector,
    MonthlyStatsCollector
)

if TYPE_CHECKING:
    from ..Pairs import Pairs


class TradeAnalyzer:
    """
    交易分析器 (主协调器)

    设计原则:
    - 不存储历史交易 (trades.csv已有)
    - 委托具体统计给Collectors
    - 所有输出通过Debug() → logs.txt
    - JSON Lines格式 (AI解析友好)

    核心价值:
    - 补充insights.json的信息盲区
    - 提供细粒度分组统计(reason/signal/holding/pair/monthly)
    - 识别"坏配对"和连续亏损
    """

    def __init__(self, algorithm):
        self.algorithm = algorithm

        # 全局统计
        self.total_trades = 0
        self.profitable_trades = 0
        self.total_pnl = 0.0

        # 统计收集器 (委托模式)
        self.reason_collector = ReasonStatsCollector()
        self.holding_collector = HoldingBucketCollector()
        self.pair_collector = PairStatsCollector()
        self.consecutive_collector = ConsecutiveStatsCollector()
        self.monthly_collector = MonthlyStatsCollector()

    def analyze_trade(self, pair: 'Pairs', reason: str):
        """
        分析单笔交易 (委托给各Collector)

        在平仓时调用 (main.py执行完平仓后立即调用)

        Args:
            pair: Pairs对象
            reason: 平仓原因 ('CLOSE', 'STOP_LOSS', 'TIMEOUT', etc.)
        """
        # 1. 提取交易数据
        pnl_pct = pair.get_pair_pnl()
        holding_days = pair.get_pair_holding_days()
        pair_id = pair.pair_id
        exit_zscore = pair.get_zscore()

        # 2. 更新全局统计
        self.total_trades += 1
        if pnl_pct > 0:
            self.profitable_trades += 1
        self.total_pnl += pnl_pct

        # 3. 委托给各Collector更新统计
        self.reason_collector.update(reason, pnl_pct, holding_days)
        self.holding_collector.update(holding_days, pnl_pct)
        self.pair_collector.update(pair_id, pnl_pct)
        self.consecutive_collector.update(pnl_pct)
        self.monthly_collector.update(self.algorithm.Time, pnl_pct)

        # 4. 输出单笔交易日志 (JSON Lines)
        self._log_trade_close(pair, reason, pnl_pct, holding_days, exit_zscore)

    def log_summary(self):
        """
        OnEndOfAlgorithm时输出汇总统计 (委托给各Collector)

        输出内容:
        1. 全局汇总
        2. 平仓原因统计 (按reason分组)
        3. 持仓时长统计 (按holding_days分桶)
        4. "坏配对"识别 (交易次数>=3且累计亏损)
        5. 连续盈亏统计
        6. 月度表现分解
        """
        # 输出全局汇总
        self._log_global_summary()

        # 委托给各Collector输出汇总
        self.reason_collector.log_summary(self.algorithm)
        self.holding_collector.log_summary(self.algorithm)
        self.pair_collector.log_summary(self.algorithm)
        self.consecutive_collector.log_summary(self.algorithm)
        self.monthly_collector.log_summary(self.algorithm)

    # ========== 内部方法 (日志输出) ==========

    def _log_trade_close(self, pair, reason, pnl_pct, holding_days, exit_zscore):
        """输出单笔交易日志 (JSON Lines)"""
        log_data = {
            'type': 'trade_close',
            'pair_id': str(pair.pair_id),
            'reason': reason,
            'pnl_pct': round(pnl_pct, 4),
            'holding_days': holding_days,
            'exit_zscore': round(exit_zscore, 2),
        }

        self.algorithm.Debug(json.dumps(log_data, ensure_ascii=False))

    def _log_global_summary(self):
        """输出全局汇总"""
        win_rate = self.profitable_trades / self.total_trades if self.total_trades > 0 else 0
        avg_pnl = self.total_pnl / self.total_trades if self.total_trades > 0 else 0

        summary = {
            'type': 'trade_summary',
            'total_trades': self.total_trades,
            'profitable_trades': self.profitable_trades,
            'win_rate': round(win_rate, 4),
            'total_pnl': round(self.total_pnl, 4),
            'avg_pnl': round(avg_pnl, 4),
        }

        self.algorithm.Debug(json.dumps(summary, ensure_ascii=False))
