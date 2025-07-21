# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Codebase Overview

This is a **Bayesian Cointegration** pairs trading strategy built for the QuantConnect platform. The strategy uses advanced statistical methods including Bayesian inference with MCMC sampling to identify and trade mean-reverting relationships between securities within the same industry sectors.

## Architecture Pattern

The strategy follows QuantConnect's **Algorithm Framework** with modular design:

- **main.py**: Central orchestrator using `BayesianCointegrationStrategy(QCAlgorithm)`
- **UniverseSelection**: Multi-stage fundamental screening with sector-based selection (30 stocks per sector)
- **AlphaModel**: Core Bayesian cointegration engine using PyMC for MCMC sampling
- **PortfolioConstruction**: Beta-neutral position sizing with margin considerations
- **RiskManagement**: Multi-layered risk controls (currently disabled)
- **Execution**: Atomic pair execution (currently unused)

Key architectural principle: **Intra-industry pairing only** - securities are paired within the same Morningstar sector, not across the entire universe.

## Development Commands

### Version Control
```bash
# Commit format: v<major>.<minor>.<patch>[_description][@date]
git commit -m "v2.4.8_strategy-optimize@20250720"

# Update CHANGELOG.md after each commit with format:
## [v2.4.8_strategy-optimize@20250720]
- Description of changes
```

### Strategy Configuration
- **Backtest period**: Modify dates in `main.py` Initialize() method
- **Parameters**: All thresholds are currently hard-coded in module `__init__` methods
- **Scheduling**: Monthly universe reselection on first trading day at 9:10 AM

## Critical Implementation Details

### Statistical Engine (AlphaModel)
- **Cointegration testing**: Engle-Granger test with p-value < 0.025
- **Bayesian modeling**: PyMC with 1500 burn-in + 1500 draws, 2 chains
- **Signal thresholds**: Entry ±1.65σ, Exit ±0.3σ, Safety limits ±3.0σ
- **Risk controls**: Max 5 pairs, each stock in max 1 pair

### Universe Selection Logic
1. **Coarse filtering**: Price > $15, Volume > $250M, IPO > 3 years  
2. **Sector grouping**: 8 major sectors with 30 stocks each by market cap
3. **Fundamental filters**: PE < 30, ROE > 5%, Debt-to-Assets < 60%, Leverage < 5x

### Position Sizing (PortfolioConstruction)
- **Equal allocation**: `1/N` where N = number of active pairs
- **Beta hedging**: Long = 1.0, Short = |β| from Bayesian regression
- **Margin**: 50% requirement for short positions

## Cross-Module Communication

### Data Flow
1. UniverseSelection → AlphaModel: `changes.AddedSecurities/RemovedSecurities`
2. AlphaModel → PortfolioConstruction: `Insight.Tag` contains `"symbol1&symbol2|alpha|beta|zscore|num_pairs"`
3. Scheduling: `Schedule.On()` triggers universe reselection monthly

### State Management
- Each module maintains independent state (`self.symbols`, `self.posterior_params`)
- Reference passing: `self.algorithm.universe_selector.last_fine_selected_symbols`
- Lifecycle separation: Selection periods vs. daily operations

## Key Dependencies

### Statistical Libraries
- **PyMC**: Bayesian MCMC sampling (version compatibility critical)
- **statsmodels**: Cointegration testing (coint function)
- **NumPy**: Numerical operations and array handling

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

### Parameter Access Pattern
Currently hard-coded in each module's `__init__`. Future optimization should implement:
```python
default_config = {
    'selection': {'min_price': 15, 'min_volume': 2.5e8},
    'financial': {'max_pe': 30, 'min_roe': 0.05},
    'volatility': {'max_volatility': 0.6}
}
```

## Performance Considerations

- **MCMC optimization**: Limited to 2 chains × 1500 samples for production speed
- **History requests**: 252-day lookback optimizes accuracy vs. performance  
- **Selective processing**: Skip failed cointegration tests early
- **Memory management**: Clear outdated references during universe reselection

## Files to Avoid Modifying

- **config.json**: QuantConnect cloud configuration
- **backtests/**: Historical backtest results and logs
- **QA/**: Technical documentation and reference materials
- **.gitignore**: Properly configured for Python/QuantConnect projects