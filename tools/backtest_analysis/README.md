# Backtest Analysis Tools

回测结果分析工具集，用于深入分析QuantConnect策略的回测表现。

## 工具清单

### 1. compare_backtests.py - 综合对比分析

最全面的回测对比工具，生成9个详细的分析表格。

**功能**:
- 参数对比
- 交易数量对比
- 平仓原因分析
- 持仓时长分析
- 持仓时长分布
- 整体表现对比
- 质量分数分析
- 风控触发统计
- 版本间变化分析

**使用方法**:
```bash
cd tools/backtest_analysis
python compare_backtests.py
```

**输出示例**:
```
================================================================================
1. 参数对比
================================================================================
版本                                  Sigma      Max Days        评分上界
--------------------------------------------------------------------------------
Smooth Brown Pig (v7.2.8)            2.5        45              45
Swimming Asparagus Lion (v7.2.9)     2.0        45              45
Virtual Fluorescent Yellow Dogfish   2.0        30              60

================================================================================
2. 交易数量对比
================================================================================
版本                                  总交易数     独特配对     开仓       平仓
--------------------------------------------------------------------------------
Smooth Brown Pig (v7.2.8)            360         90          180        180
Swimming Asparagus Lion (v7.2.9)     376         94          188        188
Virtual Fluorescent Yellow Dogfish   384         96          192        192
...
```

---

### 2. analyze_trades_detail.py - 详细交易分析

专注于交易细节的深度分析工具。

**功能**:
- 平仓原因详细统计（含百分比）
- 持仓时长分布（按平仓原因分组）
- 超过30天持仓分析
- PAIR DRAWDOWN触发分析
- TIMEOUT触发分析
- 止损(STOP_LOSS)分析
- 正常平仓(CLOSE)分析
- 关键指标对比总结

**使用方法**:
```bash
cd tools/backtest_analysis
python analyze_trades_detail.py
```

**输出示例**:
```
================================================================================
1. 平仓原因详细统计
--------------------------------------------------------------------------------
Pig (v7.2.8):
  STOP                  46 次 (51.1%)
  CLOSE                 30 次 (33.3%)
  TIMEOUT                8 次 ( 8.9%)
  PAIR DRAWDOWN          6 次 ( 6.7%)

================================================================================
4. PAIR DRAWDOWN触发分析 (v7.2.10新增规则)
--------------------------------------------------------------------------------
Dogfish (v7.2.10): 3个PAIR DRAWDOWN触发
  平均持仓时长: 22.0天
  触发的配对:
    (OKE, OXY): 持仓14天, 2023-10-24
    (D, NEE): 持仓19天, 2024-04-15
    (OXY, SLB): 持仓33天, 2024-05-28
...
```

---

### 3. extract_stats.py - 快速性能指标提取

轻量级工具，快速提取关键性能指标。

**功能**:
- 总收益率
- 年化收益率
- 夏普比率
- 最大回撤
- 胜率
- 总交易次数

**使用方法**:
```bash
cd tools/backtest_analysis
python extract_stats.py
```

**输出示例**:
```
================================================================================
整体表现指标对比
================================================================================

Pig (v7.2.8):
  Total Return: 4.36%
  Annual Return: 9.43%
  Sharpe Ratio: 0.2041
  Max Drawdown: 3.00%
  Win Rate: 45.83%
  Total Trades: 76
...
```

---

## 依赖要求

```bash
pip install pandas numpy
```

**Python版本**: 3.8+

---

## 目录结构

```
tools/
└── backtest_analysis/
    ├── __init__.py               # 包初始化文件
    ├── README.md                 # 本文档
    ├── compare_backtests.py      # 综合对比工具
    ├── analyze_trades_detail.py  # 交易细节分析
    └── extract_stats.py          # 性能指标提取
```

---

## 工作原理

所有工具都使用**相对路径**自动定位backtests/目录:

```python
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BACKTESTS_DIR = os.path.join(SCRIPT_DIR, '..', '..', 'backtests')
```

因此，无论从哪个目录运行脚本，都能正确找到回测文件。

---

## 已分析的回测版本

当前工具预配置了以下三个版本的对比分析:

| 版本 | 关键参数 | 文件前缀 |
|------|---------|----------|
| v7.2.8 | sigma=2.5, max_days=45, max_acceptable_days=45 | Smooth Brown Pig |
| v7.2.9 | sigma=2.0, max_days=45, max_acceptable_days=45 | Swimming Asparagus Lion |
| v7.2.10 | sigma=2.0, max_days=30, zero_score_threshold=60 | Virtual Fluorescent Yellow Dogfish |

---

## 添加新回测版本

如需分析新的回测结果，修改脚本中的`backtests`字典:

```python
# 在compare_backtests.py中
backtests = {
    # ... 现有版本 ...
    'Your New Version': {
        'trades': os.path.join(BACKTESTS_DIR, 'Your_Backtest_Name_trades.csv'),
        'json': os.path.join(BACKTESTS_DIR, 'Your_Backtest_Name.json'),
        'logs': os.path.join(BACKTESTS_DIR, 'Your_Backtest_Name_logs.txt'),
        'params': {'sigma': 2.2, 'max_days': 35, 'zero_score_threshold': 60}
    }
}
```

---

## 常见问题

**Q: 为什么脚本要放在tools/而不是backtests/?**
A: backtests/文件夹专门存放回测结果(.json/.csv/.txt),分析工具应该分离管理,避免混淆。

**Q: 如何自定义分析指标?**
A: 编辑对应的Python脚本,添加新的统计逻辑。所有工具都有清晰的函数注释。

**Q: 脚本运行报错找不到文件?**
A: 确保backtests/目录中存在对应的回测文件(检查文件名是否完全匹配)。

---

## 贡献指南

欢迎添加新的分析工具!新工具应该:
- 使用相对路径访问backtests/
- 添加清晰的docstring
- 在此README中更新文档

---

## 版本历史

- **v1.0.0** (2025-01-27): 初始版本,从backtests/迁移并优化路径处理
