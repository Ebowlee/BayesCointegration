# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Codebase Overview

This is a **Bayesian Cointegration** pairs trading strategy built for the QuantConnect platform. The strategy uses advanced statistical methods including Bayesian inference with MCMC sampling to identify and trade mean-reverting relationships between securities within the same industry sectors.

## Architecture Pattern (v6.4.0 OnData Architecture)

The strategy uses **OnData-driven architecture** (migrated from Algorithm Framework in v6.0.0):

- **main.py**: Central orchestrator using `BayesianCointegrationStrategy(QCAlgorithm)` with OnData event handling
- **UniverseSelection**: Multi-stage fundamental screening with sector-based selection
- **Pairs**: Core pair trading object encapsulating signal generation and trade execution
- **PairsManager**: Lifecycle manager for all pairs (active, legacy, dormant states)
- **RiskManagement**: Two-tier risk control system with separation of concerns (detection vs execution)
- **Analysis modules**: DataProcessor, CointegrationAnalyzer, BayesianModeler, PairSelector

Key architectural principles (v6.4.0):
- **OnData-driven** - All trading logic flows through OnData method, no Framework modules
- **Object-oriented pairs** - Pairs class encapsulates all pair-specific logic
- **Separation of concerns** - Risk managers detect risks, main.py executes actions
- **Smart lifecycle management** - PairsManager tracks pairs through active/legacy/dormant states
- **Intra-industry pairing** - Securities paired within same Morningstar sector
- **Natural fund constraints** - Position limits determined by available capital, not hard caps

## Development Commands

### Local Development
```bash
# Run backtest locally using LEAN CLI
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

# Run cloud backtest
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
- **Purpose**: Encapsulates all pair-specific logic
- **Key Methods**:
  - `get_signal()`: Generate trading signals (LONG_SPREAD, SHORT_SPREAD, CLOSE, STOP_LOSS, HOLD)
  - `get_zscore()`: Calculate current Z-score
  - `open_position()`: Execute beta-hedged opening trades
  - `close_position()`: Execute closing trades
  - `get_position_info()`: Query position status
- **Features**: Cooldown management, beta hedging, position tracking

### 3. PairsManager.py - Lifecycle Management
- **Purpose**: Manage all pairs through their lifecycle
- **State Management**:
  - **Active pairs**: Currently passing cointegration tests
  - **Legacy pairs**: Have positions but failed recent tests
  - **Dormant pairs**: No positions and failed tests
- **Key Methods**:
  - `create_pairs_from_models()`: Create new Pairs objects
  - `get_pairs_with_position()`: Filter pairs with positions
  - `get_pairs_without_position()`: Filter pairs without positions
  - `can_open_new_position()`: Check global position limits
  - `get_sector_concentration()`: Calculate industry exposure

### 4. RiskManagement.py - Two-Tier Risk Control
- **Purpose**: Risk detection only (execution handled by main.py)
- **Design Principle**: Separation of concerns - risk managers detect, main.py executes
- **PortfolioLevelRiskManager**:
  - `is_account_blowup()`: Detects loss > 30% of initial capital
  - `is_excessive_drawdown()`: Detects drawdown > 15% from high water mark
  - `is_high_market_volatility()`: Detects SPY 20-day annualized volatility > 30%
  - `check_sector_concentration()`: Returns sectors exceeding 40% exposure
- **PairLevelRiskManager**:
  - `check_holding_timeout()`: Detects positions held > 30 days
  - `check_position_anomaly()`: Detects partial or same-direction positions
  - `check_pair_drawdown()`: Detects pair drawdown > 20% from pair HWM

### 5. UniverseSelection.py - Stock Selection
- **Purpose**: Monthly universe refresh
- **Two-stage filtering**:
  - Coarse: Price > $20, Volume > $5M, IPO > 3 years
  - Fine: PE < 100, ROE > 0%, Debt/Assets < 80%
- **Sector-based selection**: Top stocks per Morningstar sector
- **Triggers**: Monthly via `Schedule.On()` → `TriggerSelection()`

### 6. Analysis Modules (src/analysis/)
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
    # Check pair-level risks (main.py executes liquidation)
    if pair_level_risk_manager.check_holding_timeout(pair):
        algorithm.Liquidate(pair.symbol1)
        algorithm.Liquidate(pair.symbol2)
        continue

    # Check for trading signals
    signal = pair.get_signal(data)
    if signal in [TradingSignal.CLOSE, TradingSignal.STOP_LOSS]:
        pair.close_position()
```

### Opening Logic (Pairs without Positions)
```python
# Get entry candidates sorted by quality
entry_candidates = pairs_manager.get_entry_candidates(data)

# Calculate dynamic cash buffer (5% of total portfolio value)
cash_buffer = Portfolio.TotalPortfolioValue * 0.05

# Smart allocation: ensure buffer + min_investment after all entries
# Remove low-quality pairs if fund constraint violated
permitted_pairs = filter_by_fund_constraint(entry_candidates, cash_buffer)

# Execute entries with dynamic allocation
for pair, signal, quality_score, planned_pct in permitted_pairs:
    allocation = available_cash * planned_pct  # min_pct + quality_score * range
    pair.open_position(signal, allocation, data)
```

### Position Sizing (v6.4.0)
- **No Hard Pair Limit**: Removed max_holding_pairs constraint (was 5 in v6.2.0)
- **Natural Fund Constraints**: Position count limited by available capital
- **Cash Buffer**: 5% of total portfolio value (dynamic, not fixed to initial capital)
- **Min Position**: 10% of initial capital
- **Max Position**: 30% of initial capital (increased from 25%)
- **Quality-Based Allocation**: `allocation_pct = min_pct + quality_score * (max_pct - min_pct)`
- **Smart Removal**: When funds insufficient, remove lowest quality pairs first
- **Beta Hedging**: `value1 = allocation / (1 + 1/|beta|)`

## Critical Implementation Details

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

## Cross-Module Communication (v6.4.0 OnData)

### Data Flow
1. **Universe Changes**: `OnSecuritiesChanged()` → triggers pair analysis
2. **Analysis Pipeline**: DataProcessor → CointegrationAnalyzer → BayesianModeler → PairSelector
3. **Pair Creation**: Direct Pairs object creation → PairsManager.update_pairs()
4. **Trading Flow**: OnData → Risk detection → main.py execution → Pairs.get_signal() → Trade execution
5. **State Updates**: PairsManager maintains pair lifecycle states (active/legacy/dormant)

### State Management
- **Pair States**: Active (tradeable), Legacy (position only), Dormant (inactive)
- **Position Tracking**: Direct Portfolio queries via Pairs.get_position_info()
- **Risk State**: High water marks tracked in RiskManagement classes
- **Cooldown Tracking**: Per-pair cooldown managed in Pairs objects
- **Fund Constraints**: Dynamic cash buffer and allocation tracked in main.py

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
# [UniverseSelection] 选股日, 共选出: 45
# [AlphaModel] 筛选出协整对: 3
# [PC] 生成 2 组 PortfolioTarget
```

### Module Access Pattern
```python
# Access universe selection results
symbols = self.algorithm.universe_selector.last_fine_selected_symbols

# Access risk manager state
risk_stats = self.algorithm.risk_manager.risk_triggers
```

## Performance Considerations

- **MCMC optimization**: Limited to 2 chains × 500 samples (warmup + posterior) for production speed
- **History requests**: 252-day lookback optimizes accuracy vs. performance
- **Market volatility**: 20-day rolling window with deque to avoid repeated History() calls
- **Selective processing**: Skip failed cointegration tests early
- **Memory management**: Clear outdated references during universe reselection

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

- **v6.4.0** (Jan 2025): Risk management refactor with separation of concerns, removed hard pair limits, intelligent fund allocation
- **v6.3.1** (Jan 2025): Quality scoring system overhaul - replaced correlation with half_life and volatility_ratio, optimized risk parameters
- **v6.2.0**: Complete two-tier risk management system with Portfolio and Pair level controls
- **v6.1.0**: PairsManager architecture optimization - merged PairsFactory functionality
- **v6.0.0**: Major architecture migration from Algorithm Framework to OnData-driven design

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