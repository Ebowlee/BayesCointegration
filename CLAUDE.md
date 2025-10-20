# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Codebase Overview

This is a **Bayesian Cointegration** pairs trading strategy built for the QuantConnect platform. The strategy uses advanced statistical methods including Bayesian inference with MCMC sampling to identify and trade mean-reverting relationships between securities within the same industry sectors.

## Architecture Pattern (v6.4.4 OnData + Order Tracking)

The strategy uses **OnData-driven architecture** (migrated from Algorithm Framework in v6.0.0):

- **main.py**: Central orchestrator using `BayesianCointegrationStrategy(QCAlgorithm)` with OnData event handling
- **UniverseSelection**: Multi-stage fundamental screening with sector-based selection
- **Pairs**: Core pair trading object encapsulating signal generation and trade execution
- **PairsManager**: Lifecycle manager for all pairs (active, legacy, dormant states)
- **RiskManagement**: Two-tier risk control system with separation of concerns (detection vs execution)
- **Analysis modules**: DataProcessor, CointegrationAnalyzer, BayesianModeler, PairSelector

Key architectural principles (v6.4.4):
- **OnData-driven** - All trading logic flows through OnData method, no Framework modules
- **Object-oriented pairs** - Pairs class encapsulates all pair-specific logic
- **Separation of concerns** - Risk managers detect risks, main.py executes actions
- **Smart lifecycle management** - PairsManager tracks pairs through active/legacy/dormant states
- **Order lifecycle tracking** - TicketsManager prevents duplicate orders via order locking mechanism
- **Margin-based allocation** - Position sizing uses margin requirements (50% long, 150% short)
- **Intra-industry pairing** - Securities paired within same Morningstar sector
- **Natural fund constraints** - Position limits determined by available capital, not hard caps

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
# Commit format: v<major>.<minor>.<patch>[_description][@date]
git commit -m "v2.4.8_strategy-optimize@20250720"

# Update docs/CHANGELOG.md after each commit with format:
## [v2.4.8_strategy-optimize@20250720]
- Description of changes
```

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
- **Purpose**: Encapsulates all pair-specific logic (data provider, not checker)
- **Design Principle** (v6.9.4): "Data Provider, Not Checker"
  - ✅ **Provides**: PnL calculation, position data, holding time, signal generation
  - ❌ **Does NOT**: Risk checking, HWM tracking, drawdown calculation
  - **Removed** (v6.9.4): `check_position_integrity()` (unused), `get_pair_drawdown()` (moved to PairDrawdownRule), `pair_hwm` attribute
- **Creation Pattern** (v6.9.2):
  - **Recommended**: Use classmethod factory `Pairs.from_model_result(algorithm, model_result, config)`
  - **Avoid**: Direct constructor `Pairs(algorithm, model_result, config)`
  - **Consistency**: Follows same pattern as `PairData.from_clean_data()` and `TradeSnapshot.from_pair()`
- **Key Methods**:
  - `get_signal()`: Generate trading signals (LONG_SPREAD, SHORT_SPREAD, CLOSE, STOP_LOSS, HOLD)
  - `get_zscore()`: Calculate current Z-score
  - `open_position()`: Execute beta-hedged opening trades
  - `close_position()`: Execute closing trades
  - `get_position_info()`: Query position status (with @property position_mode - v6.9.3)
  - `get_pair_pnl()`: Calculate floating PnL (pure function, no side effects - v6.9.4)
  - `get_pair_holding_days()`: Calculate holding days (data query for HoldingTimeoutRule)
  - `is_in_cooldown()`: Check cooldown period (part of signal generation logic)
- **Features**: Cooldown management, beta hedging, position tracking

### 3. PairsManager.py - Lifecycle Management
- **Purpose**: Manage all pairs through their lifecycle (storage and classification only)
- **Design Principle** (v6.9.3): "Storage vs Business Logic" separation
  - **Responsible for**: Storing pairs, state classification, simple queries
  - **NOT responsible for**: Signal aggregation, risk analysis, fund allocation (delegated to ExecutionManager and RiskManager)
- **State Management**:
  - **Active pairs**: Currently passing cointegration tests
  - **Legacy pairs**: Have positions but failed recent tests
  - **Dormant pairs**: No positions and failed tests
- **Key Methods** (v6.9.3 simplified):
  - `update_pairs()`: Update pair collection from monthly selection
  - `get_all_tradeable_pairs()`: Get active + legacy pairs
  - `get_pairs_with_position()`: Filter pairs with positions (simple query)
  - `get_pairs_without_position()`: Filter pairs without positions (simple query)
  - `get_pair_by_id()`: Retrieve specific pair by ID
  - `reclassify_pairs()`: Reclassify pairs into active/legacy/dormant states

### 4. RiskManagement.py - Two-Tier Risk Control
- **Purpose**: Risk detection and analysis (execution handled by main.py)
- **Design Principle**: Separation of concerns - risk managers detect, main.py executes
- **Dependency Injection** (v6.9.3): Receives `pairs_manager` to query pair data for concentration analysis
- **PortfolioLevelRiskManager**:
  - `is_account_blowup()`: Detects loss > 30% of initial capital
  - `is_excessive_drawdown()`: Detects drawdown > 15% from high water mark
  - `is_high_market_volatility()`: Detects SPY 20-day annualized volatility > 30%
  - `get_sector_concentration()`: Calculate industry exposure (v6.9.3: migrated from PairsManager)
- **PairLevelRiskManager**:
  - `check_holding_timeout()`: Detects positions held > 30 days
  - `check_position_anomaly()`: Detects partial or same-direction positions
  - `check_pair_drawdown()`: Detects pair drawdown > 20% from pair HWM

### 5. ExecutionManager.py - Unified Execution Engine (v6.9.3)
- **Purpose**: Execute all trading actions (risk-driven and signal-driven)
- **Design Principle**: "Execution Preparation" - aggregates signals and manages fund allocation
- **Key Methods**:
  - `handle_portfolio_risk_action()`: Execute portfolio-level risk actions (e.g., liquidate all)
  - `handle_pair_risk_actions()`: Execute pair-level risk actions (e.g., close specific pairs)
  - `handle_signal_closings()`: Execute normal closing signals from pairs
  - `handle_position_openings()`: Execute opening logic with dynamic margin allocation
  - `get_entry_candidates()`: Aggregate opening signals sorted by quality (v6.9.3: migrated from PairsManager)
- **Responsibilities** (v6.9.3 expanded):
  - Signal aggregation for opening candidates
  - Dynamic fund allocation based on quality scores
  - Order submission and ticket registration
  - Interaction with PairsManager (queries) and TicketsManager (order tracking)

### 6. UniverseSelection.py - Stock Selection
- **Purpose**: Monthly universe refresh
- **Two-stage filtering**:
  - Coarse: Price > $20, Volume > $5M, IPO > 3 years
  - Fine: PE < 100, ROE > 0%, Debt/Assets < 80%
- **Sector-based selection**: Top stocks per Morningstar sector
- **Triggers**: Monthly via `Schedule.On()` → `TriggerSelection()`

### 7. TicketsManager.py - Order Lifecycle Tracking (v6.4.4)
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

### 8. Analysis Modules (src/analysis/)
- **DataProcessor**: Clean and prepare historical data (252-day lookback)
- **CointegrationAnalyzer**: Engle-Granger cointegration tests (p-value < 0.05)
- **BayesianModeler**: PyMC MCMC parameter estimation (500 warmup + 500 samples, 2 chains)
- **PairSelector**: Quality scoring using 4 weighted metrics:
  - **statistical** (30%): Cointegration strength (p-value based)
  - **half_life** (30%): Mean reversion speed (5-30 days optimal)
  - **volatility_ratio** (20%): Spread stability (spread_vol/stock_vol)
  - **liquidity** (20%): Trading volume (dollar volume based)

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
```python
# Process pairs with positions
pairs_with_position = pairs_manager.get_pairs_with_position()
for pair in pairs_with_position.values():
    # CRITICAL: Check order lock status to prevent duplicate submissions
    if tickets_manager.is_pair_locked(pair.pair_id):
        continue  # Skip if orders are still pending

    # Check pair-level risks (main.py executes liquidation)
    if pair_level_risk_manager.check_holding_timeout(pair):
        tickets = pair.close_position()
        if tickets:
            tickets_manager.register_tickets(pair.pair_id, tickets)  # Register and lock
        continue

    # Check for trading signals
    signal = pair.get_signal(data)
    if signal in [TradingSignal.CLOSE, TradingSignal.STOP_LOSS]:
        tickets = pair.close_position()
        if tickets:
            tickets_manager.register_tickets(pair.pair_id, tickets)  # Register and lock
```

### Opening Logic (Pairs without Positions) - v6.9.3 Updated
```python
# Get pairs without positions
pairs_without_position = pairs_manager.get_pairs_without_position()

# Get entry candidates sorted by quality (descending) - v6.9.3: migrated to ExecutionManager
entry_candidates = execution_manager.get_entry_candidates(pairs_without_position, data)

# Calculate available margin (95% of MarginRemaining, keep 5% buffer)
initial_margin = Portfolio.MarginRemaining * 0.95
buffer = Portfolio.MarginRemaining * 0.05

# Dynamic allocation with quality-based scaling
for pair, signal, quality_score, planned_pct in entry_candidates:
    # Check order lock status
    if tickets_manager.is_pair_locked(pair.pair_id):
        continue  # Skip if orders are still pending

    # Calculate scaled margin allocation
    current_margin = (Portfolio.MarginRemaining - buffer) * planned_pct
    scale_factor = initial_margin / (Portfolio.MarginRemaining - buffer)
    margin_allocated = current_margin * scale_factor

    # Check minimum investment threshold
    if margin_allocated < min_investment:
        continue

    # Execute opening and register orders
    tickets = pair.open_position(signal, margin_allocated, data)
    if tickets:
        tickets_manager.register_tickets(pair.pair_id, tickets)  # Register and lock
```

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

### Statistical Engine
- **Cointegration testing**: Engle-Granger test with p-value < 0.05
- **Bayesian modeling**: PyMC with 500 warmup + 500 posterior samples, 2 chains
- **Signal thresholds**: Entry ±1.0σ, Exit ±0.3σ, Stop ±3.0σ
- **Cooldown Period**: 10 days after closing a pair (updated in v6.4.0)

### Universe Selection Logic
1. **Coarse filtering**: Price > $20, Volume > $5M, IPO > 3 years
2. **Sector grouping**: 8 major sectors with 15 stocks each by market cap (updated in v6.4.0)
3. **Fundamental filters**: PE < 100, ROE > 0%, Debt-to-Assets < 70%, Leverage < 5x
4. **Volatility filter**: Annual volatility < 50%

## Cross-Module Communication (v6.4.4 OnData + Order Tracking)

### Data Flow
1. **Universe Changes**: `OnSecuritiesChanged()` → triggers pair analysis
2. **Analysis Pipeline**: DataProcessor → CointegrationAnalyzer → BayesianModeler → PairSelector
3. **Pair Creation**: Direct Pairs object creation → PairsManager.update_pairs()
4. **Trading Flow**: OnData → Risk detection → Order lock check → main.py execution → Pairs.get_signal() → Trade execution
5. **Order Tracking**: Pairs.open/close_position() → Returns tickets → TicketsManager.register_tickets() → Order lock activated
6. **Order Events**: QCAlgorithm.OnOrderEvent() → TicketsManager.on_order_event() → Status update (PENDING/COMPLETED/ANOMALY)
7. **State Updates**: PairsManager maintains pair lifecycle states (active/legacy/dormant)

### State Management
- **Pair States**: Active (tradeable), Legacy (position only), Dormant (inactive)
- **Order States**: NONE (no orders) / PENDING (executing) / COMPLETED (filled) / ANOMALY (canceled/invalid)
- **Position Tracking**: Direct Portfolio queries via Pairs.get_position_info()
- **Risk State**: High water marks tracked in RiskManagement classes
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

### Order Lock Check Pattern (Critical for v6.4.4)
```python
# ALWAYS check order lock before executing trades
if self.tickets_manager.is_pair_locked(pair.pair_id):
    self.Debug(f"[Trade] {pair.pair_id} 订单处理中,跳过", 2)
    continue

# Execute trade and register tickets
tickets = pair.open_position(signal, margin_allocated, data)
if tickets:
    self.tickets_manager.register_tickets(pair.pair_id, tickets)
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
    # Risk management will handle single-leg positions via check_position_anomaly()
    self.Debug(f"[订单异常] {pair_id} 检测到单腿失败", 1)
```

### Pair State Confusion
**Problem**: Trading with dormant pairs (failed cointegration, no position)
**Solution**: Only trade pairs from `active_ids | legacy_ids`
```python
# ❌ WRONG - May trade dormant pairs
for pair in pairs_manager.all_pairs.values():
    signal = pair.get_signal(data)

# ✅ CORRECT - Only trade active or legacy pairs
tradeable_pairs = pairs_manager.get_all_tradeable_pairs()
for pair in tradeable_pairs.values():
    signal = pair.get_signal(data)
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

## Recent Optimization History

- **v6.9.4** (Jan 2025): Pairs responsibility clarification - removed risk checking methods (check_position_integrity, get_pair_drawdown), migrated HWM tracking to PairDrawdownRule, eliminated side effects in get_pair_pnl(), added automatic HWM cleanup in TicketsManager
- **v6.9.3** (Jan 2025): Module responsibility refactor - separated storage/business logic in PairsManager, migrated signal aggregation to ExecutionManager, migrated concentration analysis to RiskManager, added @property position_mode to Pairs for DRY principle
- **v6.9.2** (Jan 2025): Classmethod factory pattern for Pairs creation (consistency with PairData and TradeSnapshot)
- **v6.9.1** (Jan 2025): PairData architecture optimization - eliminated duplicate np.log() calls (67% performance improvement)
- **v6.9.0** (Jan 2025): PairData value object introduction - pre-computed log prices for analysis modules
- **v6.4.4** (Jan 2025): Order lifecycle tracking system (TicketsManager) replaces deduplication mechanism, prevents duplicate orders via order locking
- **v6.4.0** (Jan 2025): Risk management refactor with separation of concerns, removed hard pair limits, margin-based allocation model
- **v6.3.1** (Jan 2025): Quality scoring system overhaul - replaced correlation with half_life and volatility_ratio, optimized risk parameters
- **v6.2.0**: Complete two-tier risk management system with Portfolio and Pair level controls
- **v6.1.0**: PairsManager architecture optimization - merged PairsFactory functionality
- **v6.0.0**: Major architecture migration from Algorithm Framework to OnData-driven design

### Key Architecture Evolution
- **v6.0**: Algorithm Framework → OnData-driven architecture
- **v6.1**: Unified pairs management (PairsFactory merged into PairsManager)
- **v6.2**: Two-tier risk system (Portfolio-level + Pair-level)
- **v6.3**: Quality-based pair selection (4-metric scoring system)
- **v6.4.0**: Margin-based allocation (removed hard pair limits)
- **v6.4.4**: Order lifecycle tracking (duplicate order prevention)
- **v6.9.0-v6.9.2**: PairData optimization + classmethod factory pattern (performance + consistency)
- **v6.9.3**: Module responsibility clarification (storage vs business logic separation)
- **v6.9.4**: Pairs responsibility clarification (data provider vs risk checker separation)

## Files to Avoid Modifying

- **config.json**: QuantConnect cloud configuration (contains cloud-id and org-id)
- **backtests/**: Historical backtest results and logs (gitignored but tracked for reference)
- **.gitignore**: Properly configured for Python/QuantConnect projects
- **.claude/agents/**: AI agent definitions (managed separately)

## Project File Organization

- **src/**: Source code modules
  - **analysis/**: Data processing and statistical analysis (DataProcessor, CointegrationAnalyzer, BayesianModeler, PairSelector)
  - **config.py**: Centralized configuration via StrategyConfig class
  - **UniverseSelection.py**: Multi-stage stock filtering
  - **Pairs.py**: Pair trading object with signal generation and execution
  - **PairsManager.py**: Lifecycle management for all pairs
  - **RiskManagement.py**: Two-tier risk detection system
  - **TicketsManager.py**: Order lifecycle tracking and duplicate order prevention (v6.4.4)
- **docs/**: Documentation and version history
  - **CHANGELOG.md**: Complete version history with detailed change tracking
- **research/**: Jupyter notebooks for strategy research and analysis
- **backtests/**: Local backtest results (gitignored, for reference only)
- **main.py**: Strategy entry point and OnData orchestrator

## AI Agents for Development Support

The project includes specialized AI agents in `.claude/agents/` to assist with development:

### Available Agents

1. **backtest-analyst**: Forensic analysis of backtest results
   - Analyzes trade logs, performance metrics, and execution patterns
   - Detects anomalies like over-trading specific symbols or orphaned positions
   - Provides comparative analysis across multiple backtests
   - Use when: Analyzing backtest results, investigating unexpected behavior, comparing strategy versions

2. **code-architect**: Architecture design and optimization
   - Designs new modules and refactors existing code
   - Optimizes performance bottlenecks (especially MCMC sampling)
   - Implements design patterns and manages technical debt
   - Use when: Adding new features, optimizing performance, refactoring code

3. **quantconnect-test-engineer**: Test suite development
   - Creates unit and integration tests
   - Designs mock objects for QuantConnect components
   - Generates test data for market scenarios
   - Use when: Writing tests, ensuring code reliability, creating regression tests

### Agent Usage
These agents are automatically available in Claude Code and can be invoked through the Task tool when their expertise is needed.

## Backtest Analysis

### Local Backtest Analysis
```bash
# After running a backtest, results are saved in:
# - backtest_results.json (main results)
# - backtest_logs.txt (debug logs)
# - backtest_trades.csv (trade details)

# For comprehensive analysis, use the backtest-analyst agent
# It will automatically locate and analyze all relevant files
```

### Key Metrics to Monitor
- **Symbol concentration**: Check if any symbol appears in too many trades
- **Pair lifecycle**: Verify proper entry/exit patterns
- **Signal effectiveness**: Analyze entry/exit timing quality
- **Risk triggers**: Monitor stop-loss and holding period violations