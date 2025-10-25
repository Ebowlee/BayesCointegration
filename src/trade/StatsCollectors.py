"""
统计收集器集合

职责:
1. 各统计类型独立的Collector类
2. 统一接口: update(...) + log_summary(algorithm)
3. 轻量级设计,不继承基类 (避免过度抽象)

设计原则:
- 单一职责: 每个Collector只负责一种统计
- 内聚性高: 统计逻辑和日志输出在同一个类
- 无继承: 简单直接,避免抽象层
"""

import json
from typing import Dict, Tuple
from datetime import datetime


class ReasonStatsCollector:
    """平仓原因统计收集器"""

    def __init__(self):
        self.stats: Dict[str, Dict] = {}

    def update(self, reason: str, pnl_pct: float, holding_days: int):
        """更新统计"""
        if reason not in self.stats:
            self.stats[reason] = {
                'count': 0,
                'wins': 0,
                'total_pnl': 0.0,
                'total_holding_days': 0
            }

        stat = self.stats[reason]
        stat['count'] += 1
        if pnl_pct > 0:
            stat['wins'] += 1
        stat['total_pnl'] += pnl_pct
        stat['total_holding_days'] += holding_days

    def log_summary(self, algorithm):
        """输出汇总统计 (JSON Lines)"""
        for reason, stat in self.stats.items():
            win_rate = stat['wins'] / stat['count'] if stat['count'] > 0 else 0
            avg_pnl = stat['total_pnl'] / stat['count'] if stat['count'] > 0 else 0
            avg_holding_days = stat['total_holding_days'] / stat['count'] if stat['count'] > 0 else 0

            log_data = {
                'type': 'reason_stats',
                'reason': reason,
                'count': stat['count'],
                'win_rate': round(win_rate, 4),
                'avg_pnl': round(avg_pnl, 4),
                'avg_holding_days': round(avg_holding_days, 1),
            }

            algorithm.Debug(json.dumps(log_data, ensure_ascii=False))


class HoldingBucketCollector:
    """持仓时长分桶统计收集器"""

    def __init__(self):
        self.buckets: Dict[str, Dict] = {}

    def update(self, holding_days: int, pnl_pct: float):
        """更新统计"""
        # 分桶: 0-7天, 8-14天, 15-30天, 30天+
        if holding_days <= 7:
            bucket = '0-7天'
        elif holding_days <= 14:
            bucket = '8-14天'
        elif holding_days <= 30:
            bucket = '15-30天'
        else:
            bucket = '30天+'

        if bucket not in self.buckets:
            self.buckets[bucket] = {'count': 0, 'wins': 0, 'total_pnl': 0.0}

        stat = self.buckets[bucket]
        stat['count'] += 1
        if pnl_pct > 0:
            stat['wins'] += 1
        stat['total_pnl'] += pnl_pct

    def log_summary(self, algorithm):
        """输出汇总统计 (JSON Lines)"""
        # 按桶顺序输出
        bucket_order = ['0-7天', '8-14天', '15-30天', '30天+']
        for bucket in bucket_order:
            if bucket not in self.buckets:
                continue

            stat = self.buckets[bucket]
            win_rate = stat['wins'] / stat['count'] if stat['count'] > 0 else 0
            avg_pnl = stat['total_pnl'] / stat['count'] if stat['count'] > 0 else 0

            log_data = {
                'type': 'holding_bucket_stats',
                'bucket': bucket,
                'count': stat['count'],
                'win_rate': round(win_rate, 4),
                'avg_pnl': round(avg_pnl, 4),
            }

            algorithm.Debug(json.dumps(log_data, ensure_ascii=False))


class PairStatsCollector:
    """配对统计收集器 (用于识别"坏配对")"""

    def __init__(self):
        self.stats: Dict[Tuple[str, str], Dict] = {}

    def update(self, pair_id: Tuple[str, str], pnl_pct: float):
        """更新统计"""
        if pair_id not in self.stats:
            self.stats[pair_id] = {'count': 0, 'wins': 0, 'total_pnl': 0.0}

        stat = self.stats[pair_id]
        stat['count'] += 1
        if pnl_pct > 0:
            stat['wins'] += 1
        stat['total_pnl'] += pnl_pct

    def log_summary(self, algorithm):
        """输出"坏配对"识别 (交易次数>=3且累计亏损)"""
        bad_pairs = [
            (pair_id, stat)
            for pair_id, stat in self.stats.items()
            if stat['count'] >= 3 and stat['total_pnl'] < 0
        ]

        # 按累计亏损排序
        bad_pairs.sort(key=lambda x: x[1]['total_pnl'])

        for pair_id, stat in bad_pairs:
            win_rate = stat['wins'] / stat['count'] if stat['count'] > 0 else 0

            log_data = {
                'type': 'bad_pair',
                'pair_id': str(pair_id),
                'trade_count': stat['count'],
                'total_pnl': round(stat['total_pnl'], 4),
                'win_rate': round(win_rate, 4),
            }

            algorithm.Debug(json.dumps(log_data, ensure_ascii=False))


class ConsecutiveStatsCollector:
    """连续盈亏统计收集器"""

    def __init__(self):
        self.consecutive_wins = 0
        self.consecutive_losses = 0
        self.max_consecutive_wins = 0
        self.max_consecutive_losses = 0

    def update(self, pnl_pct: float):
        """更新统计"""
        if pnl_pct > 0:
            self.consecutive_wins += 1
            self.consecutive_losses = 0
            self.max_consecutive_wins = max(self.max_consecutive_wins, self.consecutive_wins)
        else:
            self.consecutive_losses += 1
            self.consecutive_wins = 0
            self.max_consecutive_losses = max(self.max_consecutive_losses, self.consecutive_losses)

    def log_summary(self, algorithm):
        """输出汇总统计 (JSON Lines)"""
        log_data = {
            'type': 'consecutive_stats',
            'max_consecutive_wins': self.max_consecutive_wins,
            'max_consecutive_losses': self.max_consecutive_losses,
        }

        algorithm.Debug(json.dumps(log_data, ensure_ascii=False))


class MonthlyStatsCollector:
    """月度统计收集器"""

    def __init__(self):
        self.stats: Dict[str, Dict] = {}

    def update(self, timestamp: datetime, pnl_pct: float):
        """更新统计"""
        month_key = timestamp.strftime('%Y-%m')

        if month_key not in self.stats:
            self.stats[month_key] = {'count': 0, 'wins': 0, 'total_pnl': 0.0}

        stat = self.stats[month_key]
        stat['count'] += 1
        if pnl_pct > 0:
            stat['wins'] += 1
        stat['total_pnl'] += pnl_pct

    def log_summary(self, algorithm):
        """输出汇总统计 (JSON Lines)"""
        for month, stat in sorted(self.stats.items()):
            win_rate = stat['wins'] / stat['count'] if stat['count'] > 0 else 0

            log_data = {
                'type': 'monthly_stats',
                'month': month,
                'trades': stat['count'],
                'win_rate': round(win_rate, 4),
                'total_pnl': round(stat['total_pnl'], 4),
            }

            algorithm.Debug(json.dumps(log_data, ensure_ascii=False))
