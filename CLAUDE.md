# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Codebase Overview

This is a **Bayesian Cointegration** pairs trading strategy built for the QuantConnect platform. The strategy uses advanced statistical methods including Bayesian inference with MCMC sampling to identify and trade mean-reverting relationships between securities within the same industry sectors.

## Architecture Pattern

The strategy follows QuantConnect's **Algorithm Framework** with modular design:

- **main.py**: Central orchestrator using `BayesianCointegrationStrategy(QCAlgorithm)`
- **UniverseSelection**: Multi-stage fundamental screening with sector-based selection (20 stocks per sector)
- **AlphaModel**: Core Bayesian cointegration engine using PyMC for MCMC sampling
- **PortfolioConstruction**: Beta-neutral position sizing with margin considerations
- **RiskManagement**: Multi-layered risk controls (max 4 pairs globally, 30-day holding limit)
- **Execution**: Atomic pair execution (currently unused)

### Supporting Modules
- **OrderTracker**: Tracks order lifecycle and pair states, enforces cooldown periods
- **PairRegistry**: Maintains active pair mappings and provides fast lookups
- **RiskCalculator**: Computes pair drawdowns and position metrics (planned)

Key architectural principles:
- **Intra-industry pairing only** - securities are paired within the same Morningstar sector
- **Global pair limit** - maximum 4 active pairs across entire portfolio
- **Leverage** - 2x through InteractiveBrokers margin account

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
python run_tests.py test_pair_registry  
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

# Update CHANGELOG.md after each commit with format:
## [v2.4.8_strategy-optimize@20250720]
- Description of changes
```

## Module Architecture and Interaction

### 1. main.py - Strategy Orchestrator
- **Purpose**: Central configuration and framework setup
- **Key Components**:
  - `StrategyConfig`: Centralized parameter management
  - `BayesianCointegrationStrategy`: Main algorithm class
  - Framework module initialization
- **Configuration**: All strategy parameters in `StrategyConfig` class

### 2. UniverseSelection.py - Stock Selection
- **Purpose**: Two-stage filtering (Coarse → Fine) for tradeable universe
- **Key Methods**:
  - `_select_coarse()`: Price/volume/IPO filtering
  - `_select_fine()`: Financial metrics and volatility filtering
  - `_group_and_sort_by_sector()`: Sector-based grouping
- **Triggers**: Monthly via `Schedule.On()` → `TriggerSelection()`

### 3. AlphaModel.py - Signal Generation
- **Purpose**: Bayesian cointegration analysis and trading signals
- **Components**:
  - `DataProcessor`: Historical data handling
  - `CointegrationAnalyzer`: Statistical pair testing
  - `BayesianRegressor`: MCMC parameter estimation
  - `SignalGenerator`: Z-score based signal creation
- **Signal Types**: Up/Down (entry), Flat (exit)

### 4. PortfolioConstruction.py - Position Management
- **Purpose**: Convert signals to position targets
- **Key Features**:
  - Dynamic position sizing (5-15% per pair)
  - Beta-neutral hedging
  - Quality score based allocation
- **Signal Processing**: Parses Insight.Tag for parameters

### 5. RiskManagement.py - Risk Control
- **Purpose**: Independent risk monitoring and control
- **Risk Limits**:
  - Max holding period: 30 days (reduced from 60)
  - Pair drawdown: 10%
  - Single asset drawdown: 20%
  - Cooldown period: 7 days after exit
- **Execution**: Daily automatic checks via `ManageRisk()`

## Critical Implementation Details

### Statistical Engine (AlphaModel)
- **Cointegration testing**: Engle-Granger test with p-value < 0.05
- **Bayesian modeling**: PyMC with 1000 burn-in + 1000 draws, 2 chains
- **Signal thresholds**: Entry ±1.2σ, Exit ±0.3σ, Safety limits ±3.0σ
- **Risk controls**: Max 20 pairs analyzed, each stock in max 1 pair

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

## Cross-Module Communication

### Data Flow
1. UniverseSelection → AlphaModel: `changes.AddedSecurities/RemovedSecurities`
2. AlphaModel → PortfolioConstruction: `Insight.Tag` contains `"symbol1&symbol2|alpha|beta|zscore|quality_score"`
3. Scheduling: `Schedule.On()` triggers universe reselection monthly
4. Direct state access: Modules can access each other via `self.algorithm`

### State Management
- **Global state**: No external state files; all state in module instances
- **Module states**: Each maintains independent state (`self.symbols`, `self.posterior_params`)
- **Position tracking**: Direct portfolio queries, no complex order tracking
- **Lifecycle**: Selection periods vs. daily operations

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
- All parameters hard-coded in `StrategyConfig` class in main.py
- Module-specific parameters passed via config dictionary
- No external configuration files

### Future Optimization
- Consider moving to JSON/YAML configuration
- Implement parameter optimization framework
- Add runtime parameter validation

## Testing Infrastructure

### Test Structure
- **Unit tests**: `tests/unit/` - Test individual modules in isolation
- **Integration tests**: `tests/integration/` - Test cross-module interactions
- **Mock framework**: `tests/mocks/` - Simulated QuantConnect environment

### Key Test Coverage
- **OrderTracker**: Order lifecycle, pair matching, cooldown tracking
- **PairRegistry**: Active pair management, symbol lookups
- **RiskManagement**: Stop-loss triggers, holding limits, drawdown calculations
- **Strategy Flow**: End-to-end pair lifecycle simulation

## Recent Optimization History

- **v3.1.0**: Critical fixes - signal duration optimization (5 days flat, 3 days entry), holding time calculation fix, architecture refactoring with dependency injection
- **v3.0.0**: Added comprehensive test framework and AI agent system
- **v2.17.0**: Implemented complete RiskManagement module with 3-layer risk controls
- **v2.16.0**: Verified PortfolioConstruction module functionality
- **v2.15.0**: Made quality score weights configurable
- **Module simplification**: Used `quantconnect-code-simplifier` agent for cleaner code

## Files to Avoid Modifying

- **config.json**: QuantConnect cloud configuration (contains cloud-id and org-id)
- **backtests/**: Historical backtest results and logs (gitignored but tracked for reference)
- **.gitignore**: Properly configured for Python/QuantConnect projects
- **.claude/agents/**: AI agent definitions (managed separately)

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