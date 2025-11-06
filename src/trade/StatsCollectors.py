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
from typing import Dict, Tuple, Set, Optional
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
    """
    配对统计收集器

    双重职责 (v7.6.0):
    1. 向后: 输出日志供回测分析 (识别"坏配对")
    2. 向前: 提供黑名单给PairSelector过滤

    黑名单标准:
    - 交易次数 >= 3 (确保统计意义)
    - 累计收益率 < 0 (累计亏损)
    """

    def __init__(self):
        self.stats: Dict[Tuple[str, str], Dict] = {}
        # 黑名单缓存 (优化查询性能)
        self._blacklist_cache: Set[Tuple[str, str]] = set()
        self._blacklist_dirty = False  # 脏位标记，指示黑名单需要重算

    def update(self, pair_id: Tuple[str, str], pnl_pct: float):
        """更新统计"""
        if pair_id not in self.stats:
            self.stats[pair_id] = {'count': 0, 'wins': 0, 'total_pnl': 0.0}

        stat = self.stats[pair_id]
        stat['count'] += 1
        if pnl_pct > 0:
            stat['wins'] += 1
        stat['total_pnl'] += pnl_pct

        # 标记脏位: 统计数据改变，黑名单需要重算
        self._blacklist_dirty = True

    def get_blacklist(self) -> Set[Tuple[str, str]]:
        """
        获取黑名单 (供PairSelector调用)

        黑名单标准 (与log_summary一致):
        - count >= 3 (至少3笔交易)
        - total_pnl < 0 (累计亏损)

        设计说明:
        - 返回Set而非Dict，简化调用方逻辑
        - 脏位机制避免重复计算
        - O(1)查询性能

        Returns:
            黑名单集合: Set[Tuple[str, str]]
        """
        # 脏位检查: 只在数据改变时重算
        if self._blacklist_dirty:
            self._blacklist_cache = {
                pair_id
                for pair_id, stat in self.stats.items()
                if stat['count'] >= 3 and stat['total_pnl'] < 0
            }
            self._blacklist_dirty = False

        return self._blacklist_cache

    def is_blacklisted(self, pair_id: Tuple[str, str]) -> bool:
        """
        检查单个配对是否黑名单

        供PairSelector单对象检查调用 (O(1)性能)

        Args:
            pair_id: 配对ID元组

        Returns:
            True if 黑名单, False otherwise
        """
        return pair_id in self.get_blacklist()

    def get_blacklist_stats(self, pair_id: Tuple[str, str]) -> Optional[Dict]:
        """
        获取黑名单配对的统计信息 (用于日志或诊断)

        Args:
            pair_id: 配对ID

        Returns:
            统计数据或None (不在黑名单)
        """
        if not self.is_blacklisted(pair_id):
            return None

        return self.stats.get(pair_id)

    def log_summary(self, algorithm):
        """
        输出"坏配对"识别 (交易次数>=3且累计亏损)

        改动 (v7.6.0):
        - 新增black_mark字段标记黑名单配对
        - 其他逻辑保持不变
        """
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
                'black_mark': True,  # 新增：标记为黑名单
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
