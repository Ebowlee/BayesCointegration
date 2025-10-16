# region imports
from AlgorithmImports import *
from QuantConnect.Data.Fundamental import MorningstarSectorCode
# endregion


class StrategyConfig:
    """策略配置中心"""

    def __init__(self):
        # ========== 主程序配置 ==========
        self.main = {
            'start_date': (2023, 9, 20),
            'end_date': (2024, 9, 20),
            'cash': 100000,
            'resolution': Resolution.Daily,
            'brokerage_name': BrokerageName.InteractiveBrokersBrokerage,
            'account_type': AccountType.Margin,
            'schedule_frequency': 'MonthStart',  # 每月初
            'schedule_time': (9, 10),            # 9:10 AM
            'debug_mode': False,                 # True=开发调试(详细日志), False=生产运行(仅关键日志)

            # 资金管理
            'min_investment_ratio': 0.10         # 最小投资：初始资金的10%
        }

        # ========== 选股模块配置 ==========
        self.universe_selection = {
            'max_stocks_per_sector': 15,               # 每行业最多15只
            'min_price': 20,                           # 最低价$20
            'min_volume': 5e6,                         # 最低成交量500万
            'min_days_since_ipo': 1095,                # IPO满3年
            'max_pe': 100,                             # PE上限
            'min_roe': 0,                              # ROE下限
            'max_debt_ratio': 0.7,                     # 负债率上限 (总债务/总资产)
            'max_leverage_ratio': 5,                   # 杠杆率上限 (总资产/股东权益)
            'max_volatility': 0.5                      # 年化波动率上限
        }

        # ========== 分析模块配置 ==========
        self.analysis = {
            'pvalue_threshold': 0.05,                  # 协整p值阈值
            'correlation_threshold': 0.5,              # 相关系数阈值
            'max_symbol_repeats': 1,                   # 单股最多配对数
            'max_pairs': 20,                           # 最大配对数
            'lookback_days': 252,                      # 统一历史数据天数
            'mcmc_warmup_samples': 500,
            'mcmc_posterior_samples': 500,
            'mcmc_chains': 2,
            'data_completeness_ratio': 0.98,           # 统一数据完整性要求
            'liquidity_benchmark': 5e8,                # 流动性评分基准（5亿美元）

            # 质量评分权重
            'quality_weights': {
                'statistical': 0.30,                   # 协整强度
                'half_life': 0.30,                     # 均值回归速度
                'volatility_ratio': 0.20,              # 稳定性
                'liquidity': 0.20                      # 流动性（成交量）
            },

            # 评分阈值
            'scoring_thresholds': {
                'half_life': {
                    'optimal_days': 5,                 # 最优半衰期（天）→ 1.0分
                    'max_acceptable_days': 30          # 最大可接受半衰期（天）→ 0分
                },
                'volatility_ratio': {
                    'optimal_ratio': 0.2,              # 最优波动率比率 → 1.0分
                    'max_acceptable_ratio': 1.0        # 最大可接受比率 → 0分
                }
            },

            # 贝叶斯先验参数
            'bayesian_priors': {
                'uninformed': {                        # 无信息先验（首次建模）
                    'alpha_sigma': 10,                 # 截距项的标准差
                    'beta_sigma': 5,                   # 斜率项的标准差
                    'sigma_sigma': 5.0                 # 噪声项的标准差
                },
                'informed': {                          # 信息先验（动态更新）
                    'sigma_multiplier': 1.5,           # sigma的放大系数
                    'sample_reduction_factor': 0.5     # 动态更新时的采样减少比例
                }
            }
        }

        # ========== 面向对象 Pairs/PairsManager 配置 ==========
        self.pairs_trading = {
            # 交易阈值
            'entry_threshold': 1.0,              # 建仓Z-score阈值
            'exit_threshold': 0.3,               # 平仓Z-score阈值
            'stop_threshold': 3.0,               # 止损Z-score阈值
            'pair_cooldown_days': 10,            # 配对冷却期（天）

            # 控制参数
            'max_holding_days': 30,              # 最大持仓天数

            # 仓位管理参数
            'min_position_pct': 0.10,            # 最小仓位占初始资金比例
            'max_position_pct': 0.30,            # 最大仓位占初始资金比例

            # 保证金管理 (美股规则)
            'margin_requirement_long': 0.5,      # 多头保证金率: 50%
            'margin_requirement_short': 1.5,     # 空头保证金率: 150% (100%借券+50%保证金)
            'margin_usage_ratio': 0.95           # 保证金使用率: 95% (保留5%动态缓冲)
        }

        # ========== 风险管理配置 ==========
        self.risk_management = {
            # 全局开关
            'enabled': True,  # False则完全禁用风控系统

            # ========== Portfolio层面规则 ==========
            'portfolio_rules': {
                'account_blowup': {
                    'enabled': True,
                    'priority': 100,
                    'threshold': 0.25,                   # 爆仓阈值：亏损25%
                    'cooldown_days': 36500,              # 永久冷却(100年)
                    'action': 'portfolio_liquidate_all'
                },
                'excessive_drawdown': {
                    'enabled': True,
                    'priority': 90,
                    'threshold': 0.15,                   # 回撤阈值：15%
                    'cooldown_days': 30,
                    'action': 'portfolio_stop_new_entries'
                },
                'market_volatility': {
                    'enabled': True,
                    'priority': 80,
                    'threshold': 0.28,                   # SPY 20日年化波动率
                    'window_size': 20,                   # 滚动窗口大小
                    'cooldown_days': 14,
                    'action': 'portfolio_reduce_exposure_50'
                },
                'sector_concentration': {
                    'enabled': False,                    # 暂不启用
                    'priority': 70,
                    'threshold': 0.35,                   # 行业集中度触发线：35%
                    'target_exposure': 0.25,             # 目标集中度：25%
                    'action': 'portfolio_rebalance_sectors'
                }
            },

            # ========== Pair层面规则 ==========
            'pair_rules': {
                'holding_timeout': {
                    'enabled': True,
                    'priority': 60,
                    'max_days': 30,                      # 最大持仓天数
                    'cooldown_days': 15,                 # 冷却期：避免立即重开仓
                    'action': 'pair_close'
                },
                'position_anomaly': {
                    'enabled': True,
                    'priority': 100,                     # 最高优先级：异常必须立即处理
                    'cooldown_days': 1,                  # 短冷却：快速重试
                    'action': 'pair_liquidate'
                },
                'pair_drawdown': {
                    'enabled': True,
                    'priority': 50,
                    'threshold': 0.15,                   # 配对回撤阈值：15%
                    'cooldown_days': 30,                 # 止损后充分冷却
                    'action': 'pair_close'
                }
            }
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