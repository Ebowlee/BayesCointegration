# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Codebase Overview

This is a **Bayesian Cointegration** pairs trading strategy built for the QuantConnect platform. The strategy uses advanced statistical methods including Bayesian inference with MCMC sampling to identify and trade mean-reverting relationships between securities within the same industry sectors.

## Architecture Pattern (v7.0.0 OnData + Intent Pattern)

The strategy uses **OnData-driven architecture** with **Intent Pattern** (migrated from Algorithm Framework in v6.0.0, Intent Pattern introduced in v7.0.0):

- **main.py**: Central orchestrator using `BayesianCointegrationStrategy(QCAlgorithm)` with OnData event handling
- **UniverseSelection**: Multi-stage fundamental screening with sector-based selection
- **Pairs**: Core pair trading object encapsulating signal generation and intent generation
- **OrderExecutor**: Unified order execution engine (separates intent from execution)
- **OrderIntent**: Intent value objects (OpenIntent, CloseIntent) for trade intentions
- **PairsManager**: Lifecycle manager for all pairs (active, legacy, dormant states)
- **risk**: Two-tier risk control system with separation of concerns (detection vs execution)
- **Analysis modules**: DataProcessor, CointegrationAnalyzer, BayesianModeler, PairSelector

Key architectural principles (v7.0.0):
- **OnData-driven** - All trading logic flows through OnData method, no Framework modules
- **Intent-based execution** - Intent generation (Pairs) separated from order execution (OrderExecutor)
- **Object-oriented pairs** - Pairs class encapsulates all pair-specific logic and intent generation
- **Separation of concerns** - Risk managers detect risks, main.py coordinates execution
- **Smart lifecycle management** - PairsManager tracks pairs through active/legacy/dormant states
- **Order lifecycle tracking** - TicketsManager prevents duplicate orders via order locking mechanism
- **Margin-based allocation** - Position sizing uses margin requirements (50% long, 150% short)
- **Intra-industry pairing** - Securities paired within same Morningstar industry group (动态分组,实际约18-20个)
- **Natural fund constraints** - Position limits determined by available capital, not hard caps

## Communication Patterns

### Execution Flow Diagram (流程图)

**Trigger Phrase**: When the user says "请用【流程图】沟通", use the Execution Flow Diagram format.

**Purpose**: Provide high-level architectural communication showing data flow, decision points, and system interactions.

**Format Example**:
```
执行流程:
OnSecuritiesChanged (月度触发)
    ↓
步骤1: DataProcessor.process()
    ↓
步骤2: CointegrationAnalyzer.analyze()
    ⭐ 行业配额应用点 (v7.12.0)
    [内部流程]:
        - 遍历行业分组
        - 按pvalue排序
        - 应用配额限制 ← 关键过滤点
        - 应用单股重复限制 (v7.31.0)
    ↓
步骤3: BayesianModeler.model()
    ↓
步骤4: PairSelector.select()
    ⭐ 三维度阈值筛选 (v8.9.1)
    [内部流程]:
        - CV BETA 筛选 (≤ 0.2)
        - 半衰期筛选 ([5, 20] 天)
        - 零轴穿越筛选 ([6, 30] 次)
    ↓
输出: 高质量配对列表 → PairsManager.classify_pairs()
```

**Key Elements**:
- **Vertical Timeline**: Data flows from top to bottom
- **⭐ Markers**: Critical decision or processing points
- **[内部流程]**: Indented breakdown of internal steps
- **← Annotations**: Important filter points or architectural notes
- **Version Tags**: (v7.X.X) to show when features were added

**Benefits**:
- **全局视角**: Shows complete system flow in one view
- **问题定位**: Quickly identifies where logic operates
- **影响分析**: Reveals downstream dependencies
- **沟通高效**: Reduces back-and-forth clarification questions

**When to Use**:
- Discussing architectural changes or refactoring
- Debugging multi-module interaction issues
- Planning feature implementation across layers
- Explaining system behavior to new developers

## Log Design Principles

**Purpose**: Logs are for backtest-analyst agent (AI) forensic analysis, not human debugging.

**File Ecosystem**:
- **overview.json**: Strategy-level metrics (Sharpe, drawdown, total return)
- **orders.csv**: Order execution details (time, symbol, price, quantity, status)
- **logs.txt**: Reasoning context and state transitions (for AI inference)

**Design Goals**:
- Maximize AI-parseable value within 100KB limit
- Provide decision context that overview.json/orders.csv cannot capture
- Enable forensic analysis of strategy behavior

**Log Priority Tiers**:
1. **Critical**: Trade close events (JSON with PnL, holding days, exit reason), risk trigger details with numerical values
2. **Important**: Entry conditions (entry_zscore), market context (VIX), state transitions
3. **Optional**: Redundant summaries derivable from orders.csv, verbose batch progress messages

## Development Commands

### **IMPORTANT: Backtest Workflow**
**用户负责运行回测,Claude负责分析结果**

1. **用户执行回测**:
   - 用户自己运行本地或云端回测
   - 回测完成后,用户提供回测ID给Claude

2. **Claude分析结果**:
   - Claude使用backtest-analyst agent分析回测结果
   - 或使用QuantConnect MCP工具读取回测数据
   - **禁止**: Claude不应主动运行 `lean backtest` 命令

### Local Development
```bash
# 用户自行运行本地回测
lean backtest BayesCointegration

# Deploy to QuantConnect cloud
lean cloud push --project BayesCointegration

# View backtest results
lean report

# Check LEAN CLI help
lean backtest --help
```

### Cloud Operations
```bash
# Validate configuration before backtest
lean config validate

# Check project structure
lean project-create --list

# 用户自行运行云端回测
lean cloud backtest BayesCointegration

# Download backtest results (creates three files: .json, _logs.txt, _trades.csv)
lean cloud backtest results <backtest-id> --destination ./backtests/

# View cloud backtest status
lean cloud status
```

### Version Control
```bash
# Commit format: v<major>.<minor>.<patch>[_description][@YYYYMMDD]
git commit -m "v7.2.5_optimize-mcmc-sampling@20251026"  # 示例:今天日期

# After each commit (MANDATORY步骤):
# 1. Update docs/CHANGELOG.md with version entry
## [v7.2.5_optimize-mcmc-sampling@20251026]
- Description of changes
- Breaking changes (if any)
- Code examples for significant changes

# 2. Stage and commit CHANGELOG.md
git add docs/CHANGELOG.md
git commit -m "docs: update CHANGELOG for v7.2.5"

# Branching Strategy (if using):
# - main: Production-ready code
# - feature/*: New features under development
# - hotfix/*: Emergency bug fixes
```

**Why This Matters**:
- Ensures CHANGELOG.md stays synchronized with git history
- Prevents forgotten documentation updates
- Clarifies mandatory workflow steps

### Plan File Management

**规范**: 已完成的任务必须及时从计划文件中清理

1. **任务完成后**: 立即将任务从"待修复"移至"已完成"区域
2. **计划文件更新**: 每次实施完成后更新计划文件状态
3. **避免重复显示**: 已实施的方案不应在待办区域重复出现
4. **简洁为主**: 已完成任务只保留版本号和简要描述

**示例**:
- ✅ 正确: 任务完成 → 移至"已完成"表格 → 删除"待实施"详情
- ❌ 错误: 任务完成但详情仍在"待实施"区域展示

## Performance Monitoring (v7.29.2+)

### Key Metrics

**Capital Efficiency**:
- **Margin Utilization**: 保证金占用 / 初始资金 (Target: 20-40%)
- **Position Fill Rate**: 已开仓配对 / 可交易配对 (Target: 50-70%)
- **Idle Capital Diagnostic**: 诊断低开仓率原因 (资金不足, 信号缺失, 冷却期)

**Pair Creation Funnel**:
- **Cointegration Pass Rate**: 通过协整检验 / 候选配对
- **Quality Filter Rate**: 通过质量筛选 / 协整配对
- **Risk Filter Rate**: 通过风险过滤 / 质量配对
- **Final Creation Rate**: 最终创建 / 全部候选

**Valuation Screening** (v7.29.2):
- **Pass Rate**: 通过估值筛选 / 输入股票
- **Failure Breakdown**: PE失败数, PS失败数, 两者均失败数
- **OR Logic Benefit**: 记录OR逻辑挽救的股票数量

### Diagnostic Logs (Level 1)

```python
# 资金效率诊断示例
[资金效率] 可交易配对3对 → 已开仓1对 (33.3%) → 保证金占用$3,500 (3.5%)
[配对漏斗] 候选500对 → 协整476对 (95.2%) → 质量120对 (25.2%) → 最终3对 (0.6%)

# 估值筛选统计示例
[估值筛选] 输入500只 → 通过476只 (95.2%) → 失败24只 (4.8%)
  ├─ PE失败: 10只
  ├─ PS失败: 8只
  └─ 两者均失败: 6只
[OR逻辑] 挽救14只高成长股 (PE失败但PS≤10)
```

### Monitoring Guidelines

**Daily Checks** (回测后):
- 资金效率 < 10%: 检查信号生成逻辑或增加配对数量
- 开仓率 < 30%: 检查冷却期配置或风险规则过严
- 协整通过率 < 50%: 检查选股质量或协整参数

**Monthly Reviews**:
- 配对创建漏斗趋势: 识别瓶颈阶段 (协整/质量/风险)
- 行业配额效果: 高收益行业是否获得更多配额
- 估值筛选效果: OR逻辑挽救率是否合理 (Target: 10-20%)

## Core Module Architecture

### 1. main.py - Strategy Orchestrator (v8.0.0)
- **Purpose**: Central orchestration via OnData event handling
- **Key Components**:
  - `BayesianCointegrationStrategy`: Main algorithm class
  - `OnSecuritiesChanged()`: Triggers `_run_analysis_pipeline()`
  - `_run_analysis_pipeline()`: 7-step analysis pipeline (v8.9.0)
  - `OnData()`: 6-priority trading execution
  - `OnOrderEvent()`: Route to TicketsManager (v7.99.4 fix)
- **Analysis Pipeline** (`_run_analysis_pipeline`):
  1. DataProcessor.process() - Data cleaning
  2. CointegrationAnalyzer.cointegration_procedure() - Cointegration test
  3. Build PairData dictionary
  4. BayesianModeler.modeling_procedure() - Bayesian modeling
  5. PairSelector.selection_procedure() - Quality selection
  6. Create Pairs objects
  7. PairsManager.classify_pairs() - Classification management
- **OnData 6-Priority Execution**:
  1. Portfolio cooldown check → return if active
  2. Portfolio drawdown check → trigger Liquidate if ≥20%
  3. Pair health check (PairsManager.check_pairs_health) → risk close
  4. Normal close (CLOSE/PAIR_BREAK signals) → Intent Pattern
  5. VIX safety check → return if unsafe
  6. Normal open (get_open_candidates_with_allocation) → Intent Pattern
- **Configuration**: All parameters in `src/config.py` via `StrategyConfig` class

### 2. Pairs.py - Pair Trading Object
- **Purpose**: Encapsulates all pair-specific logic (data provider, signal generator, intent generator, trade history tracker)
- **Design Principle** (v7.0.0 → v7.7.0): "Data Provider + Intent Generator + Trade History Owner"
  - ✅ **Provides**: PnL calculation, position data, holding time, signal generation, intent generation, trade statistics (v7.7.0)
  - ❌ **Does NOT**: Risk checking, HWM tracking, drawdown calculation, order execution
  - **Removed** (v6.9.4): `check_position_integrity()` (unused), `get_pair_drawdown()` (moved to PairDrawdownRule), `pair_hwm` attribute
  - **Removed** (v7.0.0): `open_position()`, `close_position()` (replaced by get_*_intent + OrderExecutor)
  - **Added** (v7.7.0 → v8.1.0): Trade statistics via `trade_history` four-tuple + dynamic aggregation methods
  - **Added** (v7.12.0): industry_code field for industry quota management
- **Creation Pattern** (v6.9.2):
  - **Recommended**: Use classmethod factory `Pairs.from_model_result(algorithm, model_result, config)`
  - **Avoid**: Direct constructor `Pairs(algorithm, model_result, config)`
  - **Consistency**: Follows same pattern as `PairData.from_clean_data()` and `TradeSnapshot.from_pair()`
- **Key Methods**:
  - `get_signal()`: Generate trading signals (LONG_SPREAD, SHORT_SPREAD, CLOSE, STOP_LOSS, HOLD)
  - `get_zscore()`: Calculate current Z-score
  - `get_open_intent()`: Generate opening intent (returns OpenIntent object - v7.0.0)
  - `get_close_intent()`: Generate closing intent (returns CloseIntent object - v7.0.0)
  - `position_mode`: Property for querying position status (v7.40.11 - direct implementation without intermediate method)
  - `get_pair_pnl()`: Calculate PnL in two modes: real-time (持仓中) or final (已平仓) - v7.0.0
  - `get_pair_cost()`: Calculate total margin required for the pair
  - `get_pair_holding_days()`: Calculate holding days (data query for PairHoldingTimeoutRule)
  - `is_in_cooldown()`: Check cooldown period (part of signal generation logic)
  - `on_position_filled()`: Callback when position fills - clears tracking variables and updates trade stats (v7.7.0)
  - `_update_trade_stats()`: Private method - calculates trade PnL% and updates statistics (v7.7.0)
- **Trade Statistics** (v8.1.0 - Single Source of Truth):
  - `trade_history: List[Tuple[datetime, datetime, float, float]]`: Full history storage
    - **Four-tuple format**: `(entry_time, exit_time, pnl, invested_capital)`
    - **No cleanup**: Full lifecycle data retained for 20-year backtests
  - **Dynamic Aggregation Methods** (O(n) per query, acceptable for low-frequency trading):
    - `get_trade_count()`: `len(trade_history)`
    - `get_win_count()`: Count of trades with pnl > 0
    - `get_total_pnl()`: Sum of all trade PnLs
    - `get_total_invested_capital()`: Sum of all invested capitals
    - `get_avg_holding_days()`: Average holding period in days
    - `get_win_rate()`: `win_count / trade_count`
    - `get_pair_roi()`: `total_pnl / total_invested_capital`
    - `get_avg_return_per_trade()`: `roi / trade_count`
  - **Data Layer Separation**:
    - **Real-time layer**: `tracked_qty` + real-time prices (for drawdown, unrealized PnL)
    - **Historical layer**: `trade_history` (for ROI, win rate, holding days)
  - **Auto-update**: `_update_trade_stats()` appends four-tuple on position close
- **Features**: Cooldown management, beta hedging, position tracking, intent generation, trade history (v7.7.0)
- **Architecture** (v7.40.0): Six-Layer Hamburger Structure (六层汉堡结构)
  - **Design Philosophy**: Code organized from "abstract to concrete, core computation to side effects"
  - **Layer 1 (构造与配置)**: `__init__()` - Object initialization (v7.45.0: tier管理迁移至PairsManager)
  - **Layer 2 (状态查询)**: `get_price()`, `position_mode` (property), `get_pair_cost()` - Data queries (v7.40.11 - direct property access)
  - **Layer 3 (核心算力)**: `get_hedge_drift()`, `calculate_leg_values()` - Pure math calculations (Beta hedging)
  - **Layer 4 (金融指标)**: `get_accum_return_pct()`, `get_pair_holding_days()` - Financial metrics
  - **Layer 5 (决策与意图)**: `get_zscore()`, `get_signal()`, `get_open_intent()` - Trading decisions
  - **Layer 6 (生命周期回调)**: `on_position_filled()`, `_update_trade_stats()` - External callbacks (side effects)
  - **Symmetry**: Layer 1 ↔ Layer 6 (construction ↔ callbacks), Layer 3 ↔ Layer 4 (pure math ↔ domain logic)

### 3. OrderExecutor.py - Order Execution Engine (v7.0.0)
- **Purpose**: Unified order execution engine (separates intent from execution)
- **Design Principle**: "Execute Intent, Not Business Logic"
  - ✅ **Responsible for**: Submitting MarketOrder via QuantConnect API
  - ❌ **NOT responsible for**: Signal generation, risk checking, position tracking
- **Key Methods**:
  - `execute_open(intent: OpenIntent)`: Execute opening orders, returns List[OrderTicket]
  - `execute_close(intent: CloseIntent)`: Execute closing orders, returns List[OrderTicket]
- **Error Handling**: Logs failed orders, returns None for failed legs
- **Benefits**:
  - Centralized order submission logic
  - Pairs module independent of QuantConnect order API
  - Easy to mock for testing

### 4. OrderIntent.py - Intent Value Objects (v7.0.0)
- **Purpose**: Encapsulate trade intentions as immutable data objects
- **Design Principle**: Value Objects (immutable, no business logic)
- **OpenIntent**:
  - Fields: `pair_id`, `symbol1`, `symbol2`, `qty1`, `qty2`, `signal`, `tag`
  - Usage: Generated by `Pairs.get_open_intent()`, consumed by `OrderExecutor.execute_open()`
- **CloseIntent**:
  - Fields: `pair_id`, `symbol1`, `symbol2`, `qty1`, `qty2`, `reason`, `tag`
  - Usage: Generated by `Pairs.get_close_intent()`, consumed by `OrderExecutor.execute_close()`
- **Benefits**:
  - Testable without QuantConnect (pure data objects)
  - Serializable for logging/debugging
  - Clear separation of intent and execution

### 5. PairsManager.py - Lifecycle + Margin + Health Management (v8.0.0)
- **Purpose**: Manage all pairs lifecycle, margin allocation, and health monitoring
- **Design Principle** (v8.0.0): "Storage + Margin Allocation + Health Check" unified manager
  - **Responsible for**: Storing pairs, state classification, margin allocation, health checks, industry metrics
  - **NOT responsible for**: Order execution (delegated to OrderExecutor), VIX/Portfolio-level risk (RiskManager)
- **Key Attributes**:
  - `all_pairs: Dict[pair_id, Pairs]`: Primary storage
  - `current_selected_pair_ids: Set`: Pairs selected this month
  - `past_selected_pair_ids: Set`: Historical pairs (previously selected, not this month)
  - `INITIAL_CAPITAL: float`: Fixed initial capital baseline
  - `FIXED_BUFFER: float`: Reserved margin buffer
- **Key Methods** (v8.0.0 updated):
  - *Lifecycle*:
    - `classify_pairs(new_pairs_dict)`: Incremental index update, classify into current/past
    - `get_pair_by_id()`, `get_pairs_with_position()`, `get_pairs_without_position()`
  - *Margin Allocation* (v8.10.0: 统一15%分配):
    - `get_available_margin()`: MarginRemaining - FIXED_BUFFER
    - `allocate_margin_to_candidates(open_candidates)`: Distribute margin based on fixed 15%
    - `_calculate_expected_profit(pair, allocated, data)`: Calculate expected profit for sorting (v8.11.0)
    - `get_open_candidates_with_allocation(data)`: Get candidates with margin allocation
  - *Health Check* (v8.15.0: 移除β漂移，简化PairBreak):
    - `check_pairs_health()`: Returns Dict[issue_type, pair_ids] with 4-priority check
      - Priority 1: anomaly (single-leg/same-direction positions)
      - Priority 2: pair_break (Z-score 3.5σ 方向感知检测)
      - Priority 3: timeout (holding days > max theoretical)
      - Priority 4: drawdown (>10%)
  - *Industry Metrics* (v8.0.0 rolling window):
    - `get_industry_composite_score(industry_code)`: rolling_roi × rolling_win_rate
    - `get_industry_realized_roi(industry_code, window_days)`: ROI with rolling window
    - `get_industry_win_rate(industry_code, window_days)`: Win rate with rolling window
    - `_aggregate_all_industry_data()`: Single-pass aggregation of all pairs
  - *Config Query*:
    - `get_cooldown_required_days(reason)`: Query cooldown days from config

### 6. RiskManager.py - Portfolio-Level Risk Control (v7.98.2)
- **Purpose**: Portfolio-level risk detection only (Pair-level moved to PairsManager)
- **Design Principle** (v7.98.2): Single responsibility - only VIX and Portfolio drawdown
  - ✅ **Responsible for**: VIX market condition check, Portfolio drawdown HWM tracking
  - ❌ **NOT responsible for**: Pair-level health checks (moved to PairsManager.check_pairs_health)
- **Key Methods**:
  - `check_portfolio_drawdown()`: Check if drawdown ≥ 20%, returns (bool, description)
  - `is_in_portfolio_cooldown()`: Query if in cooldown period (360 days)
  - `activate_portfolio_cooldown()`: Called by main.py to activate cooldown
  - `is_vix_safe()`: Check if VIX < 35, returns bool
- **State Variables**:
  - `high_water_mark`: Portfolio high water mark (initialized to initial capital)
  - `portfolio_cooldown_until`: Cooldown end time

### 7. UniverseSelection.py - Stock Selection
- **Purpose**: Monthly universe refresh with multi-stage fundamental screening
- **Two-stage filtering**:
  - **Coarse**: Price > $20, Volume > $5M, IPO > 3 years
  - **Fine** (v7.29.0+):
    - Valuation: PE ≤ 100 OR PS ≤ 10 (OR logic避免误杀高成长股)
    - Profitability: ROE > 0%
    - Leverage: Debt-to-Assets ≤ 70%, Leverage ≤ 5x
    - Volatility: Annual volatility ≤ 50%
    - Note: v7.29.1 unified boundary conditions (≤/≥ instead of </>)
- **Industry-group-based selection**: Top stocks per Morningstar industry group (动态分组,实际出现18-20个)
- **Triggers**: Monthly via `Schedule.On()` → `TriggerSelection()`

### 8. TicketsManager.py - Order Lifecycle Tracking (v6.4.4)
- **Purpose**: Prevent duplicate orders via order locking mechanism
- **Key Features**:
  - Real-time order status calculation (PENDING/COMPLETED/ANOMALY)
  - Pair-level order locking during execution
  - Anomaly detection for single-leg failures (Canceled/Invalid orders)
- **Key Methods**:
  - `register_tickets()`: Register orders after open/close operations
  - `is_pair_locked()`: Check if pair is executing orders (prevents duplicate submission)
  - `on_order_event()`: Process OrderEvent callbacks and update status
  - `get_anomaly_pairs()`: Detect pairs with order anomalies for risk management
- **Design Principle**: Single source of truth - status derived from OrderTicket.Status

### 9. Analysis Modules (src/analysis/)
- **DataProcessor**: Clean and prepare historical data (252-day lookback)
- **PairData**: Data encapsulation class for pair analysis
  - **Purpose**: Unified data interface for BayesianModeler and PairSelector
  - **Factory Method**: `PairData.from_clean_data(pair_info, clean_data)` (recommended creation pattern)
  - **Key Fields**: symbol1, symbol2, industry_code, clean_data (252-day DataFrame)
  - **Usage**: Passed between analysis modules to avoid duplicate data preparation
- **CointegrationAnalyzer**: Engle-Granger cointegration tests (p-value < 0.01)
  - **Industry Grouping** (v7.30.1): 按55个MorningstarIndustryGroupCode分组
  - **Volume Filtering** (v7.30.1): 每个子行业内按Volume(成交股数)筛选TOP 30
- **BayesianModeler**: PyMC MCMC parameter estimation (500 warmup + 500 samples, 4 chains)
  - **Input**: PairData objects from CointegrationAnalyzer
  - **Output**: ModelResult objects with posterior distributions (alpha, beta, sigma)
- **PairSelector**: 三维度阈值筛选 (v8.9.1: 删除Hurst维度)
  - **Input**: ModelResult objects from BayesianModeler
  - **Threshold Filtering** (v8.9.1): Pass/fail logic for each dimension
    | 维度 | 公式 | 阈值 | 含义 |
    |------|------|------|------|
    | CV BETA | `beta_std / |beta_mean|` | ≤ 0.2 | β估计的相对不确定性 |
    | 半衰期 | `-ln(2) / ln(rho_mean)` | [5, 20] 天 | 回归速度 |
    | 零轴穿越 | 符号变化次数 | [6, 30] 次 | 交易活跃度 |
  - **Key Methods**:
    - `_filter_by_cv_beta()`: CV BETA 稳定性筛选
    - `_filter_by_half_life()`: 半衰期范围筛选
    - `_filter_by_zero_crossing()`: 零轴穿越次数筛选
  - **Design Change** (v8.9.1): 删除Hurst维度 (60天数据不足以稳健计算R/S分析)

## Trading Execution Flow (OnData) - v8.0.0

### OnData 6-Priority Execution
1. **Portfolio Cooldown Check**: `RiskManager.is_in_portfolio_cooldown()` → return if active
2. **Portfolio Drawdown Check**: `RiskManager.check_portfolio_drawdown()` → trigger Liquidate if ≥20%
3. **Pair Health Check**: `PairsManager.check_pairs_health()` → execute risk close for anomaly/drawdown/drift/timeout
4. **Normal Close**: Check CLOSE/PAIR_BREAK signals → Intent Pattern close
5. **VIX Safety Check**: `RiskManager.is_vix_safe()` → return if VIX ≥ 35
6. **Normal Open**: `PairsManager.get_open_candidates_with_allocation()` → Intent Pattern open

### Closing Logic
**Flow**: `check_pairs_health()` or `get_signal()` → `get_close_intent()` → `execute_close()` → `register_tickets()`

**Key Steps**:
1. Health check returns Dict[issue_type, pair_ids] with priority order
2. Signal check returns CLOSE or PAIR_BREAK for normal exits
3. Execute Intent Pattern: `pair.get_close_intent(reason)` → `order_executor.execute_close(intent)`

### Opening Logic
**Flow**: `get_open_candidates_with_allocation()` → Intent Pattern open

**Key Steps** (inside PairsManager):
1. Filter current_selected pairs without position, not in cooldown, not locked
2. Get signals (LONG_SPREAD/SHORT_SPREAD)
3. Allocate margin based on fixed 15% (v8.10.0)
4. Sort by expected_profit (v8.11.0: 预期收益额排序)
5. Return List[Tuple[pair, signal, allocated_margin]]

### Position Sizing (v8.10.0 Fixed Allocation)
- **Fixed Allocation**: 统一15%分配比例
  ```python
  fixed_allocation_pct = 0.15  # 统一分配比例
  min_investment_ratio = 0.15  # 地板保护 (= INITIAL_CAPITAL × 15%)
  ```
- **Allocation Logic**:
  ```python
  planned = initial_available × 15%
  actual = max(planned, min_threshold)  # 地板保护
  ```
- **Margin Buffer**: FIXED_BUFFER reserved from MarginRemaining
- **Beta Hedging**: `long_value = margin / (1 + 1/|beta|)`, `short_value = margin / (1 + |beta|)`

## Critical Implementation Details

### Order Lifecycle Management (v6.4.4)

The TicketsManager implements a sophisticated order tracking system to prevent duplicate submissions and handle order anomalies:

**Core Mechanism**:
- **Single Source of Truth**: Order status derived from `OrderTicket.Status` (real-time calculation, no state storage)
- **Order Lock**: Pairs with PENDING orders are locked, preventing duplicate submissions
- **Anomaly Detection**: Automatically identifies single-leg failures (Canceled/Invalid orders)

**Status Flow**:
```
Order Submission → PENDING (locked) → COMPLETED (unlocked) or ANOMALY (requires risk intervention)
```

**Integration Pattern** (v7.0.0 Intent Pattern):
```python
# Step 1: Check lock before trading
if tickets_manager.is_pair_locked(pair.pair_id):
    continue  # Skip if orders pending

# Step 2: Generate intent and execute
intent = pair.get_open_intent(allocated_margin, data)
if intent:
    tickets = order_executor.execute_open(intent)
    if tickets:
        tickets_manager.register_tickets(pair.pair_id, tickets, 'OPEN')

# Step 3: OnOrderEvent routes to TicketsManager (v7.99.4 fix)
# QCAlgorithm.OnOrderEvent → TicketsManager.on_order_event → Pairs.on_position_filled
```

**Why This Architecture**:
- Prevents duplicate order submission when orders are still executing
- Handles asynchronous order processing (orders may fill across multiple OnData cycles)
- Automatically detects and flags order anomalies for risk management
- Zero additional state management burden (status calculated on-demand)

### Margin Calculation Architecture (v6.4.0)

Position sizing is based on **margin requirements** rather than cash allocation:

**U.S. Equity Margin Rules** (Regulation T):
- **Long position**: 50% margin requirement
  - Example: $10,000 long position requires $5,000 margin
- **Short position**: 150% margin requirement (100% borrowed + 50% margin)
  - Example: $10,000 short position requires $15,000 margin

**Pairs Trading Margin Formula**:
```python
# Given: desired margin allocation (e.g., $20,000)
# Beta hedging ratio determines value split

# Value allocation (beta-hedged)
long_value = margin_allocated / (1 + 1/|beta|)
short_value = margin_allocated / (1 + |beta|)

# Margin verification
required_margin = long_value * 0.5 + short_value * 1.5
# should equal margin_allocated

# Example: beta=-0.8, margin_allocated=$20,000
# long_value = 20000 / (1 + 1/0.8) = $8,889
# short_value = 20000 / (1 + 0.8) = $11,111
# required_margin = 8889*0.5 + 11111*1.5 = $21,111 ≈ $20,000
```

**Allocation Process** (v8.10.0):
1. Calculate available margin: `Portfolio.MarginRemaining - FIXED_BUFFER`
2. Calculate min_threshold: `INITIAL_CAPITAL × 15%` (地板保护)
3. For each candidate: `actual = max(available × 15%, min_threshold)`
4. Sort by expected_profit (v8.11.0: 预期收益额排序)

**Key Insight**: 统一分配比例 + 地板保护机制 + 预期收益排序。

### ~~Dual Cooldown Mechanism (v7.2.21)~~ [DEPRECATED in v7.12.0]

**Note**: This mechanism was replaced by the **Unified Cooldown System** in v7.12.0. See CHANGELOG.md for migration details.

<details>
<summary>Historical Implementation (Click to expand)</summary>

The strategy implemented **dynamic cooldown periods** based on exit reasons:

**Normal Exit (CLOSE)**: 10 days
- Z-score converges to mean (< 0.25σ)
- Pair relationship remains healthy and mean-reverting

**Stop Loss Exit (STOP_LOSS)**: 30 days
- Z-score exceeds threshold (> 2.5σ)
- Indicates potential breakdown in cointegration relationship

**v7.12.0 Change**: Unified to 3 cooldown types (NORMAL, DRAWDOWN, ANOMALY) with consistent propagation mechanism through `last_close_reason` field.

</details>

### Statistical Engine
- **Cointegration testing**: Engle-Granger test with p-value < 0.05
- **Bayesian modeling**: PyMC with 500 warmup + 500 posterior samples, 2 chains
- **Signal thresholds**: Entry [1.2σ, 1.8σ], Exit 0.3σ, Stop 3.0σ
- **Cooldown Period**: Unified cooldown_days config (default 30 days for all close reasons)

### Universe Selection Logic
1. **Coarse filtering**: Price > $20, Volume > $5M, IPO > 3 years
2. **Industry grouping**: MorningstarIndustryGroupCode动态分组(55个标准分组,实际出现18-20个)
3. **Fundamental filters** (v7.29.0+):
   - **Valuation** (OR logic): PE ≤ 100 OR PS ≤ 10 (避免误杀高成长股)
   - **Profitability**: ROE > 0%
   - **Leverage**: Debt-to-Assets ≤ 70%, Leverage ≤ 5x
   - **Note**: v7.29.1 unified boundary conditions (≤/≥ instead of </>)
4. **Volatility filter**: Annual volatility ≤ 50% (v7.29.1: boundary inclusive)

## Cross-Module Communication (v7.0.0 OnData + Intent Pattern)

### Data Flow
1. **Universe Changes**: `OnSecuritiesChanged()` → triggers pair analysis
2. **Analysis Pipeline** (7 steps in `_run_analysis_pipeline` v8.9.0):
   - DataProcessor → CointegrationAnalyzer → PairData → BayesianModeler → PairSelector → Pairs objects → PairsManager.classify_pairs()
3. **Trading Flow (OnData 6-priority)**:
   - Portfolio checks (RiskManager) → Health checks (PairsManager) → Signal close → VIX check → Signal open
4. **Intent Flow**: Pairs.get_*_intent() → OrderExecutor.execute_*() → TicketsManager.register_tickets()
5. **Order Events** (v7.99.4 fix): OnOrderEvent() → TicketsManager.on_order_event() → Pairs.on_position_filled()
6. **Industry Metrics Flow** (v8.1.0): Pairs.trade_history (four-tuple) → PairsManager._aggregate_all_industry_data() → get_industry_stats()

### State Management
- **Pair Classification**: current_selected (this month) / past_selected (historical)
- **Order States**: PENDING (executing) / COMPLETED (filled) / ANOMALY (canceled/invalid)
- **Position Tracking**: `pair.has_position()` dynamic query
- **Cooldown Tracking**: `pair.is_in_cooldown()` using last_close_reason
- **Health Issues**: anomaly/drawdown/drift/timeout (priority order)

## Key Dependencies

### Statistical Libraries
- **PyMC**: Bayesian MCMC sampling (version compatibility critical)
- **statsmodels**: Cointegration testing (coint function)
- **NumPy/Pandas**: Numerical operations and data handling

### QuantConnect Framework
- **AlgorithmImports**: Comprehensive import module
- **Framework modules**: Universe, Alpha, Portfolio, Risk, Execution models
- **Data sources**: Morningstar fundamentals, daily OHLCV, sector classifications

## Common Development Patterns

### Error Handling
```python
try:
    # PyMC modeling or cointegration test
    result = complex_statistical_operation()
except Exception as e:
    self.algorithm.Debug(f"[Module] Operation failed: {str(e)}")
    return None  # Graceful degradation
```

### Logging Convention
```python
self.algorithm.Debug(f"[ModuleName] Description: value")
# Examples:
# [配对分析] 完成: 创建3个新配对, 共管理8个配对
# [开仓] 成功开仓2/3个配对
# [平仓] (AAPL, MSFT) Z-score回归
# [持仓异常] (AAPL, MSFT) 单边持仓LEG1: qty1=100

# Debug mode: Controlled via config.py main['debug_mode']
# - True: All debug logs are printed (development/testing)
# - False: Only logs when debug_mode=True (production - currently all logs shown when enabled)
```

### Intent Pattern (v7.0.0 - Recommended)
```python
# Step 1: Generate intent from Pairs
intent = pair.get_open_intent(amount_allocated, data)
if not intent:
    return  # No valid intent (e.g., no signal, insufficient data)

# Step 2: Execute intent via OrderExecutor
tickets = self.order_executor.execute_open(intent)
if not tickets:
    return  # Execution failed (e.g., insufficient buying power)

# Step 3: Register tickets for tracking
self.tickets_manager.register_tickets(pair.pair_id, tickets, OrderAction.OPEN)

# Closing example:
intent = pair.get_close_intent(reason='STOP_LOSS')
if intent:
    tickets = self.order_executor.execute_close(intent)
    if tickets:
        self.tickets_manager.register_tickets(pair.pair_id, tickets, OrderAction.CLOSE)
```

### Order Lock Check Pattern (Critical for v6.4.4+)
```python
# ALWAYS check order lock before executing trades
if self.tickets_manager.is_pair_locked(pair.pair_id):
    self.Debug(f"[Trade] {pair.pair_id} 订单处理中,跳过", 2)
    continue

# v7.0.0: Intent Pattern (3 steps)
intent = pair.get_open_intent(amount_allocated, data)
if intent:
    tickets = self.order_executor.execute_open(intent)
    if tickets:
        self.tickets_manager.register_tickets(pair.pair_id, tickets, OrderAction.OPEN)
```

### Module Access Pattern
```python
# Access universe selection results
symbols = self.algorithm.universe_selector.last_fine_selected_symbols

# Check order status before trading
if self.algorithm.tickets_manager.is_pair_locked(pair_id):
    return  # Skip if orders pending

# Get anomaly pairs for risk management
anomaly_pairs = self.algorithm.tickets_manager.get_anomaly_pairs()
```

## Performance Considerations

- **MCMC optimization**: Limited to 2 chains × 500 samples (warmup + posterior) for production speed
- **History requests**: 252-day lookback optimizes accuracy vs. performance
- **Market volatility**: 20-day rolling window with deque to avoid repeated History() calls
- **Selective processing**: Skip failed cointegration tests early
- **Memory management**: Clear outdated references during universe reselection
- **Order tracking overhead**: Minimal - status calculated on-demand from OrderTicket.Status (no state storage)

## Common Pitfalls and Best Practices

### Critical: Order Locking (v6.4.4)
**Problem**: Without order lock checks, the same pair can submit multiple overlapping orders
**Solution**: ALWAYS check `tickets_manager.is_pair_locked()` before trading
```python
# ❌ WRONG - May submit duplicate orders
intent = pair.get_close_intent('MEAN_REVERSION', data)
order_executor.execute_close(intent)  # Danger: may execute multiple times

# ✅ CORRECT - Check lock first (Intent Pattern)
if not tickets_manager.is_pair_locked(pair.pair_id):
    intent = pair.get_close_intent('MEAN_REVERSION', data)
    if intent:
        tickets = order_executor.execute_close(intent)
        if tickets:
            tickets_manager.register_tickets(pair.pair_id, tickets, 'CLOSE')
```

### Margin vs. Cash Confusion
**Problem**: Confusing `Portfolio.Cash` with `Portfolio.MarginRemaining`
**Solution**: Use `Portfolio.MarginRemaining` for position sizing (accounts for both long/short margin)
```python
# ❌ WRONG - Cash ignores margin requirements
available = Portfolio.Cash

# ✅ CORRECT - MarginRemaining is the true available capital
available_margin = Portfolio.MarginRemaining * 0.95  # Keep 5% buffer
```

### Health Check vs. Execution (v8.0.0)
**Problem**: Mixing health detection with trade execution
**Solution**: PairsManager.check_pairs_health() detects, main.py executes
```python
# ❌ WRONG - Health check executes directly
class PairsManager:
    def check_pairs_health(self):
        if pair.get_pair_drawdown() > 0.04:
            pair.close_position()  # Violates separation

# ✅ CORRECT - Health check returns issues, main.py executes
health_issues = pairs_manager.check_pairs_health()
for issue_type, pair_ids in health_issues.items():
    for pair_id in pair_ids:
        pair = pairs_manager.get_pair_by_id(pair_id)
        intent = pair.get_close_intent(issue_type.upper(), data)
        order_executor.execute_close(intent)
```

### Order Event Anomalies
**Problem**: Ignoring single-leg order failures (Canceled/Invalid orders)
**Solution**: Use `tickets_manager.get_anomaly_pairs()` to detect and handle
```python
# In OnOrderEvent callback
anomaly_pairs = tickets_manager.get_anomaly_pairs()
for pair_id in anomaly_pairs:
    # Risk management will handle single-leg positions via check_pair_anomaly()
    self.Debug(f"[订单异常] {pair_id} 检测到单腿失败", 1)
```

### Pair Classification Confusion (v8.0.0)
**Problem**: Confusing current_selected vs past_selected pairs
**Solution**: Only open positions for current_selected; past_selected can only close
```python
# ❌ WRONG - Opening positions for past_selected pairs
for pair in pairs_manager.all_pairs.values():
    if pair.get_signal(data) in [LONG_SPREAD, SHORT_SPREAD]:
        pair.get_open_intent(...)  # May open for expired pairs

# ✅ CORRECT - Use get_open_candidates_with_allocation() (filters internally)
candidates = pairs_manager.get_open_candidates_with_allocation(data)
for pair, allocated_margin in candidates:
    intent = pair.get_open_intent(allocated_margin, data)
```

### Entry Z-Score Recording Mechanism (v7.2.16)

**Core Principle**: `entry_zscore` is recorded **at signal generation time** (not at order execution time)

**Implementation** (Pairs.get_signal()):
```python
if zscore > self.entry_threshold:
    self.entry_zscore = zscore  # Capture exact triggering value
    return TradingSignal.SHORT_SPREAD
elif zscore < -self.entry_threshold:
    self.entry_zscore = zscore  # Guaranteed |zscore| ≥ 1.0
    return TradingSignal.LONG_SPREAD
```

**Why This Matters**:
- **Before v7.2.16**: Recorded at `get_open_intent()` → could deviate to 0.07 due to price changes
- **After v7.2.16**: Recorded at signal trigger → guaranteed |zscore| ≥ entry_threshold (1.0)
- **Benefit**: Trade analysis accurately reflects entry decision quality, not execution slippage

**Z-Score Calculation** (No Smoothing):
```python
log_residual = np.log(price1) - (alpha_mean + beta_mean * np.log(price2))
zscore = (log_residual - residual_mean) / residual_std
# Instant response to price changes - no rolling window, no EMA
```

## Testing and Debugging

- **Local backtesting**: Use `lean backtest` for rapid iteration
- **Cloud backtesting**: Use `lean cloud push` for production testing
- **Debug logging**: Liberal use of `self.algorithm.Debug()` statements
- **Performance metrics**: Monitor via QuantConnect backtest results

## Configuration Management

### Current Approach
- All parameters centralized in `src/config.py` via `StrategyConfig` class
- Module-specific parameters passed via config dictionary
- QuantConnect configuration in `config.json` (cloud-id, org-id)
- No external parameter files - all hardcoded for deterministic backtesting

### Environment Configuration
- **Development**: Local LEAN CLI with Visual Studio Code integration
- **Testing**: Isolated mock environment independent of QuantConnect
- **Production**: QuantConnect cloud with InteractiveBrokers live trading
- **Debugging**: Configurable debug levels (0-3) in `src/config.py:main['debug_level']`

## Version History

**Current Version**: v8.25.0 (2025-12-04)

**Recent Major Updates**:
- **v8.25.0** (Dec 2025): 尾部宽度动态止损 - tail_width替代residual_std计算trailing_step,实现个性化止损步长
- **v8.24.0** (Dec 2025): 稀有事件捕捉 - 180天Z-score回算+[P95,P99.9]自适应入场区间
- **v8.16.0** (Dec 2025): 协整配对随机抽样限流 - EG检验后随机抽样20对,防止MCMC算力瓶颈
- **v8.15.0** (Dec 2025): 卡尔曼滤波完全移除 - 简化PairBreak为纯Z-score检测,删除β漂移AND条件
- **v8.11.0** (Dec 2025): 预期收益额排序 - 开仓时按预期收益潜力降序排序
- **v8.10.0** (Dec 2025): 统一15%资金分配 - 删除allocation_tiers,简化为固定比例+地板保护
- **v8.9.1** (Dec 2025): 删除Hurst维度 - 60天数据不足以稳健计算R/S分析
- **v8.9.0** (Dec 2025): 删除IndustryQuotaManager - 简化分析管道为7步
- **v8.8.0** (Dec 2025): PairSelector阈值筛选重构 - 废除评分系统,四维度→三维度pass/fail筛选
- **v8.7.0** (Dec 2025): RSI on Z-score动量检测 - 三重AND条件过滤入场信号
- **v8.1.0** (Nov 2025): 单一事实来源重构 - 三元组→四元组,删除累积变量,动态聚合方法
- **v8.0.0** (Nov 2025): 滚动窗口行业评分 - trade_history数据结构,180天窗口计算
- **v7.0.0** (Jan 2025): Intent模式重构 - 意图生成与订单执行分离

**Complete History**: See [docs/CHANGELOG.md](docs/CHANGELOG.md) for detailed version history and breaking changes

## Files to Avoid Modifying

- **config.json**: QuantConnect cloud configuration (contains cloud-id and org-id)
- **backtests/**: Historical backtest results and logs (gitignored but tracked for reference)
- **.gitignore**: Properly configured for Python/QuantConnect projects
- **.claude/agents/**: AI agent definitions (managed separately)

## Project File Organization

- **src/**: Source code modules
  - **analysis/**: Data processing and statistical analysis
    - **DataProcessor.py**: Data cleaning and validation (252-day lookback)
    - **CointegrationAnalyzer.py**: Cointegration testing (Engle-Granger p-value < 0.01)
    - **BayesianModeler.py**: PyMC MCMC parameter estimation
    - **PairSelector.py**: 三维度阈值筛选 (v8.9.1: CV BETA/半衰期/零轴穿越)
    - **PairData.py**: Data encapsulation class for pair analysis
  - **config.py**: Centralized configuration via StrategyConfig class
  - **UniverseSelection.py**: Multi-stage stock filtering
  - **Pairs.py**: Pair trading object with signal/intent generation and trade_history four-tuple (v8.1.0)
  - **PairsManager.py**: Lifecycle + margin allocation + health check + industry aggregation (v8.1.0)
  - **OrderExecutor.py**: Order execution engine (Intent Pattern - v7.0.0)
  - **RiskManager.py**: Portfolio-level risk control only (v7.98.2)
  - **TicketsManager.py**: Order lifecycle tracking (v6.4.4)
- **docs/**: Documentation and version history
  - **CHANGELOG.md**: Complete version history with detailed change tracking
- **research/**: Jupyter notebooks for strategy research and analysis
- **backtests/**: Local backtest results (gitignored, for reference only)
- **main.py**: Strategy entry point and OnData orchestrator

## AI Agents
Specialized agents available in `.claude/agents/`: **backtest-analyst**, **code-architect**, **quantconnect-test-engineer**