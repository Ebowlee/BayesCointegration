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
2. **Important**: Entry conditions (entry_zscore, quality_score), market context (VIX), state transitions
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

### 1. main.py - Strategy Orchestrator
- **Purpose**: Central orchestration via OnData event handling
- **Key Components**:
  - `BayesianCointegrationStrategy`: Main algorithm class
  - `OnData()`: Core trading logic execution
  - `OnSecuritiesChanged()`: Triggers pair analysis
  - Risk management coordination
  - Trade execution logic
- **Configuration**: All parameters in `src/config.py` via `StrategyConfig` class

### 2. Pairs.py - Pair Trading Object
- **Purpose**: Encapsulates all pair-specific logic (data provider, signal generator, intent generator, trade history tracker)
- **Design Principle** (v7.0.0 → v7.7.0): "Data Provider + Intent Generator + Trade History Owner"
  - ✅ **Provides**: PnL calculation, position data, holding time, signal generation, intent generation, trade statistics (v7.7.0)
  - ❌ **Does NOT**: Risk checking, HWM tracking, drawdown calculation, order execution
  - **Removed** (v6.9.4): `check_position_integrity()` (unused), `get_pair_drawdown()` (moved to PairDrawdownRule), `pair_hwm` attribute
  - **Removed** (v7.0.0): `open_position()`, `close_position()` (replaced by get_*_intent + OrderExecutor)
  - **Added** (v7.7.0): Trade statistics attributes (trade_count, win_count, total_pnl_dollars, total_pair_cost) for performance tracking
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
  - `get_position_info()`: Query position status (with @property position_mode - v6.9.3)
  - `get_pair_pnl()`: Calculate PnL in two modes: real-time (持仓中) or final (已平仓) - v7.0.0
  - `get_pair_cost()`: Calculate total margin required for the pair
  - `get_pair_holding_days()`: Calculate holding days (data query for PairHoldingTimeoutRule)
  - `is_in_cooldown()`: Check cooldown period (part of signal generation logic)
  - `on_position_filled()`: Callback when position fills - clears tracking variables and updates trade stats (v7.7.0)
  - `_update_trade_stats()`: Private method - calculates trade PnL% and updates statistics (v7.7.0)
- **Trade Statistics** (v7.7.0 → v7.7.1):
  - `trade_count`: Total historical trades for this pair
  - `win_count`: Number of profitable trades (pnl_dollars > 0)
  - `total_pnl_dollars`: Cumulative dollar PnL across all trades (v7.7.1 - numerator for weighted average)
  - `total_pair_cost`: Cumulative margin cost across all trades (v7.7.1 - denominator for weighted average)
  - **Cumulative Return Calculation**: `(total_pnl_dollars / total_pair_cost) * 100` (weighted average, not simple addition)
  - **Auto-update**: Statistics accumulated in `_update_trade_stats()` called by `on_position_filled()`
- **Features**: Cooldown management, beta hedging, position tracking, intent generation, trade history (v7.7.0)

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

### 5. PairsManager.py - Lifecycle Management
- **Purpose**: Manage all pairs through their lifecycle (storage and classification only)
- **Design Principle** (v7.0.7): "Storage vs Business Logic" separation
  - **Responsible for**: Storing pairs, state classification, simple queries
  - **NOT responsible for**: Signal aggregation, risk analysis, fund allocation (delegated to ExecutionManager and RiskManager)
- **State Management** (v7.0.7 - PairState unified):
  - **COINTEGRATED**: Currently passing cointegration tests (本轮通过协整检验)
  - **LEGACY**: Have positions but failed recent tests (历史配对但仍有持仓)
  - **ARCHIVED**: No positions and failed tests (历史配对且无持仓)
- **PairState Class** (v7.0.7): Merged PairClassifier into PairState
  - Contains both state constants and `classify()` method
  - Simplifies architecture by combining related functionality
- **Key Methods** (v7.0.7 updated):
  - `update_pairs()`: Update pair collection from monthly selection
  - `get_tradeable_pairs()`: Get cointegrated + legacy pairs (renamed from get_all_tradeable_pairs)
  - `get_pairs_with_position()`: Filter pairs with positions (simple query)
  - `get_pairs_without_position()`: Filter pairs without positions (simple query)
  - `get_pair_by_id()`: Retrieve specific pair by ID
  - `has_tradeable_pairs()`: Check if any tradeable pairs exist (O(1) performance)
  - `reclassify_pairs()`: Reclassify pairs using PairState.classify()

### 6. risk/RiskManager.py - Two-Tier Risk Control
- **Purpose**: Risk detection and analysis (execution handled by main.py)
- **Design Principle**: Separation of concerns - risk managers detect, main.py executes
- **Dependency Injection** (v6.9.3): Receives `pairs_manager` to query pair data for concentration analysis
- **Cooldown Mechanism** (v7.1.2): Per-Pair Cooldown for Pair rules, Global Cooldown for Portfolio rules
- **Portfolio-Level Rules**:
  - `AccountBlowupRule`: Detects loss > 30% of initial capital (cooldown: 永久)
  - `PortfolioDrawdownRule`: Detects drawdown > 15% from high water mark (cooldown: 30天)
  - Cooldown作用域: **全局** (触发后阻止所有交易)
- **Pair-Level Rules**:
  - `PairHoldingTimeoutRule`: Detects positions held > 30 days (cooldown: 30天)
  - `PairAnomalyRule`: Detects partial or same-direction positions (cooldown: 30天)
  - `PairDrawdownRule`: Detects pair drawdown > 15% from pair HWM (cooldown: 30天)
  - Cooldown作用域: **Per-Pair** (只影响触发的配对, 不影响其他配对)
- **排他性触发** (v7.1.2):
  - 同一配对多规则触发: 只执行最高优先级规则
  - 不同配对独立检查: (AAPL,MSFT)和(GOOGL,META)可触发不同规则
  - 示例: (AAPL,MSFT)同时满足Anomaly+Timeout → 只触发Anomaly (priority=100)

### 7. ExecutionManager.py - Unified Execution Coordinator (v7.0.0)
- **Purpose**: Coordinate all trading actions through Intent Pattern
- **Design Principle**: "Coordinator, Not Executor" - orchestrates intent generation and execution
- **Key Methods** (v7.1.7 updated):
  - `handle_portfolio_risk_intents()`: Coordinate portfolio-level risk actions (e.g., liquidate all)
  - `handle_pair_risk_intents()`: Coordinate pair-level risk actions (e.g., close specific pairs)
  - `cleanup_remaining_positions()`: Clean up residual positions during cooldown period
  - `handle_normal_close_intents()`: Coordinate normal closing intents from pairs (renamed from handle_signal_closings)
  - `handle_normal_open_intents()`: Coordinate normal opening intents with dynamic margin allocation (renamed from handle_position_openings)
  - `get_entry_candidates()`: Aggregate opening signals sorted by quality (v6.9.3: migrated from PairsManager)
- **Responsibilities** (v7.0.0 updated):
  - Signal aggregation for opening candidates
  - Dynamic fund allocation based on quality scores
  - Intent generation coordination (calls Pairs.get_*_intent)
  - Order execution coordination (calls OrderExecutor.execute_*)
  - Ticket registration (calls TicketsManager.register_tickets)
  - Interaction with PairsManager, TicketsManager, and OrderExecutor
- **Deprecated Methods** (v7.1.2 removed):
  - `handle_portfolio_risk_action()`: Replaced by handle_portfolio_risk_intents()
  - `handle_pair_risk_actions()`: Replaced by handle_pair_risk_intents()
  - `liquidate_all_positions()`: Replaced by cleanup_remaining_positions()

### 8. MarginAllocator.py - Level 1 Global Fund Allocation (v7.0.0)
- **Purpose**: Calculate available margin and allocate to entry candidates based on quality scores
- **Design Principle**: "Stateless Calculator + Fixed Buffer Constraint"
  - ✅ **Responsible for**: Computing available margin, assigning planned allocation percentages
  - ❌ **NOT responsible for**: Position opening, risk checking, order execution
- **Key Features**:
  - **Fixed Buffer**: 5% of initial capital reserved throughout entire backtest (non-dynamic)
  - **Baseline Constraint**: Allocation capped at `min(current_available, initial_capital × planned_pct)`
  - **Min Investment Filter**: $5,000 minimum allocation per pair (filters trivial positions)
  - **Quality-Based Scaling**: `planned_pct = min_pct + quality_score × (max_pct - min_pct)`
- **Key Methods**:
  - `get_available_margin()`: Returns `Portfolio.MarginRemaining - fixed_buffer`
  - `allocate_margin(entry_candidates)`: Distributes margin to candidates by quality score, returns allocation dict
  - `_calculate_planned_percentage(quality_score)`: Maps quality_score (0-1) to allocation percentage (10%-30%)
- **Allocation Algorithm**:
  1. Calculate total available margin (MarginRemaining - fixed_buffer)
  2. Assign each candidate a planned percentage based on quality_score
  3. Apply baseline constraint: `allocation = min(available_margin × planned_pct, initial_capital × planned_pct)`
  4. Filter out allocations < min_investment ($5,000)
  5. Return allocation dict: `{pair_id: allocated_margin}`
- **Integration Pattern**:
  ```python
  # ExecutionManager.handle_normal_open_intents()
  allocations = self.margin_allocator.allocate_margin(entry_candidates)
  for pair_id, margin in allocations.items():
      pair = self.pairs_manager.get_pair_by_id(pair_id)
      intent = pair.get_open_intent(margin, data)
      # ... execute intent
  ```

### 9. UniverseSelection.py - Stock Selection
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

### 10. TicketsManager.py - Order Lifecycle Tracking (v6.4.4)
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

### 11. Analysis Modules (src/analysis/)
- **DataProcessor**: Clean and prepare historical data (252-day lookback)
- **PairData**: Data encapsulation class for pair analysis
  - **Purpose**: Unified data interface for BayesianModeler and PairSelector
  - **Factory Method**: `PairData.from_clean_data(pair_info, clean_data)` (recommended creation pattern)
  - **Key Fields**: symbol1, symbol2, industry_code, clean_data (252-day DataFrame)
  - **Usage**: Passed between analysis modules to avoid duplicate data preparation
- **CointegrationAnalyzer**: Engle-Granger cointegration tests (p-value < 0.05) + industry quota application (v7.12.0)
  - **Industry Grouping** (v7.30.1): 按55个MorningstarIndustryGroupCode分组
  - **Volume Filtering** (v7.30.1): 每个子行业内按Volume(成交股数)筛选TOP 30
  - **Industry Quota** (v7.12.0): Applies dynamic quotas at cointegration stage
  - Selects TOP N pairs per industry by pvalue (N from IndustryQuotaManager)
- **BayesianModeler**: PyMC MCMC parameter estimation (500 warmup + 500 samples, 2 chains)
  - **Input**: PairData objects from CointegrationAnalyzer
  - **Output**: ModelResult objects with posterior distributions (alpha, beta, sigma)
- **PairSelector**: Quality scoring using 2 weighted metrics (v7.5.23) + risk pair filtering (v7.12.0)
  - **Input**: ModelResult objects from BayesianModeler
  - **Quality Metrics**:
    - **half_life** (60%): Mean reversion speed (most independent + highest predictive power 57%)
    - **mean_reversion_certainty** (40%): AR(1) significance (theoretical core + moderate predictive power 50%)
  - **Risk Filtering** (v7.12.0): `_filter_risk_pairs()` internal method filters DRAWDOWN/ANOMALY cooldown pairs
  - **No Blacklist**: v7.12.0 removed BlacklistManager module, simplified to cooldown-based filtering
- **IndustryQuotaManager** (v7.12.0): Dynamic industry-level quota system
  - **Warmup Period**: First 180 days use default quota (1 pair per industry)
  - **Dynamic Adjustment**: Monthly quota calculation based on weighted return
  - **Quota Tiers**: 1/3/6/9 pairs per industry (based on performance)
  - **Weighted Return**: sum(total_pnl_dollars) / sum(total_pair_cost) per industry

## Trading Execution Flow (OnData)

### Execution Priority
1. **Strategy Cooldown Check**: Skip if in global cooldown period
2. **Portfolio Risk Management**: Detect and handle portfolio-level risks (blowup, drawdown, sector concentration)
3. **Market Environment Check**: Check market volatility before opening new positions
4. **Pair Risk Management**: Detect and handle pair-level risks (timeout, anomaly, drawdown)
5. **Position Management**:
   - Close positions for pairs with exit/stop signals or risk triggers
   - Open new positions using intelligent fund allocation

### Closing Logic (Pairs with Positions)
**Flow**: `pairs_manager.get_pairs_with_position()` → Order lock check → Risk check → Signal check → Intent Pattern (3 steps)

**Key Steps**:
1. Check `tickets_manager.is_pair_locked()` to prevent duplicate orders
2. Check pair-level risks (timeout/anomaly/drawdown) via `risk_manager.check_pair_risks()`
3. Get trading signal via `pair.get_signal(data)`
4. Execute Intent Pattern: `get_close_intent() → execute_close() → register_tickets()`

**Implementation**: See [main.py](main.py#L200-L216) OnData method

### Opening Logic (Pairs without Positions)
**Flow**: `pairs_manager.get_pairs_without_position()` → `get_entry_candidates()` → Dynamic margin allocation → Intent Pattern

**Key Steps**:
1. Get entry candidates sorted by quality score (via `ExecutionManager.get_entry_candidates()`)
2. Calculate available margin (95% of MarginRemaining, keep 5% buffer)
3. Dynamic allocation with quality-based scaling: `planned_pct × scale_factor`
4. Execute Intent Pattern: `get_open_intent() → execute_open() → register_tickets()`

**Implementation**: See [main.py](main.py#L218-L224) OnData method

### Position Sizing (v6.4.4 Margin-Based Model)
- **Margin-Based Allocation**: Position sizing uses margin requirements instead of cash
  - Long position: 50% margin requirement
  - Short position: 150% margin requirement (100% borrowed + 50% margin)
  - Formula: `required_margin = long_value * 0.5 + short_value * 1.5`
- **No Hard Pair Limit**: Position count limited by available margin (natural constraint)
- **Margin Buffer**: 5% of MarginRemaining reserved (dynamic, not fixed to initial capital)
- **Min Investment**: 10% of initial capital (margin-based)
- **Max Investment**: 30% of initial capital (margin-based, increased from 25%)
- **Quality-Based Allocation**: `allocation_pct = min_pct + quality_score * (max_pct - min_pct)`
- **Dynamic Scaling**: Maintains fair allocation ratios as margin depletes
- **Beta Hedging**: `long_value = margin_allocated / (1 + 1/|beta|)`, `short_value = margin_allocated / (1 + |beta|)`

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

**Integration Pattern**:
```python
# Step 1: Check lock before trading
if tickets_manager.is_pair_locked(pair.pair_id):
    continue  # Skip if orders pending

# Step 2: Execute trade
tickets = pair.open_position(signal, margin, data)

# Step 3: Register tickets (activates lock)
if tickets:
    tickets_manager.register_tickets(pair.pair_id, tickets)

# Step 4: OnOrderEvent automatically updates status
# (via QCAlgorithm.OnOrderEvent → TicketsManager.on_order_event)

# Step 5: Next OnData cycle checks lock again
# - PENDING → Skip trading
# - COMPLETED → Allow new trades
# - ANOMALY → Risk management handles single-leg positions
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

**Dynamic Allocation Process**:
1. Calculate initial margin pool: `Portfolio.MarginRemaining * 0.95` (keep 5% buffer)
2. Assign planned percentages to pairs based on quality scores
3. As pairs open, maintain fair ratios via dynamic scaling:
   - `scale_factor = initial_margin / current_available_margin`
   - Each pair gets: `planned_pct * current_margin * scale_factor`
4. Buffer prevents margin calls, min_investment filters trivial positions

**Key Insight**: This model naturally constrains position count by available margin, eliminating the need for hard pair limits.

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
- **Signal thresholds**: Entry ±1.0σ, Exit ±0.3σ, Stop ±3.0σ
- **Cooldown Period**: Dynamic - 10 days (normal exit) or 30 days (stop loss) - updated in v7.2.21

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
2. **Analysis Pipeline**: DataProcessor → CointegrationAnalyzer (v7.12.0: applies industry quotas) → BayesianModeler → PairSelector (v7.12.0: filters risk pairs)
3. **Pair Creation**: Direct Pairs object creation → PairsManager.update_pairs()
4. **Trading Flow (Intent Pattern)**: OnData → Risk detection → Order lock check → Pairs.get_*_intent() → OrderExecutor.execute() → Trade execution
5. **Order Tracking**: Pairs.get_*_intent() → Returns Intent → OrderExecutor.execute() → Returns tickets → TicketsManager.register_tickets() → Order lock activated
6. **Order Events**: QCAlgorithm.OnOrderEvent() → TicketsManager.on_order_event() → Status update (PENDING/COMPLETED/ANOMALY)
7. **State Updates**: PairsManager maintains pair lifecycle states (active/legacy/dormant)
8. **Intent Flow** (v7.0.0): Pairs (generate intent) → OrderExecutor (execute intent) → TicketsManager (track orders)
9. **Industry Quota Flow** (v7.12.0): IndustryQuotaManager.calculate_quotas() → CointegrationAnalyzer (applies quotas) → Selects TOP N pairs per industry
10. **Risk Filtering Flow** (v7.12.0): PairSelector._filter_risk_pairs() → Checks last_close_reason (DRAWDOWN/ANOMALY) → Filters cooldown pairs

### State Management
- **Pair States**: Active (tradeable), Legacy (position only), Dormant (inactive)
- **Order States**: NONE (no orders) / PENDING (executing) / COMPLETED (filled) / ANOMALY (canceled/invalid)
- **Position Tracking**: Direct Portfolio queries via Pairs.get_position_info()
- **Risk State**: High water marks tracked in risk module classes
- **Cooldown Tracking**: Per-pair cooldown managed in Pairs objects
- **Margin Constraints**: Dynamic margin buffer (5% of MarginRemaining) and allocation tracked in main.py

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
signal = pair.get_signal(data)
if signal == TradingSignal.CLOSE:
    pair.close_position()  # Danger: may execute multiple times

# ✅ CORRECT - Check lock first
if not tickets_manager.is_pair_locked(pair.pair_id):
    signal = pair.get_signal(data)
    if signal == TradingSignal.CLOSE:
        tickets = pair.close_position()
        if tickets:
            tickets_manager.register_tickets(pair.pair_id, tickets)
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

### Risk Detection vs. Execution
**Problem**: Risk managers executing trades directly (violates separation of concerns)
**Solution**: Risk managers detect, main.py executes
```python
# ❌ WRONG - Risk manager executes
class RiskManager:
    def check_timeout(self, pair):
        if timeout:
            pair.close_position()  # Violates separation of concerns

# ✅ CORRECT - Risk manager detects, main.py executes
if pair_level_risk_manager.check_holding_timeout(pair):
    tickets = pair.close_position()  # main.py handles execution
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

### Pair State Confusion
**Problem**: Trading with archived pairs (failed cointegration, no position)
**Solution**: Only trade pairs from cointegrated + legacy states
```python
# ❌ WRONG - May trade archived pairs
for pair in pairs_manager.all_pairs.values():
    signal = pair.get_signal(data)

# ✅ CORRECT - Only trade cointegrated or legacy pairs
tradeable_pairs = pairs_manager.get_tradeable_pairs()
for pair in tradeable_pairs.values():
    signal = pair.get_signal(data)
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

**Current Version**: v7.29.2 (2025-02-06)

**Recent Major Updates**:
- **v7.29.2** (Feb 2025): 诊断日志增强 - 估值筛选统计和资金效率诊断
- **v7.29.1** (Feb 2025): 边界包含统一化 - 所有筛选器改为 ≤/≥ 避免边界值遗漏
- **v7.29.0** (Feb 2025): 估值OR逻辑 - PE≤100 OR PS≤10 避免误杀高成长股
- **v7.27.0** (Feb 2025): 累积亏损规则 - 新增PairCumulativeLossRule配对级风险控制
- **v7.12.0** (Nov 2025): 简化冻结机制 + 行业动态配额系统 - 统一冷却机制,移除BlacklistManager,新增IndustryQuotaManager
- **v7.11.0** (Nov 2025): 自适应持仓超时 - 基于半衰期的动态持仓时间限制
- **v7.7.2** (Feb 2025): 配置修复 - 补充缺失的trade_analysis配置块
- **v7.7.1** (Feb 2025): 数学修复 - 累积收益率加权平均计算
- **v7.7.0** (Feb 2025): 交易模块OOP重构 - 面向对象设计 (v7.12.0已废弃)
- **v7.5.23** (Feb 2025): 二维质量评分 - 移除Beta稳定性和残差质量指标
- **v7.0.0** (Jan 2025): Intent模式重构 - 意图生成与订单执行分离
- **v6.4.4** (Jan 2025): 订单生命周期追踪 - 订单锁防止重复提交

**Complete History**: See [docs/CHANGELOG.md](docs/CHANGELOG.md) for detailed version history and breaking changes

## Files to Avoid Modifying

- **config.json**: QuantConnect cloud configuration (contains cloud-id and org-id)
- **backtests/**: Historical backtest results and logs (gitignored but tracked for reference)
- **.gitignore**: Properly configured for Python/QuantConnect projects
- **.claude/agents/**: AI agent definitions (managed separately)

## Project File Organization

- **src/**: Source code modules
  - **analysis/**: Data processing and statistical analysis
    - **DataProcessor**: Data cleaning and validation
    - **CointegrationAnalyzer**: Cointegration testing with industry quota application (v7.12.0)
    - **BayesianModeler**: PyMC MCMC parameter estimation
    - **PairSelector**: Quality scoring and risk pair filtering (v7.12.0)
    - **IndustryQuotaManager**: Dynamic industry quota system (v7.12.0)
  - **config.py**: Centralized configuration via StrategyConfig class
  - **UniverseSelection.py**: Multi-stage stock filtering
  - **Pairs.py**: Pair trading object with signal generation, intent generation, and trade history tracking
  - **OrderExecutor.py**: Order execution engine (unified order submission - v7.0.0)
  - **OrderIntent.py**: Intent value objects (OpenIntent, CloseIntent - v7.0.0)
  - **PairsManager.py**: Lifecycle management for all pairs
  - **ExecutionManager.py**: Execution coordinator (orchestrates intent generation and execution - v7.0.0)
  - **risk/RiskManager.py**: Two-tier risk detection system
  - **TicketsManager.py**: Order lifecycle tracking and duplicate order prevention (v6.4.4)
- **docs/**: Documentation and version history
  - **CHANGELOG.md**: Complete version history with detailed change tracking
- **research/**: Jupyter notebooks for strategy research and analysis
- **backtests/**: Local backtest results (gitignored, for reference only)
- **main.py**: Strategy entry point and OnData orchestrator

## AI Agents
Specialized agents available in `.claude/agents/`: **backtest-analyst**, **code-architect**, **quantconnect-test-engineer**