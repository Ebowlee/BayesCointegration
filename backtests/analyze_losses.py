import json
import re

# Read all trade_close entries
trades = []
with open(r'c:\Users\Lybst\OneDrive\桌面\MyLeanProject\BayesCointegration\backtests\temp_trade_close.txt', 'r', encoding='utf-8') as f:
    for line in f:
        if not line.strip():
            continue
        # Extract line number and JSON part using regex
        # Format: line_num:YYYY-MM-DD HH:MM:SS {json...}
        match = re.match(r'(\d+):.+?(\{.+\})', line)
        if match:
            line_num = int(match.group(1))
            json_str = match.group(2)
            trade_data = json.loads(json_str)
            trade_data['line_num'] = line_num
            trades.append(trade_data)

# Filter losing trades (pnl_pct < 0)
losing_trades = [t for t in trades if t['pnl_pct'] < 0]

# Sort by pnl_pct (ascending, most negative first)
losing_trades.sort(key=lambda x: x['pnl_pct'])

# Print top 10 losers
print(f'总交易数: {len(trades)}')
print(f'亏损交易数: {len(losing_trades)}')
print(f'盈利交易数: {len(trades) - len(losing_trades)}')
print(f'\nTop 10 亏损交易:')
print('-' * 100)
for i, trade in enumerate(losing_trades[:10], 1):
    print(f'{i}. {trade["pair_id"]:25s} 亏损: ${trade["pnl_pct"]:10.2f}  持仓: {trade["holding_days"]:2d}天  原因: {trade["reason"]:20s}  行号: {trade["line_num"]}')
