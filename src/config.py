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
            'schedule_frequency': 'MonthStart',  # 每月初
            'schedule_time': (9, 10),            # 9:10 AM

            # 开发配置
            'debug_mode': True                   # True=开发调试(详细日志), False=生产运行(仅关键日志)
        }



        # ========== 选股模块配置 ==========
        self.universe_selection = {
            # 基础筛选
            'min_price': 15,                          # 最低股价（美元）
            'min_volume': 5e6,                        # 最低日均成交量
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
                    'threshold': 100,                 # PE上限
                    'fail_key': 'pe_failed'
                },
                'roe': {
                    'enabled': False,
                    'path': 'OperationRatios.ROE.Value',
                    'operator': 'gt',
                    'threshold': 0,                   # ROE下限
                    'fail_key': 'roe_failed'
                },
                'debt_ratio': {
                    'enabled': True,
                    'path': 'OperationRatios.DebtToAssets.Value',
                    'operator': 'lt',
                    'threshold': 0.6,                 # 负债率上限
                    'fail_key': 'debt_failed'
                },
                'leverage': {
                    'enabled': True,
                    'path': 'OperationRatios.FinancialLeverage.Value',
                    'operator': 'lt',
                    'threshold': 6,                   # 杠杆率上限（总资产/股东权益）
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
            'min_stocks_per_group': 3,                  # 子行业最少股票数(不足则跳过)
            'max_stocks_per_group': 20,                 # 子行业最多股票数(按市值选TOP)
        }

        # 3. 配对质量评估模块
        self.pair_selector = {
            # 筛选限制
            'max_symbol_repeats': 3,                    # 单股最多配对数(允许高质量股票参与多个配对)
            'max_pairs': 30,                            # 最大配对数(配合max_symbol_repeats放宽)

            # 质量门槛
            'min_quality_threshold': 0.40,              # 最低质量分数阈值(严格大于,异常beta配对理论上限=0.40)

            # 流动性基准
            'liquidity_benchmark': 5e8,                 # 流动性评分基准(5亿美元, 每日成交金额)

            # 质量评分权重
            'quality_weights': {
                'statistical': 0.10,                    
                'half_life': 0.60,                   
                'liquidity': 0.30                      
            },

            # 评分阈值
            'scoring_thresholds': {
                'half_life': {
                    'optimal_days': 20,                 # 最优半衰期(天,符合日频实际) → 1.0分
                    'zero_score_threshold': 60          # 得0分的阈值(评分函数上界,从45调整为60天,提升区分度)
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
                    'validity_days': 60                 # 历史后验有效期
                }
            }
        }

        # ========== Pairs/PairsManager 配置 ==========
        self.pairs_trading = {
            # 交易阈值
            'entry_threshold': 1.0,              # 建仓Z-score阈值
            'exit_threshold': 0.3,               # 平仓Z-score阈值
            'stop_threshold': 2.0,               # 止损Z-score阈值
            'pair_cooldown_days': 20,            # 正常清仓后配对的冷却期（天）

            # 仓位管理参数
            'min_investment_ratio': 0.05,        # 质量最低(0.0分)配对投资比例: 5%,同时作为绝对门槛
            'max_investment_ratio': 0.20,        # 质量最高(1.0分)配对投资比例: 20%

            # 保证金管理 (美股规则)
            'margin_requirement_long': 0.5,      # 多头保证金率: 50%
            'margin_requirement_short': 1.5,     # 空头保证金率: 150% (100%借券+50%保证金)
            'margin_usage_ratio': 0.98           # 保证金使用率: 95% (保留5%动态缓冲)
        }



        # ========== 风险管理配置 ==========
        self.risk_management = {
            # 全局开关
            'enabled': True,  # False则完全禁用风控系统

            # ========== 市场条件检查 ==========
            'market_condition': {
                'enabled': True,                     # 是否启用市场条件检查
                'vix_symbol': 'VIX',                 # VIX指数代码
                'vix_resolution': Resolution.Daily,  # VIX数据分辨率
                'vix_threshold': 30,                 # VIX恐慌阈值（前瞻性指标）
                'spy_volatility_threshold': 0.25,    # SPY年化波动率阈值（25%）
                'spy_volatility_window': 20          # 滚动窗口天数（行业标准）
            },

            # ========== Portfolio层面规则 ==========
            'portfolio_rules': {
                'account_blowup': {
                    'enabled': True,                     # 重新启用
                    'priority': 100,
                    'threshold': 0.20,                   # 爆仓阈值
                    'cooldown_days': 36500,              # 永久冷却(100年)
                    'action': 'portfolio_liquidate_all'
                },
                'portfolio_drawdown': {
                    'enabled': True,                     # 重新启用
                    'priority': 90,
                    'threshold': 0.05,                   # 回撤阈值
                    'cooldown_days': 30,                 # 30天冷却期(可恢复)
                    'action': 'portfolio_liquidate_all'  # 全仓清算
                }
            },

            # ========== Pair层面规则 ==========
            'pair_rules': {
                'pair_anomaly': {
                    'enabled': True,                     # Step 2已实现
                    'priority': 100,                     # 最高优先级：异常必须立即处理
                    'cooldown_days': 30                  # per-pair冷却期(30天)
                },
                'pair_drawdown': {
                    'enabled': True,                     # Step 3已实现
                    'priority': 80,
                    'threshold': 0.10,                   # 配对回撤阈值
                    'cooldown_days': 30                  # per-pair冷却期(30天)
                },
                'holding_timeout': {
                    'enabled': True,
                    'priority': 60,
                    'max_days': 30,                      # 最大持仓天数(从45调整为30天,占实际通过上限55天的55%)
                    'cooldown_days': 20                  # per-pair冷却期(30天)
                }
            }
        }


    def get_module_config(self, module_name):
        """获取模块配置"""
        return getattr(self, module_name, {})