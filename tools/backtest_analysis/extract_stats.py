import json
import os

# 获取backtests目录路径 (相对于此脚本的位置)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BACKTESTS_DIR = os.path.join(SCRIPT_DIR, '..', '..', 'backtests')

# 回测文件 (使用相对路径)
files = {
    'Pig (v7.2.8)': os.path.join(BACKTESTS_DIR, 'Smooth Brown Pig.json'),
    'Lion (v7.2.9)': os.path.join(BACKTESTS_DIR, 'Swimming Asparagus Lion.json'),
    'Dogfish (v7.2.10)': os.path.join(BACKTESTS_DIR, 'Virtual Fluorescent Yellow Dogfish.json')
}

def extract_stats(filename):
    """提取JSON中的统计数据"""
    with open(filename, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # 查找Statistics字段
    if 'Statistics' in data:
        stats = data['Statistics']
        return {
            'Total Return': stats.get('Total Net Profit', 'N/A'),
            'Sharpe Ratio': stats.get('Sharpe Ratio', 'N/A'),
            'Annual Return': stats.get('Annual Return', 'N/A'),
            'Max Drawdown': stats.get('Drawdown', 'N/A'),
            'Win Rate': stats.get('Win Rate', 'N/A'),
            'Total Trades': stats.get('Total Trades', 'N/A')
        }

    # 查找其他可能的位置
    for key in data:
        if 'Statistics' in str(key) or 'statistics' in str(key):
            print(f"Found statistics in key: {key}")

    # 检查rollingWindow中的最后一个月
    if 'rollingWindow' in data:
        months = sorted(data['rollingWindow'].keys())
        if months:
            last_month = months[-1]
            portfolio_stats = data['rollingWindow'][last_month].get('portfolioStatistics', {})
            trade_stats = data['rollingWindow'][last_month].get('tradeStatistics', {})

            return {
                'Total Return': f"{float(portfolio_stats.get('totalNetProfit', 0)) * 100:.2f}%",
                'Sharpe Ratio': portfolio_stats.get('sharpeRatio', 'N/A'),
                'Annual Return': f"{float(portfolio_stats.get('compoundingAnnualReturn', 0)) * 100:.2f}%",
                'Max Drawdown': f"{float(portfolio_stats.get('drawdown', 0)) * 100:.2f}%",
                'Win Rate': f"{float(portfolio_stats.get('winRate', 0)) * 100:.2f}%",
                'Total Trades': trade_stats.get('totalNumberOfTrades', 'N/A')
            }

    return {}

print("="*80)
print("整体表现指标对比")
print("="*80)

all_stats = {}
for name, filename in files.items():
    print(f"\n{name}:")
    stats = extract_stats(filename)
    all_stats[name] = stats
    for metric, value in stats.items():
        print(f"  {metric}: {value}")

# 制作对比表
print("\n" + "="*80)
print("关键指标对比表")
print("="*80)
print(f"{'指标':<20} {'Pig (v7.2.8)':<20} {'Lion (v7.2.9)':<20} {'Dogfish (v7.2.10)':<20}")
print("-"*80)

metrics = ['Total Return', 'Annual Return', 'Sharpe Ratio', 'Max Drawdown', 'Win Rate', 'Total Trades']
for metric in metrics:
    pig = all_stats['Pig (v7.2.8)'].get(metric, 'N/A')
    lion = all_stats['Lion (v7.2.9)'].get(metric, 'N/A')
    dog = all_stats['Dogfish (v7.2.10)'].get(metric, 'N/A')
    print(f"{metric:<20} {pig:<20} {lion:<20} {dog:<20}")