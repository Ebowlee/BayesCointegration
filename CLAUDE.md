# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Codebase Overview

This is a **Bayesian Cointegration** pairs trading strategy built for the QuantConnect platform. The strategy uses advanced statistical methods including Bayesian inference with MCMC sampling to identify and trade mean-reverting relationships between securities within the same industry sectors.

## Architecture Pattern (v6.2.0 OnData Architecture)

The strategy uses **OnData-driven architecture** (migrated from Algorithm Framework in v6.0.0):

- **main.py**: Central orchestrator using `BayesianCointegrationStrategy(QCAlgorithm)` with OnData event handling
- **UniverseSelection**: Multi-stage fundamental screening with sector-based selection
- **Pairs**: Core pair trading object encapsulating signal generation and trade execution
- **PairsManager**: Lifecycle manager for all pairs (active, legacy, dormant states)
- **RiskManagement**: Two-tier risk control system (Portfolio + Pair level)
- **Analysis modules**: DataProcessor, CointegrationAnalyzer, BayesianModeler, PairSelector

Key architectural principles (v6.2.0):
- **OnData-driven** - All trading logic flows through OnData method, no Framework modules
- **Object-oriented pairs** - Pairs class encapsulates all pair-specific logic
- **Two-tier risk management** - Portfolio-level and Pair-level risk controls
- **Smart lifecycle management** - PairsManager tracks pairs through active/legacy/dormant states
- **Intra-industry pairing** - Securities paired within same Morningstar sector
- **Global constraints** - Max 5 tradeable pairs, 5% cash buffer, dynamic position sizing

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

### Testing Framework
```bash
# Run all tests
python run_tests.py

# Run only unit tests
python run_tests.py --unit

# Run only integration tests
python run_tests.py --integration

# Run specific test module
python run_tests.py test_order_tracker
python run_tests.py test_central_pair_manager
python run_tests.py test_risk_management
python run_tests.py test_strategy_flow

# Run with verbose output
python run_tests.py -v
python run_tests.py --verbose

# Run specific test method directly
python -m unittest tests.unit.test_risk_management.TestRiskManagement.test_single_stop_loss
python -m unittest tests.unit.test_order_tracker.TestOrderTracker.test_pair_entry_tracking
python -m unittest tests.integration.test_strategy_flow.TestStrategyFlow.test_complete_pair_lifecycle
```

### Test Environment Setup
The project uses a custom test environment that mocks QuantConnect components:
- **Test Environment**: Tests run independently of QuantConnect using `tests/mocks/mock_quantconnect.py`
- **Environment Setup**: `tests/setup_test_env.py` configures the mock environment
- **Mock Framework**: Replaces AlgorithmImports with mock implementations for isolated testing

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
- **Purpose**: Portfolio and pair level risk management
- **PortfolioLevelRiskManager**:
  - Account blowup protection (< 20% initial capital)
  - Maximum drawdown control (> 15% from high water mark)
  - Market volatility detection (SPY daily movement > 5%)
  - Sector concentration limits (> 40% in single sector)
- **PairLevelRiskManager** (Generator pattern):
  - Holding period timeout (> 30 days)
  - Position anomaly detection (partial/same-direction)
  - Pair-specific drawdown (> 20% from pair HWM)

### 5. UniverseSelection.py - Stock Selection
- **Purpose**: Monthly universe refresh
- **Two-stage filtering**:
  - Coarse: Price > $20, Volume > $5M, IPO > 3 years
  - Fine: PE < 100, ROE > 0%, Debt/Assets < 80%
- **Sector-based selection**: Top stocks per Morningstar sector
- **Triggers**: Monthly via `Schedule.On()` → `TriggerSelection()`

### 6. Analysis Modules (src/analysis/)
- **DataProcessor**: Clean and prepare historical data
- **CointegrationAnalyzer**: Engle-Granger cointegration tests
- **BayesianModeler**: PyMC MCMC parameter estimation
- **PairSelector**: Quality scoring and pair selection

## Trading Execution Flow (OnData)

### Execution Priority
1. **Strategy Cooldown Check**: Skip if in global cooldown period
2. **Portfolio Risk Management**: Check and handle portfolio-level risks
3. **Pair Risk Management**: Filter risky pairs with positions
4. **Position Management**:
   - Close positions for pairs with exit/stop signals
   - Open new positions for pairs with entry signals

### Closing Logic (Pairs with Positions)
```python
# Only process pairs that have positions
pairs_with_position = pairs_manager.get_pairs_with_position()
for safe_pair in pair_level_risk_manager.manage_position_risks(pairs_with_position):
    signal = safe_pair.get_signal(data)
    if signal in ["CLOSE", "STOP_LOSS"]:
        safe_pair.close_position()
```

### Opening Logic (Pairs without Positions)
```python
# Process pairs without positions
pairs_without_position = pairs_manager.get_pairs_without_position()
# Collect signals, sort by quality_score
# Calculate available cash (Portfolio.Cash - 5% buffer)
# Allocate funds: 10% + quality_score * 15%
# Execute beta-hedged trades via pair.open_position()
```

### Position Sizing
- **Cash Buffer**: 5% of initial capital permanently reserved
- **Min Position**: 10% of initial capital
- **Max Position**: 25% of initial capital
- **Dynamic Allocation**: `allocation = min_pct + quality_score * (max_pct - min_pct)`
- **Beta Hedging**: `value1 = allocation / (1 + 1/|beta|)`

## Critical Implementation Details

### Statistical Engine
- **Cointegration testing**: Engle-Granger test with p-value < 0.05
- **Bayesian modeling**: PyMC with 1000 burn-in + 1000 draws, 2 chains
- **Signal thresholds**: Entry ±1.2σ, Exit ±0.3σ, Stop ±3.0σ
- **Cooldown Period**: 7 days after closing a pair

### Universe Selection Logic
1. **Coarse filtering**: Price > $20, Volume > $5M, IPO > 3 years  
2. **Sector grouping**: 8 major sectors with 30 stocks each by market cap
3. **Fundamental filters**: PE < 100, ROE > 0%, Debt-to-Assets < 80%, Leverage < 8x
4. **Volatility filter**: Annual volatility < 60%

### Position Sizing (PortfolioConstruction)
- **Equal allocation**: Dynamic sizing based on number of active pairs
- **Beta hedging**: Long = 1.0, Short = |β| from Bayesian regression
- **Margin**: 100% requirement for short positions
- **Cash buffer**: 5% for operational needs

## Cross-Module Communication (v6.2.0 OnData)

### Data Flow
1. **Universe Changes**: `OnSecuritiesChanged()` → triggers pair analysis
2. **Analysis Pipeline**: DataProcessor → CointegrationAnalyzer → BayesianModeler → PairSelector
3. **Pair Creation**: PairsManager.create_pairs_from_models() → creates Pairs objects
4. **Trading Flow**: OnData → RiskManagement → Pairs.get_signal() → Trade execution
5. **State Updates**: PairsManager maintains pair lifecycle states

### State Management
- **Pair States**: Active (tradeable), Legacy (position only), Dormant (inactive)
- **Position Tracking**: Direct Portfolio queries via Pairs.get_position_info()
- **Risk State**: High water marks tracked in RiskManagement classes
- **Cooldown Tracking**: Per-pair cooldown managed in Pairs objects
- **Global Constraints**: Max pairs, cash buffer tracked in main.py

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

- **MCMC optimization**: Limited to 2 chains × 1000 samples for production speed
- **History requests**: 252-day lookback optimizes accuracy vs. performance  
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

## Testing Infrastructure

### Test Structure
- **Unit tests**: `tests/unit/` - Test individual modules in isolation
- **Integration tests**: `tests/integration/` - Test cross-module interactions
- **Mock framework**: `tests/mocks/` - Simulated QuantConnect environment

### Key Test Coverage (v2.0.0 Updated)
- **RiskManagement**: Simplified extreme loss detection tests
- **Strategy Flow**: End-to-end pair lifecycle simulation
- **AlphaModel**: Signal generation and portfolio query tests
- **PortfolioConstruction**: Position sizing and state management tests

## Recent Optimization History

- **v6.2.0**: Complete two-tier risk management system with Portfolio and Pair level controls
- **v6.1.0**: PairsManager architecture optimization - merged PairsFactory functionality
- **v6.0.0**: Major architecture migration from Algorithm Framework to OnData-driven design
- **v5.0.0**: Merged Alpha module optimization from feature branch
- **v2.0.0**: Architecture simplification - removed CentralPairManager (~30% code reduction)

## Files to Avoid Modifying

- **config.json**: QuantConnect cloud configuration (contains cloud-id and org-id)
- **backtests/**: Historical backtest results and logs (gitignored but tracked for reference)
- **.gitignore**: Properly configured for Python/QuantConnect projects
- **.claude/agents/**: AI agent definitions (managed separately)

## Project File Organization

- **src/**: Source code modules
  - **alpha/**: AlphaModel modular components (5 specialized modules)
  - **config.py**: Centralized configuration via StrategyConfig class
  - **UniverseSelection.py**: Multi-stage stock filtering
  - **PortfolioConstruction.py**: Position management
  - **RiskManagement.py**: Simplified risk controls
  - **Execution.py**: Order execution (unused)
- **tests/**: Test suite with comprehensive coverage
  - **unit/**: Individual module tests with mock framework
  - **integration/**: Cross-module interaction tests
  - **mocks/**: QuantConnect framework mocks for isolated testing
  - **setup_test_env.py**: Test environment configuration
  - **run_tests.py**: Test runner script
- **docs/**: Documentation and version history
  - **CHANGELOG.md**: Complete version history with detailed change tracking
- **research/**: Jupyter notebooks for strategy research and analysis
- **backtests/**: Local backtest results (gitignored, for reference only)
- **main.py**: Strategy entry point and framework orchestrator

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