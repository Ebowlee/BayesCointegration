# region imports
from AlgorithmImports import *
# endregion


class StrategyConfig:
    """策略配置中心"""

    def __init__(self):
        
        # ========== 主程序配置 ==========
        self.main = {
            # 回测基础配置
            'start_date': (2023, 9, 20),
            'end_date': (2024, 9, 20),
            'cash': 100000,
            'resolution': Resolution.Daily,
            'brokerage_name': BrokerageName.InteractiveBrokersBrokerage,
            'account_type': AccountType.Margin,

            # 选股调度配置
            'schedule_frequency': 'MonthStart',         # 每月初
            'schedule_time': (9, 10),                   # 9:10 AM

            # 开发配置
            'debug_mode': True                          # True=开发调试(详细日志), False=生产运行(仅关键日志)
        }



        # ========== 选股模块配置 ==========
        self.universe_selection = {
            # 基础筛选
            'min_price': 10,                          # 最低股价（美元）
            'min_volume': 1e6,                        # 最低日均成交量（股数）
            'min_days_since_ipo': 360,                # IPO最短时间（天）

            # 风险指标
            'max_volatility': 0.5,                    # 年化波动率上限
            'annualization_factor': 252,              # 年化因子（交易日数）

            # 财务筛选器配置
            'financial_filters': {
                'pe_ratio': {
                    'enabled': True,
                    'path': 'ValuationRatios.PERatio',
                    'operator': 'lt',
                    'threshold': 100,                   # PE上限
                    'fail_key': 'pe_failed'
                },
                'roe': {
                    'enabled': False,
                    'path': 'OperationRatios.ROE.Value',
                    'operator': 'gt',
                    'threshold': 0,                     # ROE下限
                    'fail_key': 'roe_failed'
                },
                'debt_ratio': {
                    'enabled': True,
                    'path': 'OperationRatios.DebtToAssets.Value',
                    'operator': 'lt',
                    'threshold': 0.6,                   # 负债率上限
                    'fail_key': 'debt_failed'
                },
                'leverage': {
                    'enabled': True,
                    'path': 'OperationRatios.FinancialLeverage.Value',
                    'operator': 'lt',
                    'threshold': 6,                     # 杠杆率上限（总资产/股东权益）
                    'fail_key': 'leverage_failed'
                }
            }
        }



        # ========== 分析流程配置(按数据流顺序组织) ==========
        # 共享参数(所有分析模块使用)
        self.analysis_shared = {
            'lookback_days': 252,                       # 历史数据回看天数(统一)
        }

        # 1. 数据处理模块
        self.data_processor = {
            'data_completeness_ratio': 1.0,             # 数据完整性要求(1.0=100%,恰好252天,无NaN)
        }

        # 2. 协整分析模块
        self.cointegration_analyzer = {
            # 统计检验
            'pvalue_threshold': 0.05,                   # Engle-Granger p值阈值(95%置信度)

            # 子行业分组
            'min_stocks_per_group': 4,                  # 子行业最少股票数(不足则跳过)
            'max_stocks_per_group': 40,                 # 子行业最多股票数(按市值选TOP)
        }

        # 3. 配对质量评估模块
        self.pair_selector = {
            # 筛选限制
            'max_symbol_repeats': 3,                    # 单股最多配对数(允许高质量股票参与多个配对)
            'max_pairs': 30,                            # 最大配对数(配合max_symbol_repeats放宽)

            # 质量门槛
            'min_quality_threshold': 0.50,              # 最低质量分数阈值

            # v7.4.0: 四维评分权重体系
            'quality_weights': {
                'half_life': 0.30,                      # 均值回归速度 (使用贝叶斯beta和AR(1) lambda)
                'beta_stability': 0.25,                 # Beta稳定性 (后验标准差)
                'mean_reversion_certainty': 0.30,       # AR(1)显著性 (lambda t统计量)
                'residual_quality': 0.15                # 拟合质量 (残差标准差，替代volatility_ratio)
            },

            'scoring_thresholds': {
                'half_life': {
                    'optimal_min_days': 7,              # 平台期下限(天) - 黄金持仓期起点
                    'optimal_max_days': 15,             # 平台期上限(天) - 黄金持仓期终点
                    'min_acceptable_days': 3,           # 最小可接受半衰期(天)
                    'max_acceptable_days': 30           # 最大可接受半衰期(天)
                },
                'beta_stability': {
                    'decay_factor': 10.0                # 指数衰减因子 (exp(-decay * beta_std))
                },
                'mean_reversion_certainty': {
                    'min_t_stat': 2.0,                  # 最小t统计量阈值 (显著性起点)
                    'max_t_stat': 5.0                   # 最大t统计量阈值 (显著性满分)
                },
                'residual_quality': {
                    'min_residual_std': 0.01,           # 最小残差标准差 (完美拟合)
                    'max_residual_std': 0.20            # 最大残差标准差 (可接受上限)
                }
            }
        }

        # 4. 贝叶斯建模模块
        self.bayesian_modeler = {
            # MCMC采样参数
            'mcmc_warmup_samples': 500,                 # 预热样本数
            'mcmc_posterior_samples': 500,              # 后验样本数
            'mcmc_chains': 2,                           # MCMC链数

            # 先验配置
            'bayesian_priors': {
                'uninformed': {                         # 完全无信息先验(降级方案,OLS失败时使用)
                    'alpha_sigma': 10,                  # 截距项标准差
                    'beta_sigma': 5,                    # 斜率项标准差
                    'sigma_sigma': 5.0                  # 噪声项标准差
                },
                'informed': {                           # 历史后验先验(强信息,重复建模时使用)
                    'sigma_multiplier': 2.0,            # sigma放大系数
                    'sample_reduction_factor': 0.5,     # 采样减少比例
                    'validity_days': 60                 # 历史后验有效期: 上次建模后60天内,复用后验加速收敛; 超过60天则协整关系可能漂移,降级到OLS informed prior重新建模
                }
            }
        }

        # ========== Pairs/PairsManager 配置 ==========
        self.pairs_trading = {
            'entry_threshold_min': 1.00,            # 建仓Z-score下限 (信号足够强)
            'entry_threshold_max': 2.00,            # 建仓Z-score上限 (避免过度偏离)
            'exit_threshold': 0.30,                 # 平仓Z-score阈值
            'stop_threshold': 2.30,                 # 止损Z-score阈值

            'pair_cooldown_days_for_exit': 20,      # 正常回归平仓后的冷却期(天) - Z-score收敛
            'pair_cooldown_days_for_stop': 60,      # 止损平仓后的冷却期(天) - Z-score超限

            # 仓位管理参数
            'min_investment_ratio': 0.05,           # 质量最低(0.0分)配对投资比例: 5%,同时作为绝对门槛
            'max_investment_ratio': 0.25,           # 质量最高(1.0分)配对投资比例: 25%

            # 保证金管理 (美股规则)
            'margin_requirement_long': 0.5,         # 多头保证金率: 50%
            'margin_requirement_short': 1.5,        # 空头保证金率: 150% (100%借券+50%保证金)
            'margin_usage_ratio': 0.98              # 保证金使用率: 98% (保留2%动态缓冲)
        }



        # ========== 风险管理配置 ==========
        self.risk_management = {
            # 全局开关
            'enabled': True,  # False则完全禁用风控系统

            # ========== 市场条件检查 ==========
            'market_condition': {
                'enabled': True,                        # 是否启用市场条件检查
                'vix_symbol': 'VIX',                    # VIX指数代码
                'vix_resolution': Resolution.Daily,     # VIX数据分辨率
                'vix_threshold': 30,                    # VIX恐慌阈值（前瞻性指标）
                'spy_volatility_threshold': 0.25,       # SPY年化波动率阈值（25%）
                'spy_volatility_window': 20             # 滚动窗口天数（行业标准）
            },

            # ========== Portfolio层面规则 ==========
            'portfolio_rules': {
                'account_blowup': {
                    'enabled': True,                     
                    'priority': 100,
                    'threshold': 0.15,                   
                    'cooldown_days': 365,                
                    'action': 'portfolio_liquidate_all'
                },
                'portfolio_drawdown': {
                    'enabled': True,                     
                    'priority': 90,
                    'threshold': 0.075,                  
                    'cooldown_days': 60,                 
                    'action': 'portfolio_liquidate_all'      # 全仓清算
                }
            },

            # ========== Pair层面规则 ==========
            'pair_rules': {
                'pair_anomaly': {
                    'enabled': True,                     
                    'priority': 100,                         # 最高优先级：异常必须立即处理
                    'cooldown_days': 60                 
                },
                'pair_drawdown': {
                    'enabled': True,
                    'priority': 90,
                    'threshold': 0.05,                       # 统一回撤阈值
                    'cooldown_days_for_profit': 20,          # 盈利前提下平仓后冷却期
                    'cooldown_days_for_loss': 60             # 亏损前提下平仓后冷却期
                },
                'holding_timeout': {
                    'enabled': True,
                    'priority': 80,
                    'max_days': 60,                          # 最大持仓天数
                    'cooldown_days': 30                     
                }
            }
        }


    def get_module_config(self, module_name):
        """获取模块配置"""
        return getattr(self, module_name, {})