#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分析v6.7.0回测结果并对比baseline"""

import json
import sys

def load_backtest(filename):
    """加载回测JSON文件"""
    with open(f'backtests/{filename}', 'r', encoding='utf-8-sig') as f:
        return json.load(f)

def extract_metrics(data):
    """提取关键指标"""
    stats = data.get('statistics', {})
    perf = data.get('totalPerformance', {}).get('PortfolioStatistics', {})
    runtime = data.get('runtimeStatistics', {})

    return {
        'period': f"{runtime.get('Start Date', 'N/A')} -> {runtime.get('End Date', 'N/A')}",
        'total_return': perf.get('CompoundingAnnualReturn', 'N/A'),
        'max_drawdown': perf.get('Drawdown', stats.get('Drawdown', 'N/A')),
        'sharpe': perf.get('SharpeRatio', stats.get('Sharpe Ratio', 'N/A')),
        'sortino': perf.get('SortinoRatio', 'N/A'),
        'win_rate': perf.get('WinRate', stats.get('Win Rate', 'N/A')),
        'total_trades': perf.get('TotalNumberOfTrades', stats.get('Total Trades', 'N/A')),
        'alpha': perf.get('Alpha', stats.get('Alpha', 'N/A')),
        'beta': perf.get('Beta', stats.get('Beta', 'N/A')),
        'info_ratio': perf.get('InformationRatio', stats.get('Information Ratio', 'N/A')),
        'tracking_error': perf.get('TrackingError', stats.get('Tracking Error', 'N/A')),
    }

def print_metrics(version, metrics):
    """打印指标"""
    print(f"\n{'='*60}")
    print(f"{version} 核心指标")
    print(f"{'='*60}")
    print(f"回测期间: {metrics['period']}")
    print(f"\n【收益指标】")
    print(f"  Total/Annual Return: {metrics['total_return']}")
    print(f"\n【风险指标】")
    print(f"  Max Drawdown: {metrics['max_drawdown']}")
    print(f"\n【风险调整收益】")
    print(f"  Sharpe Ratio: {metrics['sharpe']}")
    print(f"  Sortino Ratio: {metrics['sortino']}")
    print(f"\n【交易表现】")
    print(f"  Win Rate: {metrics['win_rate']}")
    print(f"  Total Trades: {metrics['total_trades']}")
    print(f"\n【基准对比】")
    print(f"  Alpha: {metrics['alpha']}")
    print(f"  Beta: {metrics['beta']}")
    print(f"  Information Ratio: {metrics['info_ratio']}")
    print(f"  Tracking Error: {metrics['tracking_error']}")

def compare_metrics(v670, baseline):
    """对比两个版本的指标"""
    print(f"\n{'='*60}")
    print("v6.7.0 vs v6.6.2-baseline 对比分析")
    print(f"{'='*60}")

    def calc_change(new, old):
        """计算变化"""
        try:
            new_val = float(new.strip('%')) if isinstance(new, str) and '%' in new else float(new)
            old_val = float(old.strip('%')) if isinstance(old, str) and '%' in old else float(old)
            change = new_val - old_val
            symbol = '↑' if change > 0 else '↓' if change < 0 else '→'
            return f"{symbol} {change:+.4f}"
        except:
            return "N/A"

    comparisons = [
        ('Alpha', 'alpha', '目标≥1%'),
        ('Beta', 'beta', '目标<0.6'),
        ('Information Ratio', 'info_ratio', '目标>0.5'),
        ('Sharpe Ratio', 'sharpe', '越高越好'),
        ('Max Drawdown', 'max_drawdown', '目标≤5%'),
        ('Win Rate', 'win_rate', '越高越好'),
    ]

    for name, key, target in comparisons:
        v670_val = v670[key]
        baseline_val = baseline.get(key, 'N/A')
        change = calc_change(v670_val, baseline_val) if baseline_val != 'N/A' else 'N/A'
        print(f"\n{name}:")
        print(f"  v6.7.0:    {v670_val}")
        print(f"  baseline:  {baseline_val}")
        print(f"  变化:      {change}")
        print(f"  目标:      {target}")

if __name__ == '__main__':
    # 加载v6.7.0数据
    try:
        v670_data = load_backtest('Upgraded Black Bee.json')
        v670_metrics = extract_metrics(v670_data)
        print_metrics("v6.7.0 (Upgraded Black Bee)", v670_metrics)
    except Exception as e:
        print(f"加载v6.7.0失败: {e}")
        sys.exit(1)

    # 加载baseline数据 (如果存在)
    try:
        baseline_data = load_backtest('Logical Asparagus Beaver.json')
        baseline_metrics = extract_metrics(baseline_data)
        print_metrics("v6.6.2-baseline (Logical Asparagus Beaver)", baseline_metrics)

        # 对比分析
        compare_metrics(v670_metrics, baseline_metrics)
    except FileNotFoundError:
        print("\n注意: 未找到baseline回测文件,无法进行对比")
        # 使用文档中的baseline数据
        baseline_metrics = {
            'alpha': '0.43%',
            'beta': '0.0812',
            'info_ratio': '-1.3053',
            'sharpe': '0.753',
            'max_drawdown': '4.10%',
            'win_rate': '47%',
        }
        print("\n使用CHANGELOG中记录的baseline数据进行对比:")
        compare_metrics(v670_metrics, baseline_metrics)
    except Exception as e:
        print(f"加载baseline失败: {e}")
