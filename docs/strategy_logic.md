# Strategy Logic Documentation

本文档用流程图形式描述贝叶斯协整配对交易策略的完整执行逻辑。

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

### 时间窗口设计 (v8.4.0 - 消除数据窥探)

```
t=-312        t=-60         t=0 (当前)
  |______________|______________|
       252天            60天
         ↓                ↓
   协整检验期        参数估计期
   (EG Test)         (MCMC)
   样本内筛选        样本外估计
```

**设计原理**:
- **单一数据源**: `DataProcessorConfig` 定义 `total_lookback_days=312` + `bayesian_lookback_days=60`
- **派生计算**: 协整窗口 = 312 - 60 = 252天 (各模块自行计算)
- **数据隔离**: 协整检验和MCMC使用完全不重叠的时间段

```
触发: _run_analysis_pipeline() 步骤1
    ↓
=========================================
输入: UniverseSelection 输出的 ~300 只股票
=========================================
    ↓
步骤1: 批量下载历史数据
    └─ algorithm.History(symbols, 312, Resolution.Daily)  ← v8.4.0: 312天
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
    └─ len(data) == 312 (v8.4.0: 恰好312个交易日)
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
    'clean_data': {symbol: DataFrame},  ← 每只股票312天OHLCV (v8.4.0)
    'valid_symbols': [Symbol],          ← 通过验证的股票列表
    'statistics': {...}                 ← 统计信息
}
    ↓
下游: CointegrationAnalyzer.cointegration_procedure()
```

### 关键配置参数

| 参数 | 值 | 含义 |
|------|-----|------|
| `total_lookback_days` | 312 | **交易日** - 总数据下载量 (v8.4.0) |
| `bayesian_lookback_days` | 60 | **交易日** - MCMC建模窗口 |
| `data_completeness_ratio` | 1.0 | 100%数据完整性要求 |
| `max_annualized_volatility` | 0.7 | 年化波动率上限70% |
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
输出
=========================================
    ↓
{
    'pairs': [                          ← 所有通过检验的配对
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
下游: IndustryQuotaManager.apply_quotas()
```

### 关键配置参数

| 参数 | 值 | 含义 |
|------|-----|------|
| `pvalue_threshold` | 0.01 | 协整显著性水平 (1%) |
| `min_stocks_per_industry` | 10 | 行业内最少股票数 |
| `max_stocks_per_industry` | 50 | 行业内最多股票数 |
| `max_symbol_repeats` | 2 | 单股最多参与配对数 |

---

# Part 4: Industry Quota (行业配额)

```
触发: _run_analysis_pipeline() 步骤3
    ↓
=========================================
输入: CointegrationAnalyzer 输出的协整配对列表
=========================================
    ↓
步骤1: 预热期检查
    ├─ 条件: days_running < 90 (warmup_days)
    ├─ 预热期行为: 所有行业 CS=0 → 权重=1 → 均等分配
    └─ 正常期: 继续计算动态配额
    ↓
步骤2: 计算行业综合得分 (Composite Score)
    ├─ 公式: CS = Rolling_ROI × Rolling_WinRate
    ├─ 滚动窗口: 最近90天 (rolling_window_days)
    ├─ 样本保底: 若窗口内<20笔, 取最近20笔
    └─ 取值范围: [0, +∞) (ROI<0时返回0)
    ↓
步骤3: 计算行业权重
    └─ weight = ceil(exp(6 × CS))
        ├─ CS=0: weight=1 (预热期/无数据/亏损)
        └─ CS>0: 指数级增长 (奖励盈利行业)
    ↓
步骤4: 集中度熔断检查
    ├─ 条件: industry_concentration > 40%
    └─ 触发: 该行业配额直接降为0
    ↓
=========================================
步骤5: 两轮分配机制
=========================================
    ↓
第一轮 (Base Pass):
    ├─ quota = floor(total_quota × weight / total_weight)
    ├─ selected = min(quota, demand)
    ├─ unused = quota - selected     ← 退回池
    └─ unmet = demand - selected     ← 等待bonus
    ↓
第二轮 (Redistribution):
    ├─ bonus = floor(quota_pool × weight / hungry_weight_sum)
    └─ actual_bonus = min(bonus, unmet, remaining_pool)
    ↓
=========================================
输出
=========================================
    ↓
按配额筛选后的配对列表 (随机抽取, hash种子保证可复现)
    ↓
下游: 构建 PairData 字典
```

### 关键配置参数

| 参数 | 值 | 含义 |
|------|-----|------|
| `total_quota` | 20 | 全局配额总量 |
| `warmup_days` | 90 | 预热期天数 (日历天) |
| `exp_scale_factor` | 6.0 | 指数缩放系数 |
| `min_quota_per_industry` | 1 | 单行业最低配额 |
| `rolling_window_days` | 90 | CS计算滚动窗口 (日历天) |
| `min_samples_for_window` | 20 | 样本量保底 |
| `concentration_threshold` | 40% | 集中度熔断阈值 |

---

# Part 5: PairData Construction (数据封装)

```
触发: _run_analysis_pipeline() 步骤4
    ↓
=========================================
输入: 配额筛选后的配对列表 + clean_data
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

# Part 6: Bayesian Modeler (贝叶斯建模)

```
触发: _run_analysis_pipeline() 步骤5
    ↓
=========================================
输入: PairData 对象字典
=========================================
    ↓
步骤1: 先验选择 (二级先验体系)
    ├─ Level 1 (Informed): 存在有效历史后验 (30天内)
    │   ├─ α, β: Normal (均值/方差源自历史后验)
    │   ├─ ρ: Beta (矩匹配拟合)
    │   └─ σ_η: HalfNormal (放大系数2.5)
    │
    └─ Level 2 (Uninformed): 无历史记录
        ├─ α: Normal(0, 10)
        ├─ β: Normal(0, 5)
        ├─ ρ: Beta(2, 2)
        └─ σ_η: HalfNormal(0.1)
    ↓
步骤2: 数据窗口截取
    └─ 使用最近 60 个交易日 (bayesian_lookback_days)
    ↓
步骤3: 联合贝叶斯建模 (AR(1) 变换形式)
    ├─ 模型: y_t = α(1-ρ) + β(x_t - ρx_{t-1}) + ρy_{t-1} + η_t
    └─ MCMC: PyMC (4 chains × 3000 draws, 2000 warmup)
    ↓
=========================================
输出: ModelResult 对象
=========================================
    ↓
├─ 后验均值: alpha_mean, beta_mean, rho_mean, sigma_mean
├─ 后验标准差: alpha_std, beta_std, rho_std, sigma_std
├─ 衍生指标: half_life = -ln(2) / ln(ρ)
└─ 元数据: modeling_type (informed/uninformed)
    ↓
下游: PairSelector.selection_procedure()
```

### 关键配置参数

| 参数 | 值 | 含义 |
|------|-----|------|
| `bayesian_lookback_days` | 60 | 建模数据窗口 (交易日) |
| `mcmc_chains` | 4 | MCMC链数 |
| `mcmc_warmup` | 2000 | 预热采样次数 |
| `mcmc_draws` | 3000 | 正式采样次数 |
| `informed_validity_days` | 30 | 历史后验有效期 |

---

# Part 7: Pair Selector (配对筛选)

```
触发: _run_analysis_pipeline() 步骤6
    ↓
=========================================
输入: ModelResult 对象列表
=========================================
    ↓
步骤1: 三维质量评分
    ↓
维度1: Half-life (25%)
    ├─ 定义: 均值回归一半所需时间
    ├─ 公式: -ln(2) / ln(ρ)
    ├─ 评分: 非对称高斯分布
    │   ├─ 峰值: 10天
    │   ├─ 核心区间: 5-15天
    │   └─ 惩罚: <4天或>25天
    ↓
维度2: Mean-reversion Certainty (40%)
    ├─ 定义: 均值回归强度的信噪比
    ├─ 指标: SNR_κ = Mean(κ) / Std(κ)
    └─ 评分: Logistic S曲线
    ↓
维度3: Zero-crossing (35%)
    ├─ 定义: Spread穿越均值的次数
    ├─ 评分: 分段线性函数
    │   ├─ 峰值: 12次/年
    │   └─ 合理区间: 6-36次
    ↓
步骤2: 质量评分计算
    └─ quality_score = Σ(weight_i × score_i)
    ↓
步骤3: 门槛过滤
    └─ quality_score ≥ 0.50
    ↓
步骤4: ROI缩放 (v8.0.24)
    ├─ 条件: 存在历史交易记录
    └─ scaled_score = quality_score × (1 + pair_roi)
    ↓
=========================================
输出: 最终入选配对列表 (按scaled_score降序)
=========================================
    ↓
下游: 创建 Pairs 对象
```

### 关键配置参数

| 参数 | 值 | 含义 |
|------|-----|------|
| `min_quality_threshold` | 0.50 | 最低质量分数阈值 |
| `half_life权重` | 25% | 半衰期评分权重 |
| `mean_reversion_certainty权重` | 40% | MR确定性评分权重 |
| `zero_crossing权重` | 35% | 零轴穿越评分权重 |

---

# Part 8: Pairs Creation & Management (配对创建与管理)

```
触发: _run_analysis_pipeline() 步骤7-8
    ↓
=========================================
步骤7: Pairs对象创建
=========================================
    ↓
工厂方法: Pairs.from_model_result(algorithm, model_result, config)
    ↓
初始化内容:
├─ 基础信息: symbol1, symbol2, industry_code
├─ 贝叶斯参数: α, β, μ_res, σ_res
├─ 质量指标: quality_score, half_life
└─ 交易阈值: entry[1.25-1.65σ], exit[0.2σ]
    ↓
=========================================
步骤8: PairsManager分类管理
=========================================
    ↓
classify_pairs(new_pairs_dict):
    ↓
对每个配对:
    ├─ 已存在 + 无持仓 → update_params() 更新参数
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

---

# Part 9: Portfolio Risk Management (组合级风控)

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

# Part 10: Pair-level Health Check (配对级风控)

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
    └─ 冷却期: 永久 (999999天)
    ↓
优先级2: PairBreak (协整破裂)
    ├─ 定义: Z-score向不利方向突破1.95σ
    ├─ 方向感知:
    │   ├─ 多头: Z < -1.95σ 触发
    │   └─ 空头: Z > +1.95σ 触发
    ├─ 盈利跳过: unrealized_pnl > 0 时不触发 (v8.2.5)
    └─ 冷却期: 30天
    ↓
优先级3: Drift (对冲漂移)
    ├─ 定义: Drift = NetExposure / GrossExposure
    ├─ 阈值: 40%
    ├─ 盈利跳过: unrealized_pnl > 0 时不触发 (v8.2.5)
    └─ 冷却期: 30天
    ↓
优先级4: Timeout (持仓超时)
    ├─ 定义: holding_days > max_holding_days
    ├─ max_holding_days = half_life × log₀.₅(exit/entry_zscore)
    ├─ 盈利跳过: unrealized_pnl > 0 时不触发 (v8.2.5)
    └─ 冷却期: 30天
    ↓
优先级5: Drawdown (单体回撤)
    ├─ 定义: (HWM - CurrentValue) / HWM
    ├─ 阈值: 4% (亏损配对) / 8% (盈利配对)
    └─ 冷却期: 30天
    ↓
=========================================
执行: main.py 遍历问题字典
=========================================
    ↓
对每个问题配对:
├─ 生成 CloseIntent (reason=问题类型)
└─ OrderExecutor.execute_close(intent)
```

### 关键配置参数

| 参数 | 值 | 含义 |
|------|-----|------|
| `pair_break_threshold` | 1.95σ | 协整破裂Z-score阈值 |
| `drift_threshold` | 40% | 对冲漂移阈值 |
| `drawdown_threshold` | 4% | 单体回撤阈值 (亏损配对) |
| `drawdown_threshold` × 1.5 | 8% | 单体回撤阈值 (盈利配对) |

---

# Part 11: Normal Close (正常平仓)

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
        ├─ 条件: |Z-score| < 0.2σ (exit_threshold)
        ├─ 含义: 价差已回归均值附近
        ├─ 动作: CloseIntent (reason='MEAN_REVERSION')
        └─ 冷却期: 7天
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

# Part 12: VIX Check (开仓安全检查)

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

# Part 13: Normal Open (正常开仓)

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
├─ get_signal() = LONG_SPREAD 或 SHORT_SPREAD
├─ has_position() = False
├─ is_in_cooldown() = False
└─ is_pair_locked() = False
    ↓
=========================================
步骤2: 排序与分配
=========================================
    ↓
排序: 按 avg_return_per_trade 降序
    ↓
资金分配 (allocation_tiers):
├─ avg_return ≤ 0%   → 10%
├─ avg_return ≤ 10%  → 15%
├─ avg_return ≤ 20%  → 18%
├─ avg_return ≤ 25%  → 20%
└─ avg_return > 25%  → 25% (max)
├─ 无交易记录 → 10% (default)
    ↓
门槛检查: allocated < min_threshold → 放弃开仓
    ↓
=========================================
步骤3: 执行开仓
=========================================
    ↓
对每个候选配对:
├─ 生成 OpenIntent (Beta对冲计算目标股数)
└─ OrderExecutor.execute_open(intent)
```

### 关键配置参数

| 参数 | 值 | 含义 |
|------|-----|------|
| `entry_threshold_lower` | 1.25σ | 入场Z-score下限 |
| `entry_threshold_upper` | 1.65σ | 入场Z-score上限 |
| `allocation_default` | 10% | 无交易记录时的分配比例 |
| `allocation_max` | 25% | 最大分配比例 |
| `min_investment_ratio` | 10% | 最低投资比例 |

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
| `alpha` | float | 截距项 |
| `beta` | float | 协整系数 |
| `rho` | float | 自回归系数 |
| `sigma_eta` | float | 残差标准差 |

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

#### 1.5 Statistics (历史统计)
| 属性 | 类型 | 描述 |
|------|------|------|
| `trade_history` | List[Tuple] | 历史交易记录 (entry_time, exit_time, pnl, invested) |
| `trade_count` | int | 累计交易次数 |
| `win_count` | int | 累计盈利次数 |

### 2. Methods (方法)

#### 2.1 Core Logic (核心逻辑)
| 方法 | 返回值 | 描述 |
|------|--------|------|
| `get_zscore(price1, price2)` | float | 计算当前Z-score |
| `get_signal(data)` | str | 生成交易信号 |
| `get_open_intent(amount, data)` | OpenIntent | 生成开仓意图 |
| `get_close_intent(reason)` | CloseIntent | 生成平仓意图 |

#### 2.2 Calculations (计算方法)
| 方法 | 返回值 | 描述 |
|------|--------|------|
| `get_pair_holding_days()` | int | 当前持仓天数 |
| `get_max_holding_days()` | float | 理论最大持仓天数 |
| `get_pair_drawdown()` | float | 当前浮动回撤率 |
| `get_hedge_drift()` | float | 对冲漂移率 |
| `get_pair_roi()` | float | 历史累计ROI |
| `get_avg_return_per_trade()` | float | 平均每笔交易回报率 |

#### 2.3 State Queries (状态查询)
| 方法 | 返回值 | 描述 |
|------|--------|------|
| `has_position()` | bool | 是否有任何持仓 |
| `has_normal_position()` | bool | 是否有正常持仓 |
| `has_anomaly_position()` | bool | 是否有异常持仓 |
| `is_in_cooldown()` | bool | 是否在冷却期中 |

---

## Class: PairsManager

配对管理器，负责管理整个回测周期内所有配对的生命周期、行业数据聚合及资金分配。

### 1. Attributes (属性)
| 属性 | 类型 | 描述 |
|------|------|------|
| `all_pairs` | Dict[tuple, Pairs] | 所有已创建的配对对象 |
| `current_selected_pair_ids` | Set[tuple] | 当前选股周期入选的配对ID |
| `past_selected_pair_ids` | Set[tuple] | 历史曾入选但当前落选的配对ID |

### 2. Methods (方法)

#### 2.1 Lifecycle Management (生命周期管理)
| 方法 | 返回值 | 描述 |
|------|--------|------|
| `classify_pairs(new_pairs_dict)` | None | 更新配对分类 |
| `get_pair_by_id(pair_id)` | Pairs | 根据ID获取配对 |
| `get_pairs_with_position()` | Dict | 获取所有持仓配对 |

#### 2.2 Industry Analytics (行业分析)
| 方法 | 返回值 | 描述 |
|------|--------|------|
| `get_industry_composite_score(code)` | float | 行业综合得分 |
| `get_industry_roi(code)` | float | 行业历史累积ROI |
| `get_industry_win_rate(code)` | float | 行业胜率 |
| `get_industry_concentration(code)` | float | 行业资金集中度 |

#### 2.3 Capital Allocation (资金分配)
| 方法 | 返回值 | 描述 |
|------|--------|------|
| `get_open_candidates_with_allocation(data)` | List | 筛选开仓候选并分配资金 |
| `get_available_margin()` | float | 计算当前可用保证金 |

#### 2.4 Health & Risk (健康与风控)
| 方法 | 返回值 | 描述 |
|------|--------|------|
| `check_pairs_health()` | Dict | 执行所有配对健康检查 |
| `get_cooldown_required_days(reason)` | int | 查询冷却天数 |

---

## Class: IndustryData

行业数据对象 (Value Object)，封装单个行业的聚合统计数据。

### Attributes (属性)
| 属性 | 类型 | 描述 |
|------|------|------|
| `historical_pnl` | float | 历史累积盈亏 |
| `current_invested_capital` | float | 当前投入资本 |
| `historical_invested_capital` | float | 历史累积投入资本 |
| `trade_count` | int | 交易次数 |
| `win_count` | int | 盈利次数 |
| `trade_history` | List[Tuple] | 单笔交易记录列表 |
