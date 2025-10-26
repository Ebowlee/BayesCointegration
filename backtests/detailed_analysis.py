import json
import re

# Top 10 losers with line numbers
losers = [
    (1124, "('CRM', 'FIS')", -1932.75, 19, "PAIR DRAWDOWN"),
    (450, "('CTRA', 'SLB')", -1730.45, 33, "PAIR DRAWDOWN"),
    (329, "('CNP', 'D')", -1321.22, 22, "PAIR DRAWDOWN"),
    (1116, "('AMAT', 'NVDA')", -814.29, 31, "STOP_LOSS"),
    (439, "('OXY', 'WMB')", -658.40, 28, "STOP_LOSS"),
    (759, "('KMI', 'SLB')", -640.82, 8, "PAIR DRAWDOWN"),
    (1352, "('CTRA', 'OXY')", -587.46, 28, "STOP_LOSS"),
    (1322, "('KDP', 'MNST')", -556.67, 33, "PAIR DRAWDOWN"),
    (511, "('EXC', 'NEE')", -542.47, 2, "STOP_LOSS"),
    (351, "('EW', 'MDT')", -534.24, 8, "STOP_LOSS")
]

# Read log file
with open(r'c:\Users\Lybst\OneDrive\桌面\MyLeanProject\BayesCointegration\backtests\Smooth Brown Pig_logs.txt', 'r', encoding='utf-8') as f:
    log_lines = f.readlines()

print("=" * 120)
print("Top 10 亏损配对详细分析")
print("=" * 120)

for rank, (close_line, pair_id, loss, days, reason) in enumerate(losers, 1):
    print(f"\n【{rank}. {pair_id}】")
    print(f"亏损: ${loss:.2f} | 持仓: {days}天 | 平仓原因: {reason}")
    print("-" * 120)
    
    # Extract close context (3 lines before and after)
    start = max(0, close_line - 4)
    end = min(len(log_lines), close_line + 3)
    
    print("\n平仓时上下文:")
    for i in range(start, end):
        print(f"  {log_lines[i].rstrip()}")
    
    # Find opening line
    open_line = None
    for i in range(close_line - 1, -1, -1):
        if f"TM注册] {pair_id} OPEN" in log_lines[i]:
            open_line = i
            break
    
    if open_line:
        print(f"\n开仓时上下文 (第{open_line+1}行):")
        open_start = max(0, open_line - 3)
        open_end = min(len(log_lines), open_line + 4)
        for i in range(open_start, open_end):
            print(f"  {log_lines[i].rstrip()}")

print("\n" + "=" * 120)
print("分析完成")
print("=" * 120)
