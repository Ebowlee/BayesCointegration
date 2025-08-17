# region imports
from AlgorithmImports import *
from QuantConnect.Data.Fundamental import MorningstarSectorCode
# endregion


class StrategyConfig:
    """
    策略参数统一配置类
    所有参数集中管理，便于调整和维护
    """
    
    def __init__(self):
        # Main 配置 - 主程序参数
        self.main = {
            'start_date': (2024, 6, 20),
            'end_date': (2024, 9, 20),
            'cash': 100000,
            'resolution': Resolution.Daily,
            'brokerage_name': BrokerageName.InteractiveBrokersBrokerage,
            'account_type': AccountType.Margin,
            'schedule_frequency': 'MonthStart',
            'schedule_time': (9, 10),
            'portfolio_max_drawdown': 0.1,
            'portfolio_max_sector_exposure': 0.3,
            'debug_level': 1                                # 0=不输出日志, 1=只输出重要日志, 2=输出详细日志, 3=输出所有调试信息
        }
        
        # UniverseSelection 配置 - 选股模块参数
        self.universe_selection = {
            'max_stocks_per_sector': 20,                    # 每个行业最多选股数量
            'min_price': 20,                                # 最低价格20美元
            'min_volume': 5e6,                              # 最低成交量500万股
            'min_days_since_ipo': 1095,                     # IPO满3年
            'max_pe': 100,                                  # PE上限100
            'min_roe': 0,                                   # ROE下限0
            'max_debt_ratio': 0.8,                          # 债务比率上限80%
            'max_leverage_ratio': 8,                        # 杠杆率上限8倍
            'max_volatility': 0.6,                          # 年化波动率上限60%
            'volatility_lookback_days': 252,                # 波动率计算天数
            'volatility_data_completeness_ratio': 0.98      # 数据完整性要求98%
        }
        
        # AlphaModel 配置 - 信号生成模块参数
        self.alpha_model = {
            'pvalue_threshold': 0.05,                       # 协整检验p值阈值
            'correlation_threshold': 0.7,                   # 相关性阈值
            'max_symbol_repeats': 1,                        # 单股票最多参与配对数
            'max_pairs': 20,                                # 最大配对数量
            'lookback_period': 252,                         # 历史数据回看天数
            'mcmc_warmup_samples': 1000,                    # MCMC预热采样数
            'mcmc_posterior_samples': 1000,                 # MCMC后验采样数
            'mcmc_chains': 2,                                # MCMC链数
            'entry_threshold': 1.2,                         # 建仓阈值（标准差倍数）
            'exit_threshold': 0.3,                          # 平仓阈值（标准差倍数）
            'upper_limit': 3.0,                             # Z-score上限
            'lower_limit': -3.0,                            # Z-score下限
            'flat_signal_duration_days': 5,                 # 平仓信号有效期（天）
            'entry_signal_duration_days': 3,                # 建仓信号有效期（天）
            'min_data_completeness_ratio': 0.98,            # 数据完整性最低要求
            # 配对质量评分权重
            'quality_weights': {
                'statistical': 0.4,                         # 统计显著性权重
                'correlation': 0.2,                         # 相关性权重
                'liquidity': 0.4                            # 流动性匹配权重
            }
        }
        
        # PortfolioConstruction 配置 - 仓位构建模块参数
        self.portfolio_construction = {
            'margin_rate': 1.0,                             # 保证金率
            'max_position_per_pair': 0.15,                  # 单对最大仓位15%
            'min_position_per_pair': 0.05,                  # 单对最小仓位5%
            'cash_buffer': 0.05,                            # 现金缓冲5%
            'cooldown_days': 7,                             # 冷却期天数（避免频繁进出）
            'market_severe_threshold': 0.05,                # SPY单日跌5%触发市场冷静期
            'market_cooldown_days': 14                      # 市场冷静期14天
        }
        
        # RiskManagement 配置 - 风险管理模块参数
        self.risk_management = {
            'max_holding_days': 30,                         # 最大持仓天数
            'cooldown_days': 7,                             # 冷却期天数
            'max_pair_drawdown': 0.20,                      # 配对最大回撤20%
            'max_single_drawdown': 0.30,                    # 单边最大回撤30%
            'sector_exposure_threshold': 0.30               # 行业集中度阈值30%
        }
        
        # CentralPairManager 配置 - 中央配对管理器参数
        self.central_pair_manager = {
            'enabled': True,                                # 是否启用CPM
            'max_pairs': 20,                                # 最大配对数量
            'max_symbol_repeats': 1,                        # 单股票最多参与配对数
            'cooldown_days': 7,                             # 冷却期天数
            'max_holding_days': 30,                         # 最大持仓天数
            'min_quality_score': 0.3                        # 最低质量分数
        }
        
        # 行业映射配置 - Morningstar行业代码与名称映射
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
        """
        获取特定模块的配置
        
        Args:
            module_name: 模块名称 (如 'alpha_model', 'risk_management' 等)
            
        Returns:
            dict: 模块配置字典，如果模块不存在返回空字典
        """
        return getattr(self, module_name, {})