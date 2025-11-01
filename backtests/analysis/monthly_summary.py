"""
分析最新回测CSV数据 - Geeky Asparagus Coyote
重点验证v7.5.15 MonthStart修复效果
"""

import pandas as pd
from datetime import datetime
from collections import defaultdict

# 读取CSV
csv_path = r"c:\Users\Lybst\OneDrive\桌面\MyLeanProject\BayesCointegration\backtests\Geeky Asparagus Coyote_trades.csv"
df = pd.read_csv(csv_path)

# 解析时间
df['Time'] = pd.to_datetime(df['Time'])
df['Date'] = df['Time'].dt.date
df['Month'] = df['Time'].dt.to_period('M')

# 提取配对信息和操作类型
def parse_tag(tag):
    parts = tag.split('_')
    pair = parts[0].replace("('", "").replace("')", "").replace("', '", ",")
    action = parts[1]  # OPEN or CLOSE
    reason = parts[2] if len(parts) > 2 else 'N/A'
    return pair, action, reason

df['Pair'], df['Action'], df['Reason'] = zip(*df['Tag'].apply(parse_tag))

print("=" * 80)
print("贝叶斯协整策略回测分析 - Geeky Asparagus Coyote")
print("=" * 80)
print(f"回测期间: {df['Time'].min()} 至 {df['Time'].max()}")
print(f"总交易笔数: {len(df)} 笔\n")

# ============================================================
# 1. MonthStart修复验证 (v7.5.15关键目标)
# ============================================================
print("\n" + "=" * 80)
print("1. MonthStart修复验证 - 月度交易分布")
print("=" * 80)

monthly_stats = df.groupby('Month').agg({
    'Action': 'count'
}).rename(columns={'Action': 'Total_Trades'})

# 分离OPEN和CLOSE
monthly_open = df[df['Action'] == 'OPEN'].groupby('Month').size()
monthly_close = df[df['Action'] == 'CLOSE'].groupby('Month').size()

monthly_stats['OPEN'] = monthly_open
monthly_stats['CLOSE'] = monthly_close
monthly_stats = monthly_stats.fillna(0).astype(int)

print("\n月度交易统计:")
print(monthly_stats.to_string())

# 重点分析2024年7月和9月
print("\n[v7.5.15修复验证]:")
july_2024 = monthly_stats.loc[monthly_stats.index == '2024-07'] if '2024-07' in monthly_stats.index else None
sept_2024 = monthly_stats.loc[monthly_stats.index == '2024-09'] if '2024-09' in monthly_stats.index else None

if july_2024 is not None and len(july_2024) > 0:
    july_trades = july_2024['Total_Trades'].values[0]
    print(f"- 2024年7月交易: {july_trades} 笔 {'[PASS] 修复生效' if july_trades > 0 else '[FAIL] 仍然失效'}")
else:
    print(f"- 2024年7月交易: 0 笔 [FAIL] 调度失效")

if sept_2024 is not None and len(sept_2024) > 0:
    sept_trades = sept_2024['Total_Trades'].values[0]
    print(f"- 2024年9月交易: {sept_trades} 笔 {'[PASS] 修复生效' if sept_trades > 0 else '[FAIL] 仍然失效'}")
else:
    print(f"- 2024年9月交易: 0 笔 [FAIL] 调度失效")

# ============================================================
# 2. 平仓原因分析
# ============================================================
print("\n" + "=" * 80)
print("2. 平仓原因分布")
print("=" * 80)

close_df = df[df['Action'] == 'CLOSE']
reason_stats = close_df['Reason'].value_counts()

print("\n平仓原因统计:")
for reason, count in reason_stats.items():
    pct = count / len(close_df) * 100
    print(f"  {reason:20s}: {count:3d} 笔 ({pct:5.1f}%)")

# ============================================================
# 3. 配对表现分析
# ============================================================
print("\n" + "=" * 80)
print("3. 配对表现分析")
print("=" * 80)

# 配对交易次数
pair_trades = df.groupby('Pair')['Action'].apply(lambda x: (x == 'OPEN').sum())
pair_trades = pair_trades.sort_values(ascending=False)

print(f"\n共交易 {len(pair_trades)} 个不同配对")
print("\n配对交易次数TOP 10:")
for pair, count in pair_trades.head(10).items():
    print(f"  {pair:20s}: {count} 次")

# 计算配对PnL (简化版: 按symbol汇总)
print("\n配对级别PnL分析 (基于订单Value):")
pair_pnl = defaultdict(float)

for pair in df['Pair'].unique():
    pair_df = df[df['Pair'] == pair]
    # Value列已经包含正负号(买入正,卖出负)
    total_value = pair_df['Value'].sum()
    pair_pnl[pair] = total_value

# 按PnL排序
sorted_pairs = sorted(pair_pnl.items(), key=lambda x: x[1])

print("\n亏损最严重的配对TOP 10:")
for pair, pnl in sorted_pairs[:10]:
    trades_count = pair_trades.get(pair, 0)
    print(f"  {pair:20s}: ${pnl:12.2f} ({trades_count}次交易)")

print("\n盈利最高的配对TOP 10:")
for pair, pnl in sorted_pairs[-10:]:
    trades_count = pair_trades.get(pair, 0)
    print(f"  {pair:20s}: ${pnl:12.2f} ({trades_count}次交易)")

# ============================================================
# 4. 股票级别分析 (识别"问题股票")
# ============================================================
print("\n" + "=" * 80)
print("4. 股票级别分析 - 识别高频亏损股票")
print("=" * 80)

# 从配对中提取单个股票
symbol_pnl = defaultdict(float)
symbol_trades = defaultdict(int)

for index, row in df.iterrows():
    # 解析symbol (从Tag中提取)
    if 'Symbol' in df.columns:
        symbol = row['Symbol']
        value = row['Value']
        symbol_pnl[symbol] += value
        symbol_trades[symbol] += 1

# 按PnL排序
sorted_symbols = sorted(symbol_pnl.items(), key=lambda x: x[1])

print("\n参与交易次数最多的股票TOP 10:")
top_symbols = sorted(symbol_trades.items(), key=lambda x: x[1], reverse=True)[:10]
for symbol, count in top_symbols:
    pnl = symbol_pnl[symbol]
    print(f"  {symbol:6s}: {count:3d} 次交易, PnL=${pnl:12.2f}")

print("\n累计亏损最严重的股票TOP 10:")
for symbol, pnl in sorted_symbols[:10]:
    trades = symbol_trades[symbol]
    print(f"  {symbol:6s}: ${pnl:12.2f} ({trades}次交易)")

# ============================================================
# 5. 持仓时长分析
# ============================================================
print("\n" + "=" * 80)
print("5. 持仓时长分析")
print("=" * 80)

# 匹配OPEN-CLOSE配对
pair_durations = []
open_records = {}

for index, row in df.iterrows():
    pair_id = row['Pair']
    action = row['Action']
    time = row['Time']

    if action == 'OPEN':
        open_records[pair_id] = time
    elif action == 'CLOSE' and pair_id in open_records:
        open_time = open_records[pair_id]
        duration = (time - open_time).days
        pair_durations.append({
            'Pair': pair_id,
            'Duration': duration,
            'Reason': row['Reason']
        })
        del open_records[pair_id]

if pair_durations:
    duration_df = pd.DataFrame(pair_durations)

    print(f"\n平均持仓时长: {duration_df['Duration'].mean():.1f} 天")
    print(f"最短持仓: {duration_df['Duration'].min()} 天")
    print(f"最长持仓: {duration_df['Duration'].max()} 天")

    # 持仓时长分布
    print("\n持仓时长分布:")
    bins = [0, 7, 14, 30, 50, 100]
    labels = ['0-7天', '8-14天', '15-30天', '31-50天', '50天+']
    duration_df['Bucket'] = pd.cut(duration_df['Duration'], bins=bins, labels=labels)
    bucket_stats = duration_df['Bucket'].value_counts().sort_index()

    for bucket, count in bucket_stats.items():
        pct = count / len(duration_df) * 100
        print(f"  {bucket:10s}: {count:3d} 个配对 ({pct:5.1f}%)")

    # 按平仓原因分组的持仓时长
    print("\n按平仓原因的平均持仓时长:")
    reason_duration = duration_df.groupby('Reason')['Duration'].mean().sort_values(ascending=False)
    for reason, avg_days in reason_duration.items():
        count = (duration_df['Reason'] == reason).sum()
        print(f"  {reason:20s}: {avg_days:5.1f} 天 (n={count})")

# ============================================================
# 6. 异常检测
# ============================================================
print("\n" + "=" * 80)
print("6. 异常检测")
print("=" * 80)

# 检测未平仓配对
if open_records:
    print(f"\n[WARNING] 发现 {len(open_records)} 个未平仓配对:")
    for pair, open_time in open_records.items():
        print(f"  - {pair}: 开仓于 {open_time}")
else:
    print("\n[PASS] 所有配对均已正常平仓")

# 检测异常短持仓
if pair_durations:
    short_positions = [d for d in pair_durations if d['Duration'] < 7]
    if short_positions:
        print(f"\n[WARNING] 发现 {len(short_positions)} 个异常短持仓(<7天):")
        for pos in short_positions[:5]:
            print(f"  - {pos['Pair']}: {pos['Duration']}天, 原因={pos['Reason']}")

# ============================================================
# 7. 总结
# ============================================================
print("\n" + "=" * 80)
print("7. 关键发现总结")
print("=" * 80)

total_pnl = sum(pair_pnl.values())
winning_pairs = sum(1 for pnl in pair_pnl.values() if pnl > 0)
losing_pairs = sum(1 for pnl in pair_pnl.values() if pnl < 0)

print(f"\n整体表现:")
print(f"  - 总PnL: ${total_pnl:,.2f}")
print(f"  - 盈利配对: {winning_pairs}/{len(pair_pnl)} ({winning_pairs/len(pair_pnl)*100:.1f}%)")
print(f"  - 亏损配对: {losing_pairs}/{len(pair_pnl)} ({losing_pairs/len(pair_pnl)*100:.1f}%)")

print("\n" + "=" * 80)
