# Alpha模块详细文档

## 模块概述

Alpha模块是贝叶斯协整配对交易策略的核心决策引擎，负责从原始市场数据到交易信号的完整处理流程。采用模块化设计，将复杂的策略分解为独立的功能组件。

## 系统架构

```
          AlphaModel（主控制器）
                |
    ┌───────────┼───────────────────┐
    |           |                   |
    v           v                   v
┌─────────┐ ┌──────────────┐ ┌─────────────┐
│DataProc │ │CointegAnalyzer│ │BayesModeler │
└─────────┘ └──────────────┘ └─────────────┘
    |           |                   |
    v           v                   v
清洗数据    协整配对           模型参数
    └───────────┼───────────────────┘
                |
                v
        ┌──────────────┐
        │SignalGenerator│
        └──────────────┘
                |
                v
         交易信号(Insights)
```

## 核心组件

### 1. AlphaModel（主协调器）
- **职责**：协调各子模块，管理整体工作流程
- **触发时机**：月度选股日（OnSecuritiesChanged）和日常更新（Update）
- **关键功能**：
  - 接收UniverseSelection筛选的股票
  - 协调配对分析流程
  - 生成和返回交易信号

### 2. AlphaState（状态管理）
- **持久状态**：跨周期保持的数据
  - `modeled_pairs`: 当前活跃的配对列表
  - `historical_posteriors`: 历史后验参数
  - `zscore_ema`: Z-score EMA值
- **临时状态**：选股日使用后清理
  - `clean_data`: 处理后的历史数据
  - `valid_symbols`: 通过筛选的股票
  - `cointegrated_pairs`: 协整配对列表
- **控制状态**：流程控制标志
  - `is_selection_day`: 是否为选股日
  - `symbols`: 当前跟踪的股票列表

### 3. DataProcessor（数据处理）
- **数据下载**：获取252天历史OHLCV数据
- **完整性检查**：要求至少98%的数据点存在
- **合理性检查**：剔除价格为负或零的异常数据
- **缺失值填补**：线性插值 + 前向/后向填充

### 4. CointegrationAnalyzer（协整分析）
- **行业分组**：只在相同行业内寻找配对
- **协整检验**：Engle-Granger两步法，p值<0.05
- **相关性要求**：Pearson相关系数>0.7
- **质量评分系统**：
  - 统计显著性（40%）：1-pvalue
  - 相关性（20%）：价格序列相关系数
  - 流动性匹配（40%）：成交额比率

### 5. BayesianModeler（贝叶斯建模）
- **核心模型**：`log(price1) = alpha + beta * log(price2) + epsilon`
- **MCMC采样**：
  - 完全建模：1000次预热 + 1000次采样
  - 动态更新：500次预热 + 500次采样（使用历史后验）
- **先验设置**：
  - 完全建模：alpha~N(0,10), beta~N(1,5), sigma~HalfNormal(5)
  - 动态更新：使用历史后验参数
- **参数输出**：
  - alpha：截距项，反映基础价差
  - beta：斜率项，即对冲比率
  - sigma：误差标准差

### 6. SignalGenerator（信号生成）
- **Z-Score计算**：
  ```
  残差 = log(P1) - (alpha + beta*log(P2)) - residual_mean
  z-score = 残差 / residual_std
  ```
- **EMA平滑**：`smoothed = 0.8*current + 0.2*previous`
- **信号阈值**：
  - 建仓：|z-score| > 1.2
  - 平仓：|z-score| < 0.3
  - 极端偏离：|z-score| > 3.0（强制平仓）
- **交易方向**：
  - z > 0：股票1高估，做空1做多2
  - z < 0：股票1低估，做多1做空2

## 工作流程

### 月度选股日流程
1. **接收股票列表**（OnSecuritiesChanged）
   - 从UniverseSelection接收筛选后的股票
   - 标记为选股日

2. **AlphaModel直接调用三个模块**：
   ```python
   # 步骤1: 数据处理
   data_result = self.data_processor.process(symbols)

   # 步骤2: 协整分析
   cointegration_result = self.cointegration_analyzer.analyze(
       valid_symbols, clean_data, sector_code_to_name
   )

   # 步骤3: 贝叶斯建模
   modeling_result = self.bayesian_modeler.model_pairs(
       cointegrated_pairs, clean_data
   )
   ```

### 日常信号生成流程
1. **Z-score计算**
   - 获取当前价格
   - 基于模型参数计算残差
   - 标准化为z-score

2. **EMA平滑**
   - 应用指数移动平均
   - 减少短期噪音

3. **信号生成**
   - 检查市场条件（SPY监控）
   - 根据z-score生成交易方向
   - 创建Insight组

## 配置参数

### 数据处理
- `lookback_period`: 252（历史数据天数）
- `min_data_completeness_ratio`: 0.98（最低数据完整性）

### 协整分析
- `pvalue_threshold`: 0.05（协整检验p值阈值）
- `correlation_threshold`: 0.7（最低相关系数）
- `max_symbol_repeats`: 1（每股票最多出现次数）
- `max_pairs`: 20（最大配对数）
- `quality_weights`:
  - `statistical`: 0.4
  - `correlation`: 0.2
  - `liquidity`: 0.4

### 贝叶斯建模
- `mcmc_warmup_samples`: 1000（预热采样数）
- `mcmc_posterior_samples`: 1000（后验采样数）
- `mcmc_chains`: 2（马尔可夫链数量）

### 信号生成
- `entry_threshold`: 1.2（建仓阈值）
- `exit_threshold`: 0.3（平仓阈值）
- `upper_limit`: 3.0（极端偏离上限）
- `lower_limit`: -3.0（极端偏离下限）
- `flat_signal_duration_days`: 5（平仓信号持续天数）
- `entry_signal_duration_days`: 3（建仓信号持续天数）

### 市场风控
- `market_severe_threshold`: 0.05（SPY单日跌幅阈值）
- `market_cooldown_days`: 14（市场冷静期天数）

## 风控机制

### Alpha层风控
1. **市场风控**：SPY单日下跌>5%触发14天冷静期
2. **重复建仓检查**：基于Portfolio查询避免重复
3. **极端偏离控制**：|z-score|>3强制平仓

### 与其他模块交互
- **输入**：UniverseSelection提供候选股票
- **输出**：向PortfolioConstruction提供Insights
- **协作**：RiskManagement可能修改信号

## 性能优化

1. **批量处理**：月度批量分析，减少计算频率
2. **数据缓存**：避免重复下载历史数据
3. **向量化计算**：使用NumPy提升效率
4. **动态更新**：利用历史后验减少MCMC采样

## 注意事项

1. **计算密集**：选股日会进行大量计算，可能影响性能
2. **MCMC瓶颈**：贝叶斯建模是主要性能瓶颈
3. **数据依赖**：需要稳定的历史数据源
4. **参数敏感性**：阈值调整会显著影响交易频率

## 模块文件结构

- **AlphaModel.py** (130行): 主控制器，直接协调各模块
- **AlphaState.py** (76行): 状态管理
- **DataProcessor.py** (115行): 数据处理
- **CointegrationAnalyzer.py** (218行): 协整分析
- **BayesianModeler.py** (198行): 贝叶斯建模
- **SignalGenerator.py** (236行): 信号生成

**总计**: 约973行（优化后删除了PairAnalyzer中间层）

## 使用示例

```python
# 初始化Alpha模型
alpha_model = BayesianCointegrationAlphaModel(
    algorithm=self,
    config=alpha_config,
    sector_code_to_name=sector_mapping
)

# 设置到算法框架
self.SetAlpha(alpha_model)
```

## 调试技巧

1. **日志级别**：通过debug_level控制输出详细度
2. **关键指标监控**：
   - 配对数量和质量分数
   - Z-score值和平滑效果
   - 建模成功率
3. **性能监控**：
   - MCMC采样时间
   - 数据处理耗时
   - 内存使用情况