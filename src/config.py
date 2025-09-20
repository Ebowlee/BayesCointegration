# region imports
from AlgorithmImports import *
from QuantConnect.Data.Fundamental import MorningstarSectorCode
# endregion


class StrategyConfig:
    """策略配置中心"""

    def __init__(self):
        # ========== 主程序配置 ==========
        self.main = {
            'start_date': (2024, 6, 20),
            'end_date': (2024, 9, 20),
            'cash': 100000,
            'resolution': Resolution.Daily,
            'brokerage_name': BrokerageName.InteractiveBrokersBrokerage,
            'account_type': AccountType.Margin,
            'schedule_frequency': 'MonthStart',  # 每月初
            'schedule_time': (9, 10),            # 9:10 AM
            'portfolio_max_drawdown': 0.1,       # 组合最大回撤10%
            'portfolio_max_sector_exposure': 0.3, # 行业最大暴露30%
            'debug_level': 1                     # 0=不输出, 1=输出详细统计
        }

        # ========== 选股模块配置 ==========
        self.universe_selection = {
            'max_stocks_per_sector': 20,         # 每行业最多20只
            'min_price': 20,                     # 最低价$20
            'min_volume': 5e6,                   # 最低成交量500万
            'min_days_since_ipo': 1095,          # IPO满3年
            'max_pe': 100,                       # PE上限
            'min_roe': 0,                        # ROE下限
            'max_debt_ratio': 0.8,               # 负债率上限
            'max_leverage_ratio': 8,             # 杠杆率上限
            'max_volatility': 0.6,               # 年化波动率上限
            'volatility_lookback_days': 252,
            'volatility_data_completeness_ratio': 0.98
        }

        # ========== Alpha模块配置 ==========
        self.alpha_model = {
            'pvalue_threshold': 0.05,            # 协整p值阈值
            'correlation_threshold': 0.7,        # 相关性阈值
            'max_symbol_repeats': 1,             # 单股最多配对数
            'max_pairs': 20,                     # 最大配对数
            'lookback_period': 252,
            'mcmc_warmup_samples': 1000,
            'mcmc_posterior_samples': 1000,
            'mcmc_chains': 2,
            'entry_threshold': 1.2,              # 建仓Z-score阈值
            'exit_threshold': 0.3,               # 平仓Z-score阈值
            'upper_limit': 3.0,                  # Z-score上限
            'lower_limit': -3.0,                 # Z-score下限
            'flat_signal_duration_days': 5,
            'entry_signal_duration_days': 3,
            'min_data_completeness_ratio': 0.98,
            # 配对质量权重
            'quality_weights': {
                'statistical': 0.4,
                'correlation': 0.2,
                'liquidity': 0.4
            },
            # 市场风控参数（SignalGenerator使用）
            'market_severe_threshold': 0.05,     # 市场剧烈波动阈值
            'market_cooldown_days': 14          # 市场冷静期
        }

        # ========== 仓位构建配置 ==========
        self.portfolio_construction = {
            'margin_rate': 1.0,
            'max_position_per_pair': 0.15,      # 单对最大仓位
            'min_position_per_pair': 0.05,      # 单对最小仓位
            'cash_buffer': 0.05,                # 现金缓冲
            'cooldown_days': 7,                 # 冷却期
            'market_severe_threshold': 0.05,    # 市场剧烈波动阈值
            'market_cooldown_days': 14          # 市场冷静期
        }

        # ========== 风险管理配置 ==========
        self.risk_management = {
            'max_holding_days': 30,             # 最大持仓天数
            'cooldown_days': 7,
            'max_pair_drawdown': 0.20,          # 配对最大回撤
            'max_single_drawdown': 0.30,        # 单边最大回撤
            'sector_exposure_threshold': 0.30   # 行业集中度阈值
        }

        # ========== 行业映射 ==========
        self.sector_code_to_name = {
            MorningstarSectorCode.Technology: "Technology",
            MorningstarSectorCode.Healthcare: "Healthcare",
            MorningstarSectorCode.Energy: "Energy",
            MorningstarSectorCode.ConsumerDefensive: "ConsumerDefensive",
            MorningstarSectorCode.ConsumerCyclical: "ConsumerCyclical",
            MorningstarSectorCode.CommunicationServices: "CommunicationServices",
            MorningstarSectorCode.Industrials: "Industrials",
            MorningstarSectorCode.Utilities: "Utilities"
        }
        self.sector_name_to_code = {v: k for k, v in self.sector_code_to_name.items()}

    def get_module_config(self, module_name):
        """获取模块配置"""
        return getattr(self, module_name, {})