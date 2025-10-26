import pandas as pd
import re
from datetime import datetime
import os

# 获取backtests目录路径 (相对于此脚本的位置)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BACKTESTS_DIR = os.path.join(SCRIPT_DIR, '..', '..', 'backtests')

# 读取三个版本的交易数据 (使用相对路径)
files = {
    'Pig (v7.2.8)': os.path.join(BACKTESTS_DIR, 'Smooth Brown Pig_trades.csv'),
    'Lion (v7.2.9)': os.path.join(BACKTESTS_DIR, 'Swimming Asparagus Lion_trades.csv'),
    'Dogfish (v7.2.10)': os.path.join(BACKTESTS_DIR, 'Virtual Fluorescent Yellow Dogfish_trades.csv')
}

def analyze_trade_file(filename):
    """分析单个交易文件"""
    df = pd.read_csv(filename)

    # 解析Tag提取交易信息
    pattern = r"\('([^']+)',\s*'([^']+)'\)_([^_]+)_(.+)_\d+"

    trades = []
    for _, row in df.iterrows():
        tag = row['Tag']
        match = re.match(pattern, tag)
        if match:
            symbol1, symbol2, action, info = match.groups()
            pair_id = f"({symbol1}, {symbol2})"

            # 处理开仓和平仓
            if action == 'OPEN':
                reason = 'OPEN'
            elif action == 'CLOSE':
                reason = info  # CLOSE, STOP_LOSS, TIMEOUT, PAIR DRAWDOWN等
            else:
                continue

            trades.append({
                'Time': pd.to_datetime(row['Time']),
                'Symbol': row['Symbol'],
                'Pair': pair_id,
                'Action': action,
                'Reason': reason,
                'Quantity': row['Quantity'],
                'Price': row['Price'],
                'Value': row['Value']
            })

    trade_df = pd.DataFrame(trades)

    # 配对统计
    pair_trades = []
    for pair_id in trade_df['Pair'].unique():
        pair_data = trade_df[trade_df['Pair'] == pair_id]

        # 分开开仓和平仓
        opens = pair_data[pair_data['Action'] == 'OPEN']
        closes = pair_data[pair_data['Action'] == 'CLOSE']

        # 匹配开仓和平仓
        open_times = opens.groupby('Time').first()
        close_times = closes.groupby('Time').first()

        for open_time in open_times.index:
            # 找到下一个平仓时间
            future_closes = close_times[close_times.index > open_time]
            if len(future_closes) > 0:
                close_time = future_closes.index[0]
                close_reason = future_closes.iloc[0]['Reason']
                holding_days = (close_time - open_time).days

                pair_trades.append({
                    'Pair': pair_id,
                    'OpenTime': open_time,
                    'CloseTime': close_time,
                    'HoldingDays': holding_days,
                    'CloseReason': close_reason
                })

    return pd.DataFrame(pair_trades)

# 分析所有文件
results = {}
for name, filename in files.items():
    results[name] = analyze_trade_file(filename)

# 详细统计
print("="*80)
print("详细交易分析报告")
print("="*80)

# 1. 平仓原因详细统计
print("\n1. 平仓原因详细统计")
print("-"*80)
for name, df in results.items():
    print(f"\n{name}:")
    reason_stats = df['CloseReason'].value_counts()
    total = len(df)
    for reason, count in reason_stats.items():
        pct = count / total * 100
        print(f"  {reason:<20} {count:3d} 次 ({pct:5.1f}%)")

# 2. 持仓时长分布（按平仓原因）
print("\n" + "="*80)
print("2. 持仓时长分布（按平仓原因）")
print("-"*80)
for name, df in results.items():
    print(f"\n{name}:")
    for reason in df['CloseReason'].unique():
        reason_df = df[df['CloseReason'] == reason]
        avg_days = reason_df['HoldingDays'].mean()
        max_days = reason_df['HoldingDays'].max()
        min_days = reason_df['HoldingDays'].min()
        count = len(reason_df)
        print(f"  {reason:<20} 平均{avg_days:5.1f}天, 最长{max_days:3d}天, 最短{min_days:2d}天 (n={count})")

# 3. 超过30天的交易分析
print("\n" + "="*80)
print("3. 超过30天的持仓分析")
print("-"*80)
for name, df in results.items():
    over_30 = df[df['HoldingDays'] > 30]
    print(f"\n{name}: {len(over_30)}个交易超过30天")
    if len(over_30) > 0:
        # 按平仓原因分组
        by_reason = over_30['CloseReason'].value_counts()
        for reason, count in by_reason.items():
            pct = count / len(over_30) * 100
            print(f"  {reason:<20} {count:2d} 次 ({pct:5.1f}%)")

        # 具体的配对
        print("  具体配对:")
        for _, row in over_30.head(5).iterrows():
            print(f"    {row['Pair']}: {row['HoldingDays']}天 ({row['CloseReason']})")

# 4. PAIR DRAWDOWN触发分析
print("\n" + "="*80)
print("4. PAIR DRAWDOWN触发分析 (v7.2.10新增规则)")
print("-"*80)
for name, df in results.items():
    drawdown_trades = df[df['CloseReason'] == 'PAIR DRAWDOWN']
    print(f"\n{name}: {len(drawdown_trades)}个PAIR DRAWDOWN触发")
    if len(drawdown_trades) > 0:
        avg_days = drawdown_trades['HoldingDays'].mean()
        print(f"  平均持仓时长: {avg_days:.1f}天")
        print("  触发的配对:")
        for _, row in drawdown_trades.iterrows():
            print(f"    {row['Pair']}: 持仓{row['HoldingDays']}天, {row['CloseTime'].strftime('%Y-%m-%d')}")

# 5. TIMEOUT触发分析
print("\n" + "="*80)
print("5. TIMEOUT触发分析 (max_days参数影响)")
print("-"*80)
for name, df in results.items():
    timeout_trades = df[df['CloseReason'] == 'TIMEOUT']
    print(f"\n{name}: {len(timeout_trades)}个TIMEOUT触发")
    if len(timeout_trades) > 0:
        avg_days = timeout_trades['HoldingDays'].mean()
        min_days = timeout_trades['HoldingDays'].min()
        max_days = timeout_trades['HoldingDays'].max()
        print(f"  持仓天数: 平均{avg_days:.1f}天, 最短{min_days}天, 最长{max_days}天")
        print("  触发的配对:")
        for _, row in timeout_trades.iterrows():
            print(f"    {row['Pair']}: 持仓{row['HoldingDays']}天")

# 6. 止损(STOP_LOSS)分析
print("\n" + "="*80)
print("6. 止损触发分析 (sigma参数影响)")
print("-"*80)
for name, df in results.items():
    stop_trades = df[df['CloseReason'] == 'STOP']
    print(f"\n{name}: {len(stop_trades)}个STOP_LOSS触发")
    if len(stop_trades) > 0:
        avg_days = stop_trades['HoldingDays'].mean()
        print(f"  平均持仓时长: {avg_days:.1f}天")
        # 持仓时长分布
        bins = [0, 3, 7, 14, 30, 100]
        labels = ['0-3天', '4-7天', '8-14天', '15-30天', '30天+']
        stop_trades['bucket'] = pd.cut(stop_trades['HoldingDays'], bins=bins, labels=labels)
        dist = stop_trades['bucket'].value_counts().sort_index()
        for bucket, count in dist.items():
            pct = count / len(stop_trades) * 100
            print(f"    {bucket}: {count}次 ({pct:.1f}%)")

# 7. 正常平仓(CLOSE)分析
print("\n" + "="*80)
print("7. 正常平仓分析")
print("-"*80)
for name, df in results.items():
    close_trades = df[df['CloseReason'] == 'CLOSE']
    print(f"\n{name}: {len(close_trades)}个正常平仓")
    if len(close_trades) > 0:
        avg_days = close_trades['HoldingDays'].mean()
        median_days = close_trades['HoldingDays'].median()
        print(f"  持仓时长: 平均{avg_days:.1f}天, 中位数{median_days:.1f}天")

        # 持仓时长分布
        bins = [0, 3, 7, 14, 30, 100]
        labels = ['0-3天', '4-7天', '8-14天', '15-30天', '30天+']
        close_trades['bucket'] = pd.cut(close_trades['HoldingDays'], bins=bins, labels=labels)
        dist = close_trades['bucket'].value_counts().sort_index()
        for bucket, count in dist.items():
            pct = count / len(close_trades) * 100
            print(f"    {bucket}: {count}次 ({pct:.1f}%)")

# 8. 总结对比
print("\n" + "="*80)
print("8. 关键指标对比总结")
print("-"*80)
print(f"{'指标':<30} {'Pig(v7.2.8)':<15} {'Lion(v7.2.9)':<15} {'Dogfish(v7.2.10)':<15}")
print("-"*80)

# 计算各指标
metrics = {}
for name, df in results.items():
    metrics[name] = {
        '总配对数': len(df),
        'PAIR_DRAWDOWN次数': len(df[df['CloseReason'] == 'PAIR DRAWDOWN']),
        'TIMEOUT次数': len(df[df['CloseReason'] == 'TIMEOUT']),
        'STOP_LOSS次数': len(df[df['CloseReason'].str.contains('STOP', na=False)]),
        'CLOSE次数': len(df[df['CloseReason'] == 'CLOSE']),
        '平均持仓天数': df['HoldingDays'].mean(),
        '超30天交易数': len(df[df['HoldingDays'] > 30]),
        '超45天交易数': len(df[df['HoldingDays'] > 45])
    }

# 输出对比表
for metric in ['总配对数', 'PAIR_DRAWDOWN次数', 'TIMEOUT次数', 'STOP_LOSS次数',
               'CLOSE次数', '平均持仓天数', '超30天交易数', '超45天交易数']:
    pig = metrics['Pig (v7.2.8)'].get(metric, 0)
    lion = metrics['Lion (v7.2.9)'].get(metric, 0)
    dog = metrics['Dogfish (v7.2.10)'].get(metric, 0)

    if isinstance(pig, float):
        print(f"{metric:<30} {pig:<15.1f} {lion:<15.1f} {dog:<15.1f}")
    else:
        print(f"{metric:<30} {pig:<15} {lion:<15} {dog:<15}")

# 变化率分析
print("\n变化率分析 (相对于v7.2.8):")
print("-"*80)
base = metrics['Pig (v7.2.8)']
for version in ['Lion (v7.2.9)', 'Dogfish (v7.2.10)']:
    print(f"\n{version}:")
    curr = metrics[version]

    # TIMEOUT变化
    timeout_change = curr['TIMEOUT次数'] - base['TIMEOUT次数']
    print(f"  TIMEOUT: {base['TIMEOUT次数']} → {curr['TIMEOUT次数']} ({timeout_change:+d})")

    # PAIR_DRAWDOWN变化
    pd_change = curr['PAIR_DRAWDOWN次数'] - base['PAIR_DRAWDOWN次数']
    print(f"  PAIR_DRAWDOWN: {base['PAIR_DRAWDOWN次数']} → {curr['PAIR_DRAWDOWN次数']} ({pd_change:+d})")

    # 平均持仓变化
    hold_change = curr['平均持仓天数'] - base['平均持仓天数']
    print(f"  平均持仓: {base['平均持仓天数']:.1f}天 → {curr['平均持仓天数']:.1f}天 ({hold_change:+.1f}天)")

    # 超时交易变化
    over30_change = curr['超30天交易数'] - base['超30天交易数']
    print(f"  超30天: {base['超30天交易数']} → {curr['超30天交易数']} ({over30_change:+d})")