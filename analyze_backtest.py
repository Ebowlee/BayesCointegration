import json

file_path = r'C:\Users\Lybst\OneDrive\桌面\MyLeanProject\BayesCointegration\backtests\Logical Asparagus Beaver.json'

with open(file_path, 'r') as f:
    data = json.load(f)

# 提取总体性能统计
if 'totalPerformance' in data:
    tp = data['totalPerformance']
    ps = tp['portfolioStatistics']
    ts = tp['tradeStatistics']

    print('=== v6.6.2-baseline 回测性能指标 ===')
    print('回测期间: 2023-10-05 至 2024-09-18')
    print()

    print('【收益指标】')
    print(f"总收益率: {float(ps['totalNetProfit'])*100:.2f}%")
    print(f"年化收益率: {float(ps['compoundingAnnualReturn'])*100:.2f}%")
    print(f"起始资金: ${float(ps['startEquity']):,.2f}")
    print(f"结束资金: ${float(ps['endEquity']):,.2f}")
    print()

    print('【风险指标】')
    print(f"最大回撤: {float(ps['drawdown'])*100:.2f}%")
    print(f"年化波动率: {float(ps['annualStandardDeviation'])*100:.2f}%")
    print(f"99% VaR: {float(ps['valueAtRisk99'])*100:.2f}%")
    print(f"95% VaR: {float(ps['valueAtRisk95'])*100:.2f}%")
    print()

    print('【基准对比指标】')
    print(f"Alpha: {float(ps['alpha'])*100:.2f}%")
    print(f"Beta: {float(ps['beta']):.4f}")
    print(f"Sharpe Ratio: {float(ps['sharpeRatio']):.4f}")
    print(f"Sortino Ratio: {float(ps['sortinoRatio']):.4f}")
    print(f"Information Ratio: {float(ps['informationRatio']):.4f}")
    print(f"Tracking Error: {float(ps['trackingError'])*100:.2f}%")
    print(f"Treynor Ratio: {float(ps['treynorRatio']):.4f}")
    print()

    print('【交易统计】')
    print(f"总交易次数: {ts['totalNumberOfTrades']}")
    print(f"胜率: {float(ps['winRate'])*100:.2f}%")
    print(f"盈亏比: {float(ps['profitLossRatio']):.4f}")
    print(f"期望值: {float(ps['expectancy']):.4f}")
    print(f"Profit Factor: {float(ts['profitFactor']):.4f}")
    print(f"最大连续盈利: {ts['maxConsecutiveWinningTrades']}")
    print(f"最大连续亏损: {ts['maxConsecutiveLosingTrades']}")
    print()

    print('【交易细节】')
    print(f"平均持仓时间: {ts['averageTradeDuration']}")
    print(f"平均盈利: ${float(ts['averageProfit']):,.2f}")
    print(f"平均亏损: ${float(ts['averageLoss']):,.2f}")
    print(f"最大单笔盈利: ${float(ts['largestProfit']):,.2f}")
    print(f"最大单笔亏损: ${float(ts['largestLoss']):,.2f}")
    print(f"总手续费: ${float(ts['totalFees']):,.2f}")