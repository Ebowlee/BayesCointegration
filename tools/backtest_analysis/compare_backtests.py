import pandas as pd
import json
from datetime import datetime
import numpy as np
import os

# 获取backtests目录路径 (相对于此脚本的位置)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BACKTESTS_DIR = os.path.join(SCRIPT_DIR, '..', '..', 'backtests')

# 定义回测文件 (使用相对路径)
backtests = {
    'Smooth Brown Pig (v7.2.8)': {
        'trades': os.path.join(BACKTESTS_DIR, 'Smooth Brown Pig_trades.csv'),
        'json': os.path.join(BACKTESTS_DIR, 'Smooth Brown Pig.json'),
        'logs': os.path.join(BACKTESTS_DIR, 'Smooth Brown Pig_logs.txt'),
        'params': {'sigma': 2.5, 'max_days': 45, 'max_acceptable_days': 45}
    },
    'Swimming Asparagus Lion (v7.2.9)': {
        'trades': os.path.join(BACKTESTS_DIR, 'Swimming Asparagus Lion_trades.csv'),
        'json': os.path.join(BACKTESTS_DIR, 'Swimming Asparagus Lion.json'),
        'logs': os.path.join(BACKTESTS_DIR, 'Swimming Asparagus Lion_logs.txt'),
        'params': {'sigma': 2.0, 'max_days': 45, 'max_acceptable_days': 45}
    },
    'Virtual Fluorescent Yellow Dogfish (v7.2.10)': {
        'trades': os.path.join(BACKTESTS_DIR, 'Virtual Fluorescent Yellow Dogfish_trades.csv'),
        'json': os.path.join(BACKTESTS_DIR, 'Virtual Fluorescent Yellow Dogfish.json'),
        'logs': os.path.join(BACKTESTS_DIR, 'Virtual Fluorescent Yellow Dogfish_logs.txt'),
        'params': {'sigma': 2.0, 'max_days': 30, 'zero_score_threshold': 60}
    }
}

def analyze_trades(trades_file):
    """分析交易数据"""
    df = pd.read_csv(trades_file)

    # 解析Tag获取交易原因
    df['pair_id'] = df['Tag'].str.extract(r"'([^']+)',\s*'([^']+)'").apply(lambda x: f"({x[0]}, {x[1]})" if pd.notna(x[0]) else None, axis=1)
    df['action'] = df['Tag'].str.extract(r'_(OPEN|CLOSE)_')[0]
    df['reason'] = df['Tag'].apply(lambda x: x.split('_')[-2] if 'CLOSE' in x else 'OPEN')

    # 统计结果
    stats = {
        'total_trades': len(df),
        'unique_pairs': df['pair_id'].nunique(),
        'open_trades': len(df[df['action'] == 'OPEN']),
        'close_trades': len(df[df['action'] == 'CLOSE']),
    }

    # 平仓原因统计
    close_df = df[df['action'] == 'CLOSE']
    if len(close_df) > 0:
        close_reasons = close_df['reason'].value_counts()
        stats['close_reasons'] = close_reasons.to_dict()

    # 计算持仓时长
    trades_by_pair = []
    for pair_id in df['pair_id'].unique():
        if pd.isna(pair_id):
            continue
        pair_df = df[df['pair_id'] == pair_id].copy()
        pair_df['Time'] = pd.to_datetime(pair_df['Time'])

        opens = pair_df[pair_df['action'] == 'OPEN'].groupby('Time').first()
        closes = pair_df[pair_df['action'] == 'CLOSE'].groupby('Time').first()

        for open_time in opens.index:
            # 找到对应的平仓
            future_closes = closes[closes.index > open_time]
            if len(future_closes) > 0:
                close_time = future_closes.index[0]
                close_reason = future_closes.iloc[0]['reason']
                holding_days = (close_time - open_time).days
                trades_by_pair.append({
                    'pair': pair_id,
                    'open_time': open_time,
                    'close_time': close_time,
                    'holding_days': holding_days,
                    'close_reason': close_reason
                })

    if trades_by_pair:
        holding_df = pd.DataFrame(trades_by_pair)
        stats['avg_holding_days'] = holding_df['holding_days'].mean()
        stats['max_holding_days'] = holding_df['holding_days'].max()
        stats['min_holding_days'] = holding_df['holding_days'].min()

        # 持仓时长分布
        bins = [0, 7, 14, 21, 30, 45, 100]
        labels = ['0-7d', '8-14d', '15-21d', '22-30d', '31-45d', '45d+']
        holding_df['holding_bucket'] = pd.cut(holding_df['holding_days'], bins=bins, labels=labels)
        stats['holding_distribution'] = holding_df['holding_bucket'].value_counts().to_dict()

        # 30天和45天超时统计
        stats['trades_over_30_days'] = len(holding_df[holding_df['holding_days'] > 30])
        stats['trades_over_45_days'] = len(holding_df[holding_df['holding_days'] > 45])
        stats['timeout_trades'] = len(holding_df[holding_df['close_reason'] == 'TIMEOUT'])

    return stats

def analyze_json(json_file):
    """分析JSON回测结果"""
    with open(json_file, 'r') as f:
        data = json.load(f)

    # 提取关键指标
    stats = data.get('Statistics', {})
    return {
        'total_return': float(stats.get('Total Net Profit', '0').replace('%', '')),
        'sharpe_ratio': float(stats.get('Sharpe Ratio', '0')),
        'annual_return': float(stats.get('Annual Return', '0').replace('%', '')),
        'max_drawdown': float(stats.get('Drawdown', '0').replace('%', '')),
        'win_rate': float(stats.get('Win Rate', '0').replace('%', '')),
        'profit_factor': float(stats.get('Profit Factor', '0')),
        'total_trades': int(stats.get('Total Trades', '0'))
    }

def analyze_logs(logs_file):
    """分析日志文件"""
    with open(logs_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    stats = {
        'pair_drawdown_triggers': 0,
        'timeout_triggers': 0,
        'stop_loss_triggers': 0,
        'created_pairs': [],
        'quality_scores': []
    }

    for line in lines:
        # 统计PairDrawdownRule触发
        if 'PAIR DRAWDOWN' in line or 'PairDrawdownRule' in line:
            stats['pair_drawdown_triggers'] += 1

        # 统计超时触发
        if 'TIMEOUT' in line and '平仓' in line:
            stats['timeout_triggers'] += 1

        # 统计止损触发
        if 'STOP_LOSS' in line or '止损' in line:
            stats['stop_loss_triggers'] += 1

        # 提取质量分数
        if '质量分数:' in line:
            try:
                score = float(line.split('质量分数:')[1].split(',')[0])
                stats['quality_scores'].append(score)
            except:
                pass

        # 提取配对创建信息
        if '创建新配对' in line or '新配对:' in line:
            stats['created_pairs'].append(line.strip())

    if stats['quality_scores']:
        stats['avg_quality_score'] = np.mean(stats['quality_scores'])
        stats['min_quality_score'] = min(stats['quality_scores'])
        stats['max_quality_score'] = max(stats['quality_scores'])
        stats['scores_above_0.4'] = sum(1 for s in stats['quality_scores'] if s > 0.4)
        stats['scores_0.4_to_0.5'] = sum(1 for s in stats['quality_scores'] if 0.4 < s <= 0.5)

    return stats

# 执行分析
print("="*80)
print("三个版本回测对比分析")
print("="*80)

results = {}
for name, files in backtests.items():
    print(f"\n分析 {name}...")
    results[name] = {
        'params': files['params'],
        'trades': analyze_trades(files['trades']),
        'performance': analyze_json(files['json']),
        'logs': analyze_logs(files['logs'])
    }

# 输出对比表格
print("\n" + "="*80)
print("1. 参数对比")
print("="*80)
print(f"{'版本':<35} {'Sigma':<10} {'Max Days':<15} {'评分上界':<15}")
print("-"*80)
for name, data in results.items():
    params = data['params']
    threshold = params.get('zero_score_threshold', params.get('max_acceptable_days', ''))
    print(f"{name:<35} {params.get('sigma', ''):<10} {params.get('max_days', ''):<15} {threshold:<15}")

print("\n" + "="*80)
print("2. 交易数量对比")
print("="*80)
print(f"{'版本':<35} {'总交易数':<12} {'独特配对':<12} {'开仓':<10} {'平仓':<10}")
print("-"*80)
for name, data in results.items():
    t = data['trades']
    print(f"{name:<35} {t['total_trades']:<12} {t['unique_pairs']:<12} {t['open_trades']:<10} {t['close_trades']:<10}")

print("\n" + "="*80)
print("3. 平仓原因分析")
print("="*80)
for name, data in results.items():
    print(f"\n{name}:")
    reasons = data['trades'].get('close_reasons', {})
    for reason, count in sorted(reasons.items(), key=lambda x: x[1], reverse=True):
        print(f"  {reason}: {count} 次")

print("\n" + "="*80)
print("4. 持仓时长分析")
print("="*80)
print(f"{'版本':<35} {'平均(天)':<12} {'最短(天)':<10} {'最长(天)':<10} {'>30天':<10} {'>45天':<10} {'超时平仓':<10}")
print("-"*80)
for name, data in results.items():
    t = data['trades']
    print(f"{name:<35} {t.get('avg_holding_days', 0):<12.1f} {t.get('min_holding_days', 0):<10} "
          f"{t.get('max_holding_days', 0):<10} {t.get('trades_over_30_days', 0):<10} "
          f"{t.get('trades_over_45_days', 0):<10} {t.get('timeout_trades', 0):<10}")

print("\n" + "="*80)
print("5. 持仓时长分布")
print("="*80)
for name, data in results.items():
    print(f"\n{name}:")
    dist = data['trades'].get('holding_distribution', {})
    for bucket in ['0-7d', '8-14d', '15-21d', '22-30d', '31-45d', '45d+']:
        count = dist.get(bucket, 0)
        print(f"  {bucket}: {count} 次")

print("\n" + "="*80)
print("6. 整体表现对比")
print("="*80)
print(f"{'版本':<35} {'总收益%':<12} {'年化收益%':<12} {'夏普比':<10} {'最大回撤%':<12} {'胜率%':<10}")
print("-"*80)
for name, data in results.items():
    p = data['performance']
    print(f"{name:<35} {p['total_return']:<12.2f} {p['annual_return']:<12.2f} "
          f"{p['sharpe_ratio']:<10.3f} {p['max_drawdown']:<12.2f} {p['win_rate']:<10.1f}")

print("\n" + "="*80)
print("7. 质量分数分析")
print("="*80)
print(f"{'版本':<35} {'平均分':<10} {'最低分':<10} {'最高分':<10} {'>0.4数量':<12} {'0.4-0.5数量':<12}")
print("-"*80)
for name, data in results.items():
    l = data['logs']
    avg = l.get('avg_quality_score', 0)
    min_s = l.get('min_quality_score', 0)
    max_s = l.get('max_quality_score', 0)
    above = l.get('scores_above_0.4', 0)
    mid = l.get('scores_0.4_to_0.5', 0)
    print(f"{name:<35} {avg:<10.3f} {min_s:<10.3f} {max_s:<10.3f} {above:<12} {mid:<12}")

print("\n" + "="*80)
print("8. 风控触发统计")
print("="*80)
print(f"{'版本':<35} {'PairDrawdown':<15} {'Timeout':<12} {'StopLoss':<12}")
print("-"*80)
for name, data in results.items():
    l = data['logs']
    print(f"{name:<35} {l['pair_drawdown_triggers']:<15} {l['timeout_triggers']:<12} {l['stop_loss_triggers']:<12}")

# 计算变化率
print("\n" + "="*80)
print("9. 版本间变化分析 (相对于v7.2.8)")
print("="*80)

base = 'Smooth Brown Pig (v7.2.8)'
for name in ['Swimming Asparagus Lion (v7.2.9)', 'Virtual Fluorescent Yellow Dogfish (v7.2.10)']:
    print(f"\n{name} vs {base}:")

    # 交易数量变化
    base_trades = results[base]['trades']['total_trades']
    curr_trades = results[name]['trades']['total_trades']
    trade_change = (curr_trades - base_trades) / base_trades * 100
    print(f"  交易数量: {base_trades} → {curr_trades} ({trade_change:+.1f}%)")

    # 平均持仓时长变化
    base_holding = results[base]['trades'].get('avg_holding_days', 0)
    curr_holding = results[name]['trades'].get('avg_holding_days', 0)
    holding_change = curr_holding - base_holding
    print(f"  平均持仓: {base_holding:.1f}天 → {curr_holding:.1f}天 ({holding_change:+.1f}天)")

    # 收益变化
    base_return = results[base]['performance']['total_return']
    curr_return = results[name]['performance']['total_return']
    return_change = curr_return - base_return
    print(f"  总收益: {base_return:.2f}% → {curr_return:.2f}% ({return_change:+.2f}pp)")

    # 夏普比率变化
    base_sharpe = results[base]['performance']['sharpe_ratio']
    curr_sharpe = results[name]['performance']['sharpe_ratio']
    sharpe_change = curr_sharpe - base_sharpe
    print(f"  夏普比率: {base_sharpe:.3f} → {curr_sharpe:.3f} ({sharpe_change:+.3f})")