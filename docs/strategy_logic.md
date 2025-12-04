# Strategy Logic Documentation

本文档用流程图形式描述贝叶斯协整配对交易策略的完整执行逻辑。

**当前版本**: v8.24.0 (2025-12-04)

---

# Part 1: Universe Selection (选股逻辑)

```
触发机制: Schedule.On (每月初 9:10 AM) → trigger_selection()
    ↓
=========================================
阶段一: 粗选 (_select_coarse)
=========================================
    ↓
输入: QuantConnect 全市场股票 (~8000只)
    ↓
步骤1: 基础筛选 (AND逻辑，全部满足)
    ├─ HasFundamentalData = True      ← 排除ETF/ADR等
    ├─ Price ≥ $30                    ← 排除低价股
    ├─ MarketCap ≥ $1B                ← 大中盘股
    ├─ DollarVolume ≥ $100M           ← 流动性门槛
    └─ IPO时间 ≥ 360天                ← 排除次新股
    ↓
步骤2: 流动性排序
    └─ 按 DollarVolume 降序 → 取 TOP 400
    ↓
输出: ~400只候选股票 Symbol 列表
    ↓
=========================================
阶段二: 精选 (_select_fine)
=========================================
    ↓
输入: 400只候选股票的 FineFundamental 数据
    ↓
步骤1: 财务筛选 (FinancialValidator)
    └─ 估值筛选 (OR逻辑，任一满足):
        ├─ PE ≤ 80   ← 传统价值股
        └─ PS ≤ 10   ← 高成长股豁免
    ↓
步骤2: 合并ETF Symbol (静默)
    └─ 添加 VIX、行业ETF等用于风控和对冲
    ↓
输出: ~300只股票 → last_fine_selected_symbols
    ↓
下游: OnSecuritiesChanged() → _run_analysis_pipeline()
```

### 关键配置参数

| 参数 | 值 | 含义 |
|------|-----|------|
| `min_price` | $30 | 股价下限 |
| `min_market_cap` | $1B | 市值下限 |
| `min_dollar_volume` | $100M | 日成交额下限 |
| `min_days_since_ipo` | 360天 | IPO时间要求 |
| `max_coarse_stocks` | 400 | 粗选数量上限 |
| `PE阈值` | ≤80 | 估值筛选 (OR) |
| `PS阈值` | ≤10 | 估值筛选 (OR) |

---

# Part 2: Data Processing (数据处理)

### 时间窗口设计 (v8.24.0 - 消除数据窥探 + Z-score回算)

```
t=-240        t=-60         t=0 (当前)
  |______________|______________|
       180天            60天
         ↓                ↓
   协整检验期        参数估计期
   (EG Test)         (MCMC)
   样本内筛选        样本外估计
         └─────────────────┘
              180天
         Z-score回算窗口 (v8.24.0)
```

**设计原理**:
- **单一数据源**: `DataProcessorConfig` 定义 `total_lookback_days=240` + `bayesian_lookback_days=60`
- **派生计算**: 协整窗口 = 240 - 60 = 180天 (各模块自行计算)
- **数据隔离**: 协整检验和MCMC使用完全不重叠的时间段
- **v8.24.0 回算策略**: 用MCMC估计的最新α,β参数，回算过去180天的Z-score序列

```
触发: _run_analysis_pipeline() 步骤1
    ↓
=========================================
输入: UniverseSelection 输出的 ~300 只股票
=========================================
    ↓
步骤1: 批量下载历史数据
    └─ algorithm.History(symbols, 240, Resolution.Daily)
    └─ 返回多级索引 DataFrame (symbol × date)
    ↓
=========================================
步骤2: 逐股票验证 (_validate_data)
=========================================
    ↓
检查1: 数据存在性
    └─ symbol 是否在 DataFrame 索引中
    └─ 失败原因: data_missing
    ↓
检查2: 数据完整性
    └─ len(data) == 240 (恰好240个交易日)
    └─ 失败原因: incomplete
    ↓
检查3: 缺失值检查
    └─ close.isnull().any() == False
    └─ 失败原因: has_missing_values
    ↓
检查4: 价格合理性
    └─ (close > 0).all()
    └─ 失败原因: invalid_values
    ↓
检查5: 波动率过滤
    └─ 年化波动率 ≤ 70%
    └─ 计算: daily_returns.std() × √252
    └─ 失败原因: high_volatility
    ↓
检查6: 极端跌幅过滤
    └─ 单日最大跌幅 ≥ -10%
    └─ 失败原因: extreme_drawdown
    ↓
=========================================
输出
=========================================
    ↓
{
    'clean_data': {symbol: DataFrame},  ← 每只股票240天OHLCV
    'valid_symbols': [Symbol],          ← 通过验证的股票列表
    'statistics': {...}                 ← 统计信息
}
    ↓
下游: CointegrationAnalyzer.cointegration_procedure()
```

### 关键配置参数

| 参数 | 值 | 含义 |
|------|-----|------|
| `total_lookback_days` | 240 | **交易日** - 总数据下载量 (v8.24.0) |
| `bayesian_lookback_days` | 60 | **交易日** - MCMC建模窗口 |
| `zscore_back_projection_days` | 180 | **交易日** - Z-score回算窗口 (v8.24.0) |
| `data_completeness_ratio` | 1.0 | 100%数据完整性要求 |
| `max_annualized_volatility` | 0.5 | 年化波动率上限50% |
| `max_daily_drawdown` | -0.10 | 单日跌幅下限-10% |

---

# Part 3: Cointegration Analyzer (协整分析)

```
触发: _run_analysis_pipeline() 步骤2
    ↓
=========================================
输入: DataProcessor 输出的 ~280 只有效股票 + clean_data
=========================================
    ↓
步骤1: 按行业分组 (_group_by_industry)
    ├─ 股票: 读取 MorningstarIndustryGroupCode (55个标准分组)
    ├─ ETF: 查询 etf_industry_mapping (支持多行业映射)
    ├─ 过滤: 行业内股票数 ≥ 10 (min_stocks_per_industry)
    └─ 限制: 行业内最多 50只 (max_stocks_per_industry)
    ↓
输出: ~15-20个有效行业分组
    ↓
=========================================
步骤2: 行业内协整检验 (逐行业遍历)
=========================================
    ↓
对每个行业:
    ├─ 生成所有配对组合: C(n,2) = n(n-1)/2
    │   例: 30只股票 → 435对候选
    ↓
    对每对 (symbol1, symbol2):
        ├─ 检查1: 数据长度一致 (len==len)
        ├─ 检查2: 时间索引对齐 (index.equals)
        ↓
        Engle-Granger 协整检验
        └─ statsmodels.tsa.stattools.coint(prices1, prices2)
        ↓
        返回: (score, pvalue, critical_values)
        ↓
        筛选: pvalue < 0.01 (pvalue_threshold)
        ↓
    └─ 按 pvalue 升序排序 (越小越显著)
    ↓
=========================================
步骤3: 随机抽样限流 (v8.16.0 - MCMC算力保护)
=========================================
    ↓
汇总所有行业的协整配对 (可能 100+ 对)
    ↓
判断: len(all_pairs) > max_cointegrated_pairs (20)?
    ├─ YES: random.sample(all_pairs, 20)
    │       ⭐ 设计原理: p-value < 0.01 已筛选,通过的都是"合格品"
    │          无需排序,随机抽样保持公平性
    └─ NO: 保留全部
    ↓
输出日志: "[协整限流] 120对 → 随机抽样20对"
    ↓
=========================================
输出
=========================================
    ↓
{
    'pairs': [                          ← 限流后的配对列表 (≤20对)
        {
            'symbol1': AAPL,
            'symbol2': MSFT,
            'pvalue': 0.008,
            'industry_code': 31165
        },
        ...
    ],
    'statistics': {...}
}
    ↓
下游: 构建 PairData 字典 (步骤4)
```

### 关键配置参数

| 参数 | 值 | 含义 |
|------|-----|------|
| `pvalue_threshold` | 0.01 | 协整显著性水平 (1%) |
| `min_stocks_per_industry` | 10 | 行业内最少股票数 |
| `max_stocks_per_industry` | 50 | 行业内最多股票数 |
| `max_symbol_repeats` | 2 | 单股最多参与配对数 |
| `max_cointegrated_pairs` | 20 | 协整配对数量上限 (v8.16.0) |

---

# Part 4: PairData Construction (数据封装)

```
触发: _run_analysis_pipeline() 步骤4
    ↓
=========================================
输入: 协整检验后的配对列表 (限流后≤20对) + clean_data
=========================================
    ↓
对每个配对:
    ├─ 从 clean_data 提取 Close 价格
    ├─ 执行对数转换: LogPrices = np.log(Prices)
    └─ 验证长度一致性
    ↓
=========================================
输出: PairData 对象
=========================================
    ↓
@dataclass (frozen=True, 不可变)
├─ symbol1, symbol2: Symbol
├─ prices1, prices2: np.ndarray
├─ log_prices1, log_prices2: np.ndarray (预计算)
└─ industry_code: str
    ↓
下游: BayesianModeler.modeling_procedure()
```

---

# Part 5: Bayesian Modeler (贝叶斯建模)

```
触发: _run_analysis_pipeline() 步骤5
    ↓
=========================================
输入: PairData 对象字典 (≤20对) + 协整配对列表 (含OLS估计)
=========================================
    ↓
步骤1: 先验选择 (v8.5.0 Empirical Bayes 两级体系)
    ├─ Level 1 (Historical): 存在有效历史后验 (30天内)
    │   ├─ α, β: Normal (均值/方差源自历史后验)
    │   ├─ ρ: Beta (矩匹配拟合)
    │   └─ σ_η: HalfNormal (放大系数2.5)
    │
    └─ Level 2 (OLS Informed): 无历史记录 (所有配对必有)
        ├─ α: Normal(α̂_ols, α_se × k)     ← 数据驱动
        ├─ β: Normal(β̂_ols, β_se × k)     ← 数据驱动
        ├─ ρ: Beta(2, 2)                   ← 弱先验
        └─ σ_η: HalfNormal(σ̂_ols × 0.1)   ← 数据驱动
    ↓
步骤2: 数据窗口截取 (时间隔离)
    └─ 使用最近 60 个交易日 (与协整检验252天完全隔离)
    ↓
步骤3: 联合贝叶斯建模 (AR(1) 变换形式)
    ├─ 模型: y_t = α(1-ρ) + β(x_t - ρx_{t-1}) + ρy_{t-1} + η_t
    └─ MCMC: PyMC (4 chains × 500 draws, 500 warmup)
    ↓
=========================================
输出: ModelResult 对象
=========================================
    ↓
├─ 后验均值: alpha_mean, beta_mean, rho_mean, sigma_mean
├─ 后验标准差: alpha_std, beta_std, rho_std, sigma_std
├─ 衍生指标: half_life = -ln(2) / ln(ρ)
└─ 元数据: modeling_type (historical_posterior/ols_informed)
    ↓
下游: PairSelector.selection_procedure()
```

### Empirical Bayes 设计原理 (v8.5.0)

```
t=-240        t=-60         t=0 (当前)
  |______________|______________|
       180天            60天
         ↓                ↓
   OLS回归估计       MCMC参数估计
   β̂_ols, se(β̂)    beta ~ N(β̂_ols, se×k)
   "长期历史经验"    "短期参数更新"
```

**核心思想**: Long-term Posterior (OLS) = Short-term Prior (MCMC)
- OLS 在协整窗口 (180天) 上估计参数，作为 MCMC 的先验
- MCMC 在建模窗口 (60天) 上更新参数，得到后验
- 两个窗口完全隔离，消除数据窥探

### v8.24.0 Z-score 回算策略

```
近端建模 + 远端回算 (Projection Strategy)

MCMC建模: 后60天数据 → 获取最新 α, β 参数
                ↓
Z-score回算: 用 α, β 回算全180天的 Z-score
                ↓
分位数计算: 180个样本点 → P99/P99.9 有统计意义
```

**实现代码** (`BayesianModeler._extract_posterior_stats`):
```python
# 从MCMC获取最新参数
alpha_mean = float(np.mean(trace['alpha']))
beta_mean = float(np.mean(trace['beta']))

# 用最新参数回算180天spread
projection_days = config.pairs.zscore_back_projection_days  # 180
y_full = pair_data.log_prices1[-projection_days:]
x_full = pair_data.log_prices2[-projection_days:]
log_spread = y_full - (alpha_mean + beta_mean * x_full)

# 计算180天Z-score序列
residual_std = float(np.std(log_spread))
zscore_series = (log_spread - np.mean(log_spread)) / residual_std
```

### 关键配置参数

| 参数 | 值 | 含义 |
|------|-----|------|
| `bayesian_lookback_days` | 60 | 建模数据窗口 (交易日) |
| `ols_prior_sigma_multiplier` | 2.0 | OLS标准误放宽倍数 (α, β) |
| `sigma_eta_scale_factor` | 0.1 | 状态噪声缩放因子 |
| `mcmc_chains` | 4 | MCMC链数 |
| `mcmc_warmup` | 500 | 预热采样次数 |
| `mcmc_draws` | 500 | 正式采样次数 |
| `informed_validity_days` | 30 | 历史后验有效期 |

---

# Part 6: Pair Selector (配对筛选) - v8.24.0 稀有事件捕捉

```
触发: _run_analysis_pipeline() 步骤5
    ↓
=========================================
输入: ModelResult 对象列表
=========================================
    ↓
三维度阈值筛选 (Pass/Fail 逻辑)
    ↓
维度1: CV BETA 稳定性
    ├─ 公式: CV = beta_std / |beta_mean|
    ├─ 含义: β估计的相对不确定性
    ├─ 阈值: CV ≤ 0.2 通过
    └─ 边界: |beta_mean| < 0.1 → 剔除 (避免CV爆炸)
    ↓
维度2: 半衰期
    ├─ 公式: half_life = -ln(2) / ln(rho_mean)
    ├─ 含义: 残差回归到一半所需天数
    └─ 阈值: 3 ≤ half_life ≤ 30 天通过
    ↓
维度3: 零轴穿越
    ├─ 计算: spread 穿越均值的次数
    ├─ 含义: 穿越越多 → 交易机会越多
    └─ 阈值: 2 ≤ count ≤ 60 次通过
    ↓
全部通过 → 保留
任一失败 → 剔除
    ↓
=========================================
v8.24.0: 稀有事件捕捉 (Rare Event Capture)
=========================================
    ↓
核心设计: "近端建模 + 远端回算"
├─ MCMC建模: 后60天 → 获取最新 α, β
├─ Z-score回算: 全180天 → 用最新参数回算历史
└─ 分位数统计: 180个点 → P99有统计意义
    ↓
计算逻辑:
├─ 步骤1: 用60天MCMC的α,β回算180天Z-score序列
├─ 步骤2: 取 |Z-score| 的 P99 和 P99.9 分位数
└─ 步骤3: 返回 residual_std (原始残差标准差，因配对而异)
    ↓
输出: 每个配对的个性化入场区间 [P99, P99.9]
├─ P99 ≈ 2.33σ: 入场下限 (100次出现1次)
├─ P99.9 ≈ 3.1σ: 入场上限 (1000次出现1次)
└─ residual_std: 配对个性化标准差 (用于止损步长)
    ↓
=========================================
漏斗日志输出
=========================================
    └─ [PairSelector] 输入 N → CV_BETA (-X) → Half_life (-Y) → ZeroCrossing (-Z) → Data (-W) → 输出 M
    ↓
=========================================
输出: 最终入选配对列表 (含 entry_threshold, entry_upper, zscore_std)
=========================================
    ↓
下游: 创建 Pairs 对象 (步骤6)
```

### 关键配置参数

| 参数 | 值 | 含义 |
|------|-----|------|
| `cv_beta_threshold` | 0.2 | CV > 0.2 → 剔除 |
| `min_abs_beta` | 0.1 | \|β\| < 0.1 → 剔除 |
| `half_life_min` | 3.0 | < 3天 → 剔除 |
| `half_life_max` | 30.0 | > 30天 → 剔除 |
| `zero_crossing_min` | 2 | < 2次 → 剔除 |
| `zero_crossing_max` | 60 | > 60次 → 剔除 |
| `adaptive_entry_enabled` | True | 自适应阈值总开关 |
| `entry_percentile_lower` | 99.0 | 入场下限百分位 P99 (v8.24.0) |
| `entry_percentile_upper` | 99.9 | 入场上限百分位 P99.9 (v8.24.0) |
| `zscore_back_projection_days` | 180 | Z-score回算窗口 (v8.24.0) |

### 设计说明 (v8.9.1)

**删除 Hurst 维度的原因**:
- 60天 spread 数据不足以稳健计算 R/S 分析
- Hurst 指数需要较长时间序列才能得到可靠估计
- 半衰期和零轴穿越已能有效筛选均值回归特性

### v8.24.0 稀有事件捕捉设计

**核心思想**: 只在真正的极端情况入场，捕捉"稀有事件"的回归收益

**为什么从 [P90, P99] 提升到 [P99, P99.9]?**

| 分位数 | 对应σ | 发生频率 | 评估 |
|--------|-------|----------|------|
| P90 | ~1.28σ | 10次/1次 | ❌ 太常见，正常噪音 |
| P99 | ~2.33σ | 100次/1次 | ✅ 开始有意思 |
| P99.9 | ~3.1σ | 1000次/1次 | ✅ 极端机会 |

**为什么需要180天回算?**

| 窗口 | 样本量 | P99对应点位 | 统计稳健性 |
|------|--------|-------------|------------|
| 60天 | 60 | 第0.6个 (≈最大值) | ❌ 不稳定 |
| 180天 | 180 | 第1.8个 | ✅ 有意义 |

**为什么用 residual_std 而非 zscore_std?**
- zscore_series 是标准化序列，std(zscore) ≈ 1.0 (数学恒等)
- residual_std 是原始残差标准差，因配对而异
- 用于计算个性化止损步长: `trailing_step = residual_std × multiplier`

**示例**:
| 配对 | 180天P99 | 180天P99.9 | residual_std | 入场区间 |
|------|----------|------------|--------------|----------|
| JPM-USB | 2.35σ | 3.12σ | 0.0234 | [2.35, 3.12] |
| XOM-CVX | 2.18σ | 2.89σ | 0.0156 | [2.18, 2.89] |

---

# Part 7: Pairs Creation & Management (配对创建与管理)

```
触发: _run_analysis_pipeline() 步骤6-7
    ↓
=========================================
步骤6: Pairs对象创建
=========================================
    ↓
工厂方法: Pairs.from_model_result(algorithm, model_result, config)
    ↓
初始化内容:
├─ 基础信息: symbol1, symbol2, industry_code
├─ 贝叶斯参数: α, β, μ_res, σ_res
├─ 质量指标: quality_score, half_life
└─ 交易阈值: entry[2.0-2.5σ], exit[0.5σ]
    ↓
=========================================
步骤7: PairsManager分类管理
=========================================
    ↓
classify_pairs(new_pairs_dict):
    ↓
对每个配对:
    ├─ 已存在 + 无持仓 → update_params() 更新参数 (v8.20.1: 含RSI历史重置)
    ├─ 已存在 + 有持仓 → 跳过 (参数冻结)
    └─ 新配对 → 注册到 all_pairs
    ↓
分类:
├─ current_selected_pair_ids: 本轮入选 (可开新仓)
└─ past_selected_pair_ids: 历史曾入选 (只能平仓)
    ↓
=========================================
输出: PairsManager 管理的配对集合
=========================================
    ↓
等待 OnData 触发交易执行
```

### 分析管道汇总 (7步流程)

```
[Analysis汇总] 输入280 → 有效260 → 候选3500对 → 协整180对 (18行业) → 贝叶斯180对 → 质量筛选25对 → 创建25个Pairs
```

### v8.20.1 RSI历史生命周期修复

**问题**: 跨周期时 `update_params()` 更新了参数，但 `zscore_history` 和 `rsi_history` 仍保留旧参数计算的数据。

**关键发现**: 冷却期间 `get_signal()` 仍被调用（在 `is_in_cooldown()` 检查之前），同周期内数据保持连续。

**两种场景区分**:
| 场景 | 参数变化 | 数据处理 |
|------|----------|----------|
| 同周期内反复开仓 | 参数不变 | **不清空** (数据连续) |
| 跨周期开仓 | `update_params()` 更新 | **清空+预热** |

**修复**: `update_params()` 中添加:
```python
self.zscore_history.clear()
self.rsi_history.clear()
self._warmup_zscore_history()  # 从 algorithm.clean_data 重新预热
```

---

# Part 8: Portfolio Risk Management (组合级风控)

```
触发: OnData() 优先级 1-2
    ↓
=========================================
优先级1: 冷却期检查 (Hard Stop)
=========================================
    ↓
检查: risk_manager.is_in_portfolio_cooldown()
    ├─ 条件: CurrentTime < CooldownUntil
    └─ 触发: 直接 return (策略完全停止)
    ↓
=========================================
优先级2: 回撤检查 (Eject Button)
=========================================
    ↓
步骤1: 更新高水位
    └─ HWM = max(HWM, TotalPortfolioValue)
    ↓
步骤2: 计算回撤
    └─ Drawdown = (HWM - CurrentValue) / HWM
    ↓
步骤3: 触发判断
    ├─ 条件: Drawdown ≥ 20%
    └─ 动作:
        ├─ Liquidate() (强平所有持仓)
        ├─ activate_portfolio_cooldown() (360天冷却)
        └─ 重置 HWM
    ↓
通过 → 继续执行配对级检查
```

### 关键配置参数

| 参数 | 值 | 含义 |
|------|-----|------|
| `drawdown_threshold` | 20% | 组合回撤触发阈值 |
| `drawdown_cooldown_days` | 360 | 回撤触发后冷却期 |

---

# Part 9: Pair-level Health Check (配对级风控) - v8.15.0 简化PairBreak

```
触发: OnData() 优先级3
    ↓
=========================================
输入: 所有持仓中的配对
=========================================
    ↓
PairsManager.check_pairs_health() 返回问题字典
    ↓
优先级1: Anomaly (异常持仓)
    ├─ 定义: 单边持仓 或 同向持仓
    ├─ 动作: 立即平仓修正
    └─ 冷却期: 永久 (half_life × 999999)
    ↓
优先级2: PairBreak (协整破裂 - v8.15.0 纯Z-score检测)
    ├─ 触发条件 (Z-score方向感知):
    │   ├─ 多头持仓: Z < -4.0σ (价差继续恶化)
    │   └─ 空头持仓: Z > +4.0σ (价差继续恶化)
    ├─ 设计说明: v8.15.0移除β漂移条件 (卡尔曼滤波在对数价格下有粘性)
    └─ 冷却期: half_life × 6 (v8.13.0)
    ↓
优先级3: Timeout (持仓超时)
    ├─ 定义: holding_days > max_holding_days
    ├─ max_holding_days = half_life × log₀.₅(exit/entry_zscore)
    └─ 冷却期: half_life × 4 (v8.13.0)
    ↓
优先级4: Drawdown (单体回撤)
    ├─ 定义: (HWM - CurrentValue) / HWM > 5%
    └─ 冷却期: half_life × 6 (v8.13.0)
    ↓
=========================================
执行: main.py 遍历问题字典
=========================================
    ↓
对每个问题配对:
├─ 生成 CloseIntent (reason=问题类型)
└─ OrderExecutor.execute_close(intent)
```

### v8.15.0 关键变更

1. **移除卡尔曼滤波**: β漂移检测在对数价格下存在"粘性"问题，即使提高Q参数也无法有效跟踪β变化
2. **简化PairBreak逻辑**: 从AND条件(Z-score + β漂移)简化为纯Z-score方向感知检测
3. **提高PairBreak阈值**: 3.5σ → 4.0σ (补偿移除β漂移条件带来的敏感度)

### v8.13.0 动态冷却期机制

**设计理念**: "量体裁衣" - 冷却期与配对自身的均值回归速度挂钩

**核心公式**: `cooldown_days = ceil(half_life × multiplier)`

**Multiplier配置**:
| 平仓原因 | Multiplier | 含义 |
|----------|------------|------|
| `MEAN_REVERSION` | 2.0 | 正常平仓: 等待2个完整回归周期 |
| `TIMEOUT` | 4.0 | 超时平仓: 等待4个完整回归周期 |
| `DRAWDOWN` | 6.0 | 回撤止损: 充分冷却后再重试 |
| `PAIR_BREAK` | 6.0 | 协整破裂: 充分冷却后再重试 |
| `ANOMALY` | 99999.0 | 数据异常: 永久冷却 |

**示例**:
- half_life=8天的快速回归配对: MEAN_REVERSION冷却16天, DRAWDOWN冷却48天
- half_life=15天的慢速回归配对: MEAN_REVERSION冷却30天, DRAWDOWN冷却90天

**保底机制**: 若配对的half_life为None，使用`default_half_life=10.0`天

### 关键配置参数

| 参数 | 值 | 含义 |
|------|-----|------|
| `pair_break_threshold` | 4.0σ | Z-score方向感知阈值 |
| `drawdown_threshold` | 5% | 单体回撤阈值 (统一) |

---

# Part 10: Normal Close (正常平仓)

```
触发: OnData() 优先级4
    ↓
=========================================
输入: 所有持仓配对 (pairs_with_position)
=========================================
    ↓
对每个配对:
    ├─ 检查订单锁: is_pair_locked() → 跳过
    ↓
    获取信号: pair.get_signal(data)
        ↓
    信号: CLOSE (均值回归)
        ├─ 条件: |Z-score| < 0.5σ (exit_threshold)
        ├─ 含义: 价差已回归均值附近
        ├─ 动作: CloseIntent (reason='MEAN_REVERSION')
        └─ 冷却期: half_life × 1 (v8.13.0 动态计算)
        ↓
    信号: HOLD
        └─ 继续持有，等待回归
    ↓
=========================================
执行: OrderExecutor.execute_close(intent)
=========================================
```

### 信号类型说明

| 信号 | 持仓状态 | 含义 |
|------|---------|------|
| `LONG_SPREAD` | 无持仓 | 做多价差 (买入股1，卖空股2) |
| `SHORT_SPREAD` | 无持仓 | 做空价差 (卖空股1，买入股2) |
| `CLOSE` | 有持仓 | 均值回归完成，触发平仓 |
| `HOLD` | 有持仓 | 继续持有 |
| `WAIT` | 无持仓 | 等待入场信号 |

---

# Part 11: VIX Check (开仓安全检查)

```
触发: OnData() 优先级5
    ↓
获取 VIX 最新值
    ↓
判断:
├─ VIX ≥ 35 → 禁止开仓 (return)
├─ VIX < 35 → 允许开仓
└─ VIX 数据缺失 → 默认允许开仓
    ↓
通过 → 继续执行正常开仓
```

### 关键配置参数

| 参数 | 值 | 含义 |
|------|-----|------|
| `vix_threshold` | 35 | VIX恐慌阈值 |

---

# Part 12: RSI on Z-score (动量检测) - v8.7.0

```
触发: get_signal() 入场信号生成时
    ↓
=========================================
设计理念: 橡皮筋理论
=========================================
    ↓
Z-score 告诉你"橡皮筋拉了多远"
RSI on Z-score 告诉你"还在拉还是已松手"
    ↓
目标: 避免在"火箭式冲击"时入场
    ↓
=========================================
三重AND条件
=========================================
    ↓
条件A: Z-score 位置 (由调用方保证)
    └─ entry_threshold_lower ≤ |Z| ≤ entry_threshold_upper
    ↓
条件B: 历史动量确认
    ├─ SHORT_SPREAD: 近期RSI曾 > 80 (超买)
    └─ LONG_SPREAD: 近期RSI曾 < 20 (超卖)
    ↓
条件C: 当前回落/反弹确认
    ├─ SHORT_SPREAD: 当前RSI < 80 (已回落)
    └─ LONG_SPREAD: 当前RSI > 20 (已反弹)
    ↓
=========================================
RSI计算细节
=========================================
    ↓
数据源: Z-score 日变化 (差分)
    └─ changes = [z[i] - z[i-1] for i in range(1, n)]
    ↓
公式: RSI = 100 - 100/(1 + RS)
    └─ RS = avg_gain / avg_loss
    ↓
周期: RSI(8) → 需要9天Z-score数据

=========================================
RSI 计算过程示例 (rsi_period=8)
=========================================

Day:     1    2    3    4    5    6    7    8    9
Z:      2.1  2.3  2.0  1.8  2.2  2.5  2.3  2.1  1.9
           ↓    ↓    ↓    ↓    ↓    ↓    ↓    ↓
变化:     +0.2 -0.3 -0.2 +0.4 +0.3 -0.2 -0.2 -0.2
           └─────────────┬─────────────┘
                    8个变化量
                         ↓
平均涨幅 = (0.2 + 0.4 + 0.3) / 8 = 0.1125
平均跌幅 = (0.3 + 0.2 + 0.2 + 0.2 + 0.2) / 8 = 0.1375
                         ↓
RS = 0.1125 / 0.1375 = 0.818
RSI = 100 - 100/(1+0.818) = 45
    ↓
=========================================
预热机制
=========================================
    ↓
Pairs.__init__() 调用 _warmup_zscore_history()
    ├─ 从 algorithm.clean_data 加载历史价格
    ├─ 计算历史 Z-score 并填充 deque
    └─ 确保首日即可计算RSI (无盲区)
    ↓
=========================================
信号流程
=========================================
    ↓
get_signal(data):
    ↓
1. 计算当前 Z-score
2. 追加到 zscore_history
3. 计算并追加 RSI 到 rsi_history
4. 检查入场区间
5. 若在区间内:
    ├─ 确定候选信号 (SHORT/LONG)
    ├─ 调用 _check_rsi_entry_condition()
    │   ├─ 满足 → 返回信号
    │   └─ 不满足 → 返回 WAIT
    └─ 数据不足 → 回退原始逻辑
```

### 关键配置参数

| 参数 | 值 | 含义 |
|------|-----|------|
| `rsi_period` | 8 | RSI计算周期 (需要9天Z-score数据) |
| `rsi_short_spread_threshold` | 80 | 超买阈值 (SHORT_SPREAD入场检查) |
| `rsi_long_spread_threshold` | 20 | 超卖阈值 (LONG_SPREAD入场检查) |
| `rsi_lookback_for_extreme` | 3 | 查找"近期曾极端"的回溯窗口 |
| `rsi_warmup_days` | 10 | 预热天数 (从clean_data加载) |
| `momentum_rsi_threshold` | 40 | 动量止盈阈值 (空头<40强, 多头>60强) |

### 边界情况处理

| 场景 | 处理方式 |
|------|----------|
| clean_data 不可用 | 跳过预热，逐日积累 |
| RSI 数据不足 | 回退到原始入场逻辑 |
| Z-score 在区间但RSI不满足 | 返回 WAIT，等待条件满足 |
| 同周期平仓后重新开仓 | 历史数据保留，可立即判断 |
| 跨周期参数更新 (v8.20.1) | `update_params()` 清空+重新预热 |

---

# Part 12.5: 动量止盈 (Momentum Profit Taking) - v8.19.0

```
触发: get_signal() 中 |Z| < exit_threshold (进入出场区)
    ↓
=========================================
设计理念: "让利润奔跑"
=========================================
    ↓
传统模式: Z进入出场区 → 立即平仓
动量模式: Z进入出场区 → 检查动量 → 动量强则继续持有
    ↓
与阶梯止损配合:
├─ 动量止盈 (攻): 趋势强劲时延迟平仓
└─ 阶梯止损 (守): 保护已实现的盈利台阶
    ↓
=========================================
动量判断逻辑 (_should_hold_for_momentum)
=========================================
    ↓
条件 A (绝对动量): RSI 处于强势区域
    ├─ SHORT_SPREAD: RSI < 40 (空头主导)
    └─ LONG_SPREAD: RSI > 60 (多头主导)
    ↓
条件 B (相对动量): RSI 动量未衰竭
    ├─ SHORT_SPREAD: RSI ≤ Prev_RSI (下跌动能持续)
    └─ LONG_SPREAD: RSI ≥ Prev_RSI (上涨动能持续)
    ↓
决策:
├─ 条件A AND 条件B → HOLD (继续持有)
└─ 否则 → CLOSE (落袋为安)
```

### 场景模拟 (空头持仓)

假设做空 Spread，入场 Z=+3.0σ:

| Day | Z-score | RSI | Prev_RSI | 条件A (RSI<40) | 条件B (RSI≤Prev) | 决策 |
|-----|---------|-----|----------|----------------|------------------|------|
| 10 | +0.6 | - | - | - | - | HOLD (未到检查站) |
| 11 | +0.4 | 35 | 38 | ✓ | ✓ | **HOLD** (动量强) |
| 12 | -0.2 | 28 | 35 | ✓ | ✓ | **HOLD** (动量更强) |
| 13 | -0.8 | 25 | 28 | ✓ | ✓ | **HOLD** |
| 14 | -0.9 | 32 | 25 | ✓ | ✗ | **CLOSE** (动量衰竭) |

**收益对比**:
- 传统模式: 3.0 → +0.4 = 2.6σ 收益
- 动量模式: 3.0 → -0.9 = 3.9σ 收益 (+50%)

### 阶梯止损保护

如果 Day 12 的 Z 从 -0.2 反弹到 +1.1:
- best_step = 0 (Z 曾到达 0.4σ)
- stop_line = (0+1) × 0.5 + 0.5 = 1.0σ
- 1.1 > 1.0 → 触发阶梯止损
- 保住 3.0 - 1.1 = 1.9σ 的盈利

### 关键配置参数

| 参数 | 值 | 含义 |
|------|-----|------|
| `momentum_profit_enabled` | True | 动量止盈总开关 |
| `momentum_rsi_threshold` | 40 | RSI动量阈值 (空头<40, 多头>60) |

### RSI阈值设计说明

| 阈值 | 用途 | 严格度 | 原因 |
|------|------|--------|------|
| 80/20 | 入场检测 | 严格 | 需确认极端区域 + 回落/反弹 |
| 40/60 | 出场动量 | 宽松 | 只需确认趋势尚在 |

**为什么出场用40而非20?**
- 使用20/80会导致动量止盈极少触发
- 40/60是"势头还在"的合理阈值
- 即使判断失误，阶梯止损会截断损失

---

# Part 13: Normal Open (正常开仓) - v8.11.0 预期收益排序

```
触发: OnData() 优先级6
    ↓
=========================================
步骤1: 候选筛选
=========================================
    ↓
PairsManager.get_open_candidates_with_allocation(data):
    ↓
筛选条件 (AND逻辑):
├─ current_selected (本轮入选)
├─ get_signal() = LONG_SPREAD 或 SHORT_SPREAD (含RSI过滤)
├─ has_position() = False
├─ is_in_cooldown() = False
└─ is_pair_locked() = False
    ↓
=========================================
步骤2: 资金分配 (v8.10.0 统一15%)
=========================================
    ↓
分配逻辑:
├─ 动态基准: initial_available = MarginRemaining - FIXED_BUFFER
├─ 固定地板: min_threshold = INITIAL_CAPITAL × 15%
├─ 计划分配: planned = initial_available × 15%
└─ 实际分配: actual = max(planned, min_threshold)
    ↓
效果:
├─ 亏损时 (initial_available < INITIAL_CAPITAL): 使用地板值
└─ 盈利时 (initial_available > INITIAL_CAPITAL): 使用动态值
    ↓
=========================================
步骤3: 计算预期收益额 (v8.11.0)
=========================================
    ↓
公式:
├─ expected_return_pct = (|Z-score| - exit_threshold) / |Z-score|
└─ expected_profit = actual_allocated × expected_return_pct
    ↓
含义: 假设 spread 完全回归到出场点 (0.5σ) 的预期收益
    ↓
=========================================
步骤4: 按预期收益额排序
=========================================
    ↓
排序: 按 expected_profit 降序
├─ Z-score 偏离越大 → 回归空间越大
├─ 分配资金越多 → 收益放大
└─ 优先开仓预期收益最大的配对
    ↓
=========================================
步骤5: 执行开仓
=========================================
    ↓
对每个候选配对:
├─ 生成 OpenIntent (Beta对冲计算目标股数)
└─ OrderExecutor.execute_open(intent)
```

### 预期收益排序示例

| 配对 | Z-score | 分配额 | 预期收益率 | 预期收益额 | 排序 |
|------|---------|--------|-----------|-----------|------|
| A-B | 2.5 | $15,000 | (2.5-0.5)/2.5 = 80% | $12,000 | 1st |
| C-D | 2.0 | $15,000 | (2.0-0.5)/2.0 = 75% | $11,250 | 2nd |
| E-F | 2.2 | $12,000 | (2.2-0.5)/2.2 = 77% | $9,273 | 3rd |

### 关键配置参数

| 参数 | 值 | 含义 |
|------|-----|------|
| `entry_threshold` | 动态 | 自适应入场阈值 (v8.20.0: 由PairSelector计算) |
| `entry_threshold_floor` | 3.0σ | 阈值地板值 (v8.20.0) |
| `entry_threshold_upper` | 6.0σ | 入场Z-score上限 (v8.20.0: 扩展宽网) |
| `exit_threshold` | 0.5σ | 出场Z-score阈值 |
| `fixed_allocation_pct` | 15% | 统一分配比例 (v8.10.0) |
| `sort_by_expected_profit` | True | 启用预期收益排序 (v8.11.0) |

---

# Appendix: Class Reference (类参考手册)

## Class: Pairs

核心数据对象，封装了配对的统计参数、持仓状态及信号逻辑。

### 1. Attributes (属性)

#### 1.1 Identity & Metadata (身份与元数据)
| 属性 | 类型 | 描述 |
|------|------|------|
| `pair_id` | Tuple[str, str] | 配对唯一标识符 |
| `symbol1` / `symbol2` | Symbol | 两个股票的Symbol对象 |
| `industry_code` | str | 所属行业代码 |

#### 1.2 Model Parameters (模型参数)
| 属性 | 类型 | 描述 |
|------|------|------|
| `alpha_mean` | float | 截距项后验均值 |
| `beta_mean` | float | 协整系数后验均值 |
| `residual_mean` | float | 残差均值 |
| `residual_std` | float | 残差标准差 |

#### 1.3 Quality Metrics (质量指标)
| 属性 | 类型 | 描述 |
|------|------|------|
| `quality_score` | float | 配对综合质量评分 (0.0-1.0) |
| `half_life` | float | 均值回归半衰期 (天) |

#### 1.4 State Management (状态管理)
| 属性 | 类型 | 描述 |
|------|------|------|
| `position_mode` | PositionMode | 当前持仓状态 |
| `tracked_qty1` / `tracked_qty2` | int | 持仓数量追踪 |
| `entry_zscore` | float | 开仓时的Z-score |
| `entry_time` | datetime | 开仓时间 |
| `max_holding_days` | float | 理论最大持仓天数 |

#### 1.5 RSI on Z-score (动量检测 v8.7.0)
| 属性 | 类型 | 描述 |
|------|------|------|
| `zscore_history` | deque | Z-score历史队列 (maxlen=9) |
| `rsi_history` | deque | RSI历史队列 (maxlen=4) |

#### 1.6 Statistics (历史统计)
| 属性 | 类型 | 描述 |
|------|------|------|
| `trade_history` | List[Tuple] | 历史交易记录 (entry_time, exit_time, pnl, invested) |

### 2. Methods (方法)

#### 2.1 Core Logic (核心逻辑)
| 方法 | 返回值 | 描述 |
|------|--------|------|
| `get_zscore(price1, price2)` | float | 计算当前Z-score |
| `get_signal(data)` | str | 生成交易信号 (v8.7.0: 含RSI过滤) |
| `get_open_intent(amount, data)` | OpenIntent | 生成开仓意图 |
| `get_close_intent(reason)` | CloseIntent | 生成平仓意图 |

#### 2.2 RSI Methods (动量检测 v8.7.0)
| 方法 | 返回值 | 描述 |
|------|--------|------|
| `_warmup_zscore_history()` | None | 从clean_data预热Z-score历史 |
| `_calculate_rsi()` | float | 计算当前RSI并追加到历史 |
| `_check_rsi_entry_condition(signal)` | bool | 检查RSI入场条件 (三重AND) |

#### 2.3 Calculations (计算方法)
| 方法 | 返回值 | 描述 |
|------|--------|------|
| `get_pair_holding_days()` | int | 当前持仓天数 |
| `get_max_holding_days()` | float | 理论最大持仓天数 |
| `calculate_leg_values()` | Tuple | Beta对冲计算双腿目标金额 |
| `get_trade_count()` | int | 历史交易次数 |
| `get_win_count()` | int | 历史盈利次数 |

#### 2.4 State Queries (状态查询)
| 方法 | 返回值 | 描述 |
|------|--------|------|
| `has_position()` | bool | 是否有任何持仓 |
| `has_normal_position()` | bool | 是否有正常持仓 |
| `has_anomaly_position()` | bool | 是否有异常持仓 |
| `is_in_cooldown()` | bool | 是否在冷却期中 |

---

## Class: PairsManager

配对管理器，负责管理整个回测周期内所有配对的生命周期及资金分配。

### 1. Attributes (属性)
| 属性 | 类型 | 描述 |
|------|------|------|
| `all_pairs` | Dict[tuple, Pairs] | 所有已创建的配对对象 |
| `current_selected_pair_ids` | Set[tuple] | 当前选股周期入选的配对ID |
| `past_selected_pair_ids` | Set[tuple] | 历史曾入选但当前落选的配对ID |
| `INITIAL_CAPITAL` | float | 初始资本 (固定基准) |
| `FIXED_BUFFER` | float | 保证金缓冲 (2%初始资金) |

### 2. Methods (方法)

#### 2.1 Lifecycle Management (生命周期管理)
| 方法 | 返回值 | 描述 |
|------|--------|------|
| `classify_pairs(new_pairs_dict)` | None | 更新配对分类 |
| `get_pair_by_id(pair_id)` | Pairs | 根据ID获取配对 |
| `get_pairs_with_position()` | Dict | 获取所有持仓配对 |

#### 2.2 Capital Allocation (资金分配 v8.10.0-v8.11.0)
| 方法 | 返回值 | 描述 |
|------|--------|------|
| `get_open_candidates_with_allocation(data)` | List | 筛选开仓候选并分配资金 |
| `get_available_margin()` | float | 计算当前可用保证金 |
| `allocate_margin_to_candidates(candidates)` | Dict | 统一15%资金分配 |
| `_calculate_expected_profit(pair, allocated, data)` | float | 计算预期收益额 |

#### 2.3 Health & Risk (健康与风控)
| 方法 | 返回值 | 描述 |
|------|--------|------|
| `check_pairs_health()` | Dict | 执行所有配对健康检查 |
| `get_cooldown_required_days(reason, half_life)` | int | 动态计算冷却天数 (v8.13.0) |

---

## OnData 执行优先级汇总

| 优先级 | 检查项 | 触发条件 | 动作 |
|--------|--------|----------|------|
| 1 | Portfolio冷却期 | `is_in_portfolio_cooldown()` | 直接return |
| 2 | Portfolio回撤 | Drawdown ≥ 20% | Liquidate + 360天冷却 |
| 3 | 配对健康检查 | Anomaly/PairBreak/Timeout/Drawdown | 风控平仓 |
| 4 | 正常平仓 | CLOSE信号 | 均值回归平仓 |
| 5 | VIX检查 | VIX ≥ 35 | 禁止开仓 |
| 6 | 正常开仓 | 候选筛选+资金分配+预期收益排序 | 执行开仓 |
