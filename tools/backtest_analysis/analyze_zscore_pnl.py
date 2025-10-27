#!/usr/bin/env python3
"""
Z-Score与PnL深度分析工具

功能:
- 从日志中提取所有交易记录
- 分析进入/退出Z-score分布
- 分析盈亏与Z-score的关联
- 对比不同平仓原因的表现
- 生成完整排序表和分析报告

Usage:
    python tools/backtest_analysis/analyze_zscore_pnl.py backtests/Casual_Apricot_Chicken_logs.txt
"""

import json
import re
import sys
from pathlib import Path
from typing import List, Dict, Optional
import statistics
from collections import defaultdict


def load_trade_data(log_file_path: str) -> List[Dict]:
    """
    从日志文件中提取所有trade_close记录

    Args:
        log_file_path: 日志文件路径

    Returns:
        List[Dict]: 交易记录列表
    """
    trades = []

    with open(log_file_path, 'r', encoding='utf-8') as f:
        for line in f:
            if '"type": "trade_close"' in line:
                # 提取JSON部分 (格式: "YYYY-MM-DD HH:MM:SS {json...}")
                json_match = re.search(r'\{.*\}', line)
                if json_match:
                    try:
                        trade_data = json.loads(json_match.group())
                        trades.append(trade_data)
                    except json.JSONDecodeError as e:
                        print(f"警告: 无法解析JSON: {line.strip()}")
                        print(f"错误: {e}")

    print(f"成功加载 {len(trades)} 笔交易记录")
    return trades


def calculate_statistics(values: List[float]) -> Dict[str, float]:
    """
    计算描述性统计

    Args:
        values: 数值列表

    Returns:
        Dict: 统计指标
    """
    if not values:
        return {}

    sorted_values = sorted(values)
    n = len(values)

    return {
        'count': n,
        'mean': statistics.mean(values),
        'median': statistics.median(values),
        'stdev': statistics.stdev(values) if n > 1 else 0,
        'min': min(values),
        'max': max(values),
        'q25': sorted_values[n // 4],
        'q75': sorted_values[3 * n // 4]
    }


def analyze_zscore_distribution(trades: List[Dict]) -> Dict:
    """
    分析Z-Score分布

    Args:
        trades: 交易记录列表

    Returns:
        Dict: 分布统计结果
    """
    print("\n" + "="*80)
    print("Z-SCORE 分布分析")
    print("="*80)

    # 提取entry_zscore
    entry_zscores = [t['entry_zscore'] for t in trades if 'entry_zscore' in t]
    entry_stats = calculate_statistics(entry_zscores)

    # 提取exit_zscore (仅正常平仓)
    exit_zscores = [t['exit_zscore'] for t in trades if 'exit_zscore' in t]
    exit_stats = calculate_statistics(exit_zscores)

    print(f"\n【Entry Z-Score 统计】(共{entry_stats['count']}笔)")
    print(f"  均值:     {entry_stats['mean']:6.2f}")
    print(f"  中位数:   {entry_stats['median']:6.2f}")
    print(f"  标准差:   {entry_stats['stdev']:6.2f}")
    print(f"  最小值:   {entry_stats['min']:6.2f}")
    print(f"  25%分位:  {entry_stats['q25']:6.2f}")
    print(f"  75%分位:  {entry_stats['q75']:6.2f}")
    print(f"  最大值:   {entry_stats['max']:6.2f}")

    print(f"\n【Exit Z-Score 统计】(共{exit_stats['count']}笔正常平仓)")
    print(f"  均值:     {exit_stats['mean']:6.2f}")
    print(f"  中位数:   {exit_stats['median']:6.2f}")
    print(f"  标准差:   {exit_stats['stdev']:6.2f}")
    print(f"  最小值:   {exit_stats['min']:6.2f}")
    print(f"  25%分位:  {exit_stats['q25']:6.2f}")
    print(f"  75%分位:  {exit_stats['q75']:6.2f}")
    print(f"  最大值:   {exit_stats['max']:6.2f}")

    return {
        'entry': entry_stats,
        'exit': exit_stats
    }


def analyze_pnl_by_zscore(trades: List[Dict]) -> None:
    """
    按Entry Z-Score区间分析盈亏表现

    Args:
        trades: 交易记录列表
    """
    print("\n" + "="*80)
    print("盈亏 vs Entry Z-Score 关联分析")
    print("="*80)

    # 定义Z-score区间
    bins = [
        ('[-∞, -1.5)', float('-inf'), -1.5),
        ('[-1.5, -1.0)', -1.5, -1.0),
        ('[-1.0, 0)', -1.0, 0),
        ('[0, 1.0)', 0, 1.0),
        ('[1.0, 1.5)', 1.0, 1.5),
        ('[1.5, 2.0)', 1.5, 2.0),
        ('[2.0, 3.0)', 2.0, 3.0),
        ('[3.0, +∞)', 3.0, float('inf'))
    ]

    # 分组统计
    grouped = defaultdict(list)
    for trade in trades:
        z = trade.get('entry_zscore')
        pnl = trade.get('pnl_pct', 0)

        if z is not None:
            for label, min_z, max_z in bins:
                if min_z <= z < max_z:
                    grouped[label].append((z, pnl, trade))
                    break

    print(f"\n{'Entry Z区间':<15} {'交易数':>6} {'胜率':>8} {'平均盈利%':>12} {'平均亏损%':>12} {'盈亏比':>8}")
    print("-" * 80)

    for label, min_z, max_z in bins:
        trades_in_bin = grouped.get(label, [])
        if not trades_in_bin:
            continue

        count = len(trades_in_bin)
        pnls = [pnl for _, pnl, _ in trades_in_bin]

        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]

        win_rate = len(wins) / count if count > 0 else 0
        avg_win = statistics.mean(wins) if wins else 0
        avg_loss = statistics.mean(losses) if losses else 0
        profit_loss_ratio = abs(avg_win / avg_loss) if avg_loss != 0 else float('inf')

        print(f"{label:<15} {count:>6} {win_rate:>7.1%} {avg_win:>11.1f}% {avg_loss:>11.1f}% {profit_loss_ratio:>7.2f}")


def analyze_by_close_reason(trades: List[Dict]) -> None:
    """
    按平仓原因对比分析

    Args:
        trades: 交易记录列表
    """
    print("\n" + "="*80)
    print("平仓原因对比分析")
    print("="*80)

    # 按reason分组
    grouped = defaultdict(list)
    for trade in trades:
        reason = trade.get('reason', 'UNKNOWN')
        grouped[reason].append(trade)

    print(f"\n{'平仓原因':<20} {'数量':>6} {'占比':>8} {'胜率':>8} {'平均PnL%':>12} {'Entry Z均值':>12} {'Exit Z均值':>12}")
    print("-" * 90)

    total_trades = len(trades)

    for reason, reason_trades in sorted(grouped.items(), key=lambda x: len(x[1]), reverse=True):
        count = len(reason_trades)
        ratio = count / total_trades

        pnls = [t.get('pnl_pct', 0) for t in reason_trades]
        wins = [p for p in pnls if p > 0]
        win_rate = len(wins) / count if count > 0 else 0
        avg_pnl = statistics.mean(pnls) if pnls else 0

        entry_zs = [t.get('entry_zscore') for t in reason_trades if t.get('entry_zscore') is not None]
        exit_zs = [t.get('exit_zscore') for t in reason_trades if t.get('exit_zscore') is not None]

        avg_entry_z = statistics.mean(entry_zs) if entry_zs else 0
        avg_exit_z = statistics.mean(exit_zs) if exit_zs else None

        exit_z_str = f"{avg_exit_z:>11.2f}" if avg_exit_z is not None else "N/A".rjust(12)

        print(f"{reason:<20} {count:>6} {ratio:>7.1%} {win_rate:>7.1%} {avg_pnl:>11.1f}% {avg_entry_z:>11.2f} {exit_z_str}")


def analyze_quality_correlation(trades: List[Dict]) -> None:
    """
    分析质量分数与盈利能力的关系

    Args:
        trades: 交易记录列表
    """
    print("\n" + "="*80)
    print("质量分数相关性分析")
    print("="*80)

    # 定义quality_score区间
    bins = [
        ('[0.40-0.60)', 0.40, 0.60),
        ('[0.60-0.70)', 0.60, 0.70),
        ('[0.70-0.80)', 0.70, 0.80),
        ('[0.80-0.90)', 0.80, 0.90),
        ('[0.90-1.00]', 0.90, 1.01)
    ]

    # 分组统计
    grouped = defaultdict(list)
    for trade in trades:
        q = trade.get('quality_score')
        pnl = trade.get('pnl_pct', 0)

        if q is not None:
            for label, min_q, max_q in bins:
                if min_q <= q < max_q:
                    grouped[label].append((q, pnl, trade))
                    break

    print(f"\n{'Quality区间':<15} {'交易数':>6} {'胜率':>8} {'平均PnL%':>12} {'盈亏比':>8}")
    print("-" * 60)

    for label, min_q, max_q in bins:
        trades_in_bin = grouped.get(label, [])
        if not trades_in_bin:
            continue

        count = len(trades_in_bin)
        pnls = [pnl for _, pnl, _ in trades_in_bin]

        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]

        win_rate = len(wins) / count if count > 0 else 0
        avg_pnl = statistics.mean(pnls) if pnls else 0

        avg_win = statistics.mean(wins) if wins else 0
        avg_loss = statistics.mean(losses) if losses else 0
        profit_loss_ratio = abs(avg_win / avg_loss) if avg_loss != 0 else float('inf')

        print(f"{label:<15} {count:>6} {win_rate:>7.1%} {avg_pnl:>11.1f}% {profit_loss_ratio:>7.2f}")


def rank_and_display_trades(trades: List[Dict]) -> None:
    """
    按PnL排序并显示Top/Bottom交易

    Args:
        trades: 交易记录列表
    """
    print("\n" + "="*80)
    print("完整交易排序表（按PnL降序）")
    print("="*80)

    # 按pnl_pct降序排序
    sorted_trades = sorted(trades, key=lambda t: t.get('pnl_pct', 0), reverse=True)

    # 打印表头
    print(f"\n{'排名':>4} {'Pair ID':<25} {'Entry Z':>8} {'Exit Z':>8} {'PnL%':>10} {'Days':>5} {'Quality':>8} {'Reason':<15}")
    print("-" * 105)

    # 打印所有交易
    for rank, trade in enumerate(sorted_trades, 1):
        pair_id = trade.get('pair_id', 'N/A')
        entry_z = trade.get('entry_zscore', 0)
        exit_z = trade.get('exit_zscore')
        pnl_pct = trade.get('pnl_pct', 0)
        days = trade.get('holding_days', 0)
        quality = trade.get('quality_score', 0)
        reason = trade.get('reason', 'UNKNOWN')

        exit_z_str = f"{exit_z:>7.2f}" if exit_z is not None else "N/A".rjust(8)

        print(f"{rank:>4} {pair_id:<25} {entry_z:>7.2f} {exit_z_str} {pnl_pct:>9.1f}% {days:>5} {quality:>7.3f} {reason:<15}")

    # 总结Top 10和Bottom 10
    print("\n" + "="*80)
    print("Top 10 盈利交易特征")
    print("="*80)

    top_10 = sorted_trades[:10]
    avg_entry_z_top = statistics.mean([t['entry_zscore'] for t in top_10 if t.get('entry_zscore') is not None])
    avg_quality_top = statistics.mean([t['quality_score'] for t in top_10 if t.get('quality_score') is not None])
    avg_days_top = statistics.mean([t['holding_days'] for t in top_10])

    print(f"  平均Entry Z-Score: {avg_entry_z_top:.2f}")
    print(f"  平均Quality Score: {avg_quality_top:.3f}")
    print(f"  平均持仓天数:     {avg_days_top:.1f}天")

    print("\n" + "="*80)
    print("Bottom 10 亏损交易特征")
    print("="*80)

    bottom_10 = sorted_trades[-10:]
    avg_entry_z_bottom = statistics.mean([t['entry_zscore'] for t in bottom_10 if t.get('entry_zscore') is not None])
    avg_quality_bottom = statistics.mean([t['quality_score'] for t in bottom_10 if t.get('quality_score') is not None])
    avg_days_bottom = statistics.mean([t['holding_days'] for t in bottom_10])

    # 统计平仓原因
    reason_counts = defaultdict(int)
    for t in bottom_10:
        reason_counts[t.get('reason', 'UNKNOWN')] += 1

    print(f"  平均Entry Z-Score: {avg_entry_z_bottom:.2f}")
    print(f"  平均Quality Score: {avg_quality_bottom:.3f}")
    print(f"  平均持仓天数:     {avg_days_bottom:.1f}天")
    print(f"  平仓原因分布:")
    for reason, count in sorted(reason_counts.items(), key=lambda x: x[1], reverse=True):
        print(f"    - {reason}: {count}笔")


def main():
    """主函数"""
    if len(sys.argv) < 2:
        print("用法: python analyze_zscore_pnl.py <日志文件路径>")
        print("示例: python analyze_zscore_pnl.py backtests/Casual_Apricot_Chicken_logs.txt")
        sys.exit(1)

    log_file = sys.argv[1]

    if not Path(log_file).exists():
        print(f"错误: 文件不存在: {log_file}")
        sys.exit(1)

    print("="*80)
    print("Z-SCORE 与 PNL 深度分析报告")
    print("="*80)
    print(f"日志文件: {log_file}")

    # 加载数据
    trades = load_trade_data(log_file)

    if not trades:
        print("错误: 未找到任何交易记录")
        sys.exit(1)

    # 执行分析
    analyze_zscore_distribution(trades)
    analyze_pnl_by_zscore(trades)
    analyze_by_close_reason(trades)
    analyze_quality_correlation(trades)
    rank_and_display_trades(trades)

    print("\n" + "="*80)
    print("分析完成！")
    print("="*80)


if __name__ == '__main__':
    main()
