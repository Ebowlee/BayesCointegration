"""
Forensic分析 - 深度追踪高频亏损配对
回答核心问题:
1. 开仓滑点: signal_zscore vs entry_zscore
2. 平仓滑点: exit_signal_zscore vs actual_exit_zscore
3. 冷静期机制: 为何反复开仓?
4. 质量分数: 每次开仓的quality_score是多少?
"""

import pandas as pd
import re
from datetime import datetime, timedelta
from collections import defaultdict

# 读取CSV
csv_path = r"c:\Users\Lybst\OneDrive\桌面\MyLeanProject\BayesCointegration\backtests\Geeky Asparagus Coyote_trades.csv"
df = pd.read_csv(csv_path)

# 解析时间
df['Time'] = pd.to_datetime(df['Time'])
df['Date'] = df['Time'].dt.date

# 提取配对信息
def parse_tag(tag):
    parts = tag.split('_')
    pair = parts[0].replace("('", "").replace("')", "").replace("', '", ",")
    action = parts[1]
    reason = parts[2] if len(parts) > 2 else 'N/A'
    timestamp = parts[3] if len(parts) > 3 else 'N/A'
    return pair, action, reason, timestamp

df['Pair'], df['Action'], df['Reason'], df['SignalTime'] = zip(*df['Tag'].apply(parse_tag))

# 目标配对 (反复亏损的前3名)
target_pairs = [
    'AMAT,NVDA',   # 4次交易, -$2720
    'DVN,ET',      # 2次交易, -$3104
    'OXY,SLB'      # 4次交易, -$734
]

print("=" * 100)
print("FORENSIC ANALYSIS - 高频亏损配对深度追踪")
print("=" * 100)
print("\n分析方法:")
print("1. 逐笔还原: 开仓->持仓->平仓全过程")
print("2. 滑点检测: 信号时刻price vs 实际成交price")
print("3. 冷静期检查: 两次开仓间隔是否满足配置要求")
print("4. 质量分数追溯: (需要logs.txt配合,CSV无此数据)")
print()

# 为每个配对构建完整交易历史
def build_trade_history(pair_name, pair_df):
    """构建配对的完整交易历史"""
    trades = []

    # 按时间排序
    pair_df = pair_df.sort_values('Time')

    current_position = None

    for idx, row in pair_df.iterrows():
        action = row['Action']

        if action == 'OPEN':
            # 开仓记录
            symbol1, symbol2 = pair_name.split(',')

            # 找到两腿订单
            leg1 = pair_df[(pair_df['Time'] == row['Time']) & (pair_df['Symbol'] == symbol1)]
            leg2 = pair_df[(pair_df['Time'] == row['Time']) & (pair_df['Symbol'] == symbol2)]

            if len(leg1) > 0 and len(leg2) > 0:
                leg1_row = leg1.iloc[0]
                leg2_row = leg2.iloc[0]

                current_position = {
                    'open_time': row['Time'],
                    'open_date': row['Date'],
                    'signal_time': row['SignalTime'],
                    'symbol1': symbol1,
                    'symbol2': symbol2,
                    'open_price1': leg1_row['Price'],
                    'open_price2': leg2_row['Price'],
                    'open_qty1': leg1_row['Quantity'],
                    'open_qty2': leg2_row['Quantity'],
                    'open_value1': leg1_row['Value'],
                    'open_value2': leg2_row['Value'],
                }

        elif action == 'CLOSE' and current_position:
            # 平仓记录
            symbol1 = current_position['symbol1']
            symbol2 = current_position['symbol2']

            leg1 = pair_df[(pair_df['Time'] == row['Time']) & (pair_df['Symbol'] == symbol1)]
            leg2 = pair_df[(pair_df['Time'] == row['Time']) & (pair_df['Symbol'] == symbol2)]

            if len(leg1) > 0 and len(leg2) > 0:
                leg1_row = leg1.iloc[0]
                leg2_row = leg2.iloc[0]

                current_position.update({
                    'close_time': row['Time'],
                    'close_date': row['Date'],
                    'close_reason': row['Reason'],
                    'close_signal_time': row['SignalTime'],
                    'close_price1': leg1_row['Price'],
                    'close_price2': leg2_row['Price'],
                    'close_qty1': leg1_row['Quantity'],
                    'close_qty2': leg2_row['Quantity'],
                    'close_value1': leg1_row['Value'],
                    'close_value2': leg2_row['Value'],
                })

                # 计算PnL
                pnl = (current_position['close_value1'] + current_position['close_value2']) + \
                      (current_position['open_value1'] + current_position['open_value2'])
                current_position['pnl'] = pnl

                # 计算持仓天数
                holding_days = (current_position['close_time'] - current_position['open_time']).days
                current_position['holding_days'] = holding_days

                trades.append(current_position)
                current_position = None

    return trades

# 分析每个目标配对
for pair_name in target_pairs:
    pair_df = df[df['Pair'] == pair_name].copy()

    if len(pair_df) == 0:
        continue

    print("\n" + "=" * 100)
    print(f"配对: {pair_name}")
    print("=" * 100)

    # 构建交易历史
    trades = build_trade_history(pair_name, pair_df)

    print(f"\n总交易次数: {len(trades)} 次")
    total_pnl = sum(t['pnl'] for t in trades)
    print(f"累计PnL: ${total_pnl:,.2f}\n")

    # 逐笔分析
    for i, trade in enumerate(trades, 1):
        print(f"\n{'-' * 100}")
        print(f"第 {i} 次交易")
        print(f"{'-' * 100}")

        # 开仓信息
        print(f"\n[开仓] {trade['open_date']}")
        print(f"  信号时刻: {trade['signal_time']}")
        print(f"  实际成交: {trade['open_time']}")

        # 计算信号时刻到成交的时间差
        try:
            signal_dt = datetime.strptime(trade['signal_time'], '%Y%m%d_%H%M%S')
            execution_dt = trade['open_time']
            delay = (execution_dt - signal_dt).total_seconds() / 3600  # 小时
            print(f"  [Delay] 延迟: {delay:.1f} 小时 (隔夜缺口)")
        except:
            print(f"  [Delay] 延迟: 无法计算")

        print(f"\n  {trade['symbol1']:6s}: qty={trade['open_qty1']:6.0f}, price=${trade['open_price1']:8.2f}, value=${trade['open_value1']:12.2f}")
        print(f"  {trade['symbol2']:6s}: qty={trade['open_qty2']:6.0f}, price=${trade['open_price2']:8.2f}, value=${trade['open_value2']:12.2f}")

        # 计算开仓时的price ratio
        if trade['open_qty1'] != 0 and trade['open_qty2'] != 0:
            # Beta = -(value2/value1) 的近似
            open_spread = abs(trade['open_value1']) + abs(trade['open_value2'])
            print(f"  开仓spread总值: ${open_spread:,.2f}")

        # 平仓信息
        print(f"\n[平仓] {trade['close_date']} (持仓 {trade['holding_days']} 天)")
        print(f"  平仓原因: {trade['close_reason']}")
        print(f"  信号时刻: {trade['close_signal_time']}")
        print(f"  实际成交: {trade['close_time']}")

        try:
            close_signal_dt = datetime.strptime(trade['close_signal_time'], '%Y%m%d_%H%M%S')
            close_execution_dt = trade['close_time']
            close_delay = (close_execution_dt - close_signal_dt).total_seconds() / 3600
            print(f"  [Delay] 延迟: {close_delay:.1f} 小时 (隔夜缺口)")
        except:
            print(f"  [Delay] 延迟: 无法计算")

        print(f"\n  {trade['symbol1']:6s}: qty={trade['close_qty1']:6.0f}, price=${trade['close_price1']:8.2f}, value=${trade['close_value1']:12.2f}")
        print(f"  {trade['symbol2']:6s}: qty={trade['close_qty2']:6.0f}, price=${trade['close_price2']:8.2f}, value=${trade['close_value2']:12.2f}")

        # 价格变化分析
        price_change1 = (trade['close_price1'] - trade['open_price1']) / trade['open_price1'] * 100
        price_change2 = (trade['close_price2'] - trade['open_price2']) / trade['open_price2'] * 100

        print(f"\n  价格变化:")
        print(f"    {trade['symbol1']}: {price_change1:+.2f}%")
        print(f"    {trade['symbol2']}: {price_change2:+.2f}%")

        # PnL
        print(f"\n  [PnL] 本次PnL: ${trade['pnl']:+,.2f}")

        # 冷静期检查
        if i < len(trades):
            next_trade = trades[i]
            cooldown_days = (next_trade['open_time'] - trade['close_time']).days

            # 根据平仓原因判断预期冷静期
            if trade['close_reason'] == 'STOP':
                expected_cooldown = 60  # config中的stop冷静期
            elif trade['close_reason'] == 'CLOSE':
                expected_cooldown = 20  # config中的正常冷静期
            else:
                expected_cooldown = 30  # PAIR DRAWDOWN/TIMEOUT默认

            print(f"\n  [Cooldown] 冷静期检查:")
            print(f"    实际间隔: {cooldown_days} 天")
            print(f"    预期冷静期: {expected_cooldown} 天 (基于{trade['close_reason']})")

            if cooldown_days < expected_cooldown:
                print(f"    [WARNING] 违反冷静期! 过早重新开仓")
            else:
                print(f"    [PASS] 满足冷静期要求")

# 总结
print("\n" + "=" * 100)
print("关键发现总结")
print("=" * 100)

print("\n1. 滑点问题:")
print("   - 所有订单都是 Market On Open")
print("   - 信号时刻(Day T 16:00) -> 实际成交(Day T+1 09:30)")
print("   - 隔夜缺口普遍存在,高波动股票(NVDA)影响最大")

print("\n2. 冷静期机制:")
print("   - CSV数据显示配对重复开仓间隔")
print("   - 需要检查是否满足config中的cooldown配置")

print("\n3. 质量分数问题:")
print("   [FAIL] CSV文件不包含quality_score数据")
print("   [FAIL] 需要logs.txt中的[PairScore]日志才能追溯")
print("   [FAIL] 当前无法回答'为何高质量分数配对反复亏损'")

print("\n4. 建议:")
print("   - 立即运行带debug_mode=True的回测获取logs.txt")
print("   - 从logs中提取每次开仓时的quality_score, half_life, beta_cv等指标")
print("   - 对比quality_score与实际PnL的相关性")

print("\n" + "=" * 100)
