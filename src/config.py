# region imports
from AlgorithmImports import *
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
            'debug_mode': True,                  # True=开发调试(详细日志), False=生产运行(仅关键日志)

            # 基准配置
            'benchmark_symbol': 'SPY',           # 基准标的
            'benchmark_name': 'S&P 500 ETF',     # 基准名称
            'benchmark_description': 'Standard benchmark for US equity strategies',

            # TODO(v6.8.0): 配置重组计划
            # 以下参数按业务职责应迁移到对应模块：
            # - min_investment_ratio → self.execution (ExecutionManager重构时)
            # - market_condition_* → self.risk_management['market_condition'] (RiskManager重构时)
            # 当前临时保留在main中以保持稳定性

            # 资金管理（临时位置，待迁移）
            'min_investment_ratio': 0.10,        # 最小投资：初始资金的10%

            # 市场条件检查（临时位置，待迁移）
            'market_condition_enabled': True,    # 是否启用市场条件检查
            'vix_threshold': 30,                 # VIX恐慌阈值（前瞻性指标，生产环境）
            'spy_volatility_threshold': 0.25,    # SPY年化波动率阈值（生产环境：25%）
            'spy_volatility_window': 20          # 滚动窗口天数（行业标准）
        }

        # ========== 选股模块配置 ==========
        self.universe_selection = {
            # 基础筛选
            'min_price': 15,                          # 最低股价（美元）
            'min_volume': 5e6,                        # 最低日均成交量
            'min_days_since_ipo': 360,                # IPO最短时间（天）

            # 财务指标阈值
            'max_pe': 100,                            # PE上限
            'min_roe': 0,                             # ROE下限
            'max_debt_ratio': 0.6,                    # 负债率上限
            'max_leverage_ratio': 6,                  # 杠杆率上限（总资产/股东权益）

            # 风险指标
            'max_volatility': 0.5,                    # 年化波动率上限
            'annualization_factor': 252,              # 年化因子（交易日数）

            # 财务筛选器配置（配置化验证逻辑）
            'financial_filters': {
                'pe_ratio': {
                    'enabled': True,
                    'path': 'ValuationRatios.PERatio',
                    'operator': 'lt',
                    'threshold_key': 'max_pe',
                    'fail_key': 'pe_failed'
                },
                'roe': {
                    'enabled': False,
                    'path': 'OperationRatios.ROE.Value',
                    'operator': 'gt',
                    'threshold_key': 'min_roe',
                    'fail_key': 'roe_failed'
                },
                'debt_ratio': {
                    'enabled': True,
                    'path': 'OperationRatios.DebtToAssets.Value',
                    'operator': 'lt',
                    'threshold_key': 'max_debt_ratio',
                    'fail_key': 'debt_failed'
                },
                'leverage': {
                    'enabled': True,
                    'path': 'OperationRatios.FinancialLeverage.Value',
                    'operator': 'lt',
                    'threshold_key': 'max_leverage_ratio',
                    'fail_key': 'leverage_failed'
                }
            }
        }

        # ========== 分析流程配置(按数据流顺序组织) ==========

        # 共享参数(所有分析模块使用)
        self.analysis_shared = {
            'lookback_days': 252,  # 历史数据回看天数(统一)
        }

        # 1. 数据处理模块
        self.data_processor = {
            'data_completeness_ratio': 1.0,  # 数据完整性要求(1.0=100%,恰好252天,无NaN)
        }

        # 2. 协整分析模块
        self.cointegration_analyzer = {
            # 统计检验
            'pvalue_threshold': 0.05,           # Engle-Granger p值阈值(95%置信度)

            # 子行业分组
            'min_stocks_per_group': 3,          # 子行业最少股票数(不足则跳过)
            'max_stocks_per_group': 20,         # 子行业最多股票数(按市值选TOP)
        }

        # 3. 配对质量评估模块
        self.pair_selector = {
            # 筛选限制
            'max_symbol_repeats': 3,            # 单股最多配对数(允许高质量股票参与多个配对)
            'max_pairs': 30,                    # 最大配对数(配合max_symbol_repeats放宽)

            # 质量门槛
            'min_quality_threshold': 0.40,      # 最低质量分数阈值(严格大于,异常beta配对理论上限=0.40)

            # 流动性基准
            'liquidity_benchmark': 5e8,         # 流动性评分基准(5亿美元)

            # 质量评分权重
            'quality_weights': {
                'statistical': 0.10,            # 协整强度(基于p值) - 仅作参考
                'half_life': 0.60,              # 均值回归速度 - 核心指标(大幅提升)
                'liquidity': 0.30               # 流动性(成交量) - 实战必需(提升)
                # 已移除 'volatility_ratio': 与half_life信息重叠
            },

            # 评分阈值
            'scoring_thresholds': {
                'half_life': {
                    'optimal_days': 15,         # 最优半衰期(天,符合日频实际) → 1.0分
                    'max_acceptable_days': 60   # 最大可接受半衰期(包含长周期配对) → 0分
                }
                # 已移除 'volatility_ratio' 配置
            }
        }

        # 4. 贝叶斯建模模块
        self.bayesian_modeler = {
            # MCMC采样参数
            'mcmc_warmup_samples': 500,         # 预热样本数
            'mcmc_posterior_samples': 500,      # 后验样本数
            'mcmc_chains': 2,                   # MCMC链数

            # 先验配置
            'bayesian_priors': {
                'uninformed': {                 # 完全无信息先验(降级方案,OLS失败时使用)
                    'alpha_sigma': 10,          # 截距项标准差
                    'beta_sigma': 5,            # 斜率项标准差
                    'sigma_sigma': 5.0          # 噪声项标准差
                },
                'informed': {                   # 历史后验先验(强信息,重复建模时使用)
                    'sigma_multiplier': 2.0,    # sigma放大系数(从1.5调整为2.0,更宽松)
                    'sample_reduction_factor': 0.5,  # 采样减少比例
                    'validity_days': 60         # 历史后验有效期(天,从252天调整为60天)
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
                    'enabled': True,                     # 重新启用
                    'priority': 100,
                    'threshold': 0.25,                   # 爆仓阈值：亏损25%
                    'cooldown_days': 36500,              # 永久冷却(100年)
                    'action': 'portfolio_liquidate_all'
                },
                'excessive_drawdown': {
                    'enabled': True,                     # 重新启用
                    'priority': 90,
                    'threshold': 0.15,                   # 回撤阈值：15% (生产环境)
                    'cooldown_days': 30,                 # 30天冷却期(可恢复)
                    'action': 'portfolio_liquidate_all'  # 全仓清算(与AccountBlowup相同)
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
                    'action': 'pair_close'               # 订单锁机制已防止重复,无需冷却期
                },
                'position_anomaly': {
                    'enabled': True,                     # Step 2已实现
                    'priority': 100,                     # 最高优先级：异常必须立即处理
                    'action': 'pair_close'               # 统一使用pair_close
                },
                'pair_drawdown': {
                    'enabled': True,                     # Step 3已实现
                    'priority': 50,
                    'threshold': 0.15,                   # 配对回撤阈值：15%
                    'action': 'pair_close'
                }
            }
        }


    def get_module_config(self, module_name):
        """获取模块配置"""
        return getattr(self, module_name, {})