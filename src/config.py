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
            'min_volume': 5e6,                        # 最低日均成交量（股数）
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
            'pvalue_threshold': 0.01,                   # Engle-Granger p值阈值

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

            # 二维评分权重体系 (v7.5.23: 移除beta_stability维度)
            'quality_weights': {
                'half_life': 0.60,                      # 均值回归速度 (最独立+预测力最强,准确率57%)
                'mean_reversion_certainty': 0.40        # AR(1)显著性 (理论核心,预测力中等50%)
            },

            'scoring_thresholds': {
                # 非对称高斯评分 (改良C方案配套, 阈值优先设计)
                # 设计理念: 8天峰值平衡统计质量与30天timeout安全性
                # 核心区间: 5-10天 (≥0.75), 可接受: 4-12天 (≥0.50)
                'half_life': {
                    'peak_days': 8,                     # 峰值 (统计质量+timeout安全性最优平衡)
                    'sigma_left': 3.5,                  # 左侧标准差 (4-8天区间,保证6天≈0.90)
                    'sigma_right': 4.5,                 # 右侧标准差 (8-12天区间,保证10天≈0.85, 12天≈0.65)
                    'min_days': 4,                      # 软下界 (4天以下平滑惩罚,避免噪音)
                    'decay_start': 12,                  # 远端衰减起点 (12天后快速排除)
                    'decay_rate': 0.6                   # 衰减速率 (15天≈0.18)
                },
                # v7.5.23: 移除beta_stability维度 (与MR重叠50%,所有配对评分0.97-0.99无区分度)
                'mean_reversion_certainty': {
                    # v7.5.5: κ-based SNR（连续时间均值回归率，频率不变）
                    'time_delta_days': 1.0,              # Δt（日频数据）
                    'logistic_steepness': 2.5,           # a参数: 控制S曲线陡峭度
                    'logistic_midpoint': 2.0,            # b参数: SNR_κ=2 → score=0.5
                    'max_snr_kappa': 10.0                # 上界截断（防止极端值）
                }
                # v7.5.22: 移除residual_quality维度 (预测失败率57%, 历史拟合≠未来预测)
            }
        }

        # 4. 贝叶斯建模模块
        self.bayesian_modeler = {
            'mcmc_chains': 2,                           # MCMC链数

            # 先验配置
            'bayesian_priors': {
                'uninformed': {                         # 完全无信息先验(降级方案,OLS失败时使用)
                    'alpha_sigma': 10,                  # 截距项标准差
                    'beta_sigma': 5,                    # 斜率项标准差
                    'sigma_sigma': 5.0,                 # 噪声项标准差
                    # v7.5.20: ρ的无信息Beta先验
                    'rho_alpha': 2,                     # Beta(2,2) ≈ 弱信息Uniform
                    'rho_beta': 2
                },
                'informed': {                           # 历史后验先验(强信息,重复建模时使用)
                    'sigma_multiplier': 2.0,            # sigma放大系数
                    'validity_days': 30,                # 历史后验有效期: 上次建模后30天内,复用后验加速收敛; 超过30天则协整关系可能漂移(v7.5.19: 从60天缩短至30天,匹配持仓周期),降级到uninformed prior重新建模
                    # v7.5.20: ρ的Beta先验温度化参数
                    'rho_variance_multiplier': 1.2,     # ρ方差放宽系数(τ)
                    'rho_variance_safety': 0.9,         # Beta方差安全边界(c)
                    # v7.5.20: σ_η的HalfNormal先验放宽参数
                    'sigma_eta_multiplier': 2.5         # σ_η标准差放宽系数
                },
                'joint_single_stage': {                 # 单阶段联合模型配置
                    'sigma_eta_prior': 0.1,             # AR(1)创新噪声η的HalfNormal先验参数(σ_η ~ HalfNormal(0.1), 预期小噪声, log价差残差通常0.01-0.10)
                    'mcmc_warmup': 1000,                # MCMC预热样本数（所有先验统一使用）
                    'mcmc_draws': 1000,                 # MCMC后验样本数（所有先验统一使用）
                    'enable': True                      # 是否启用联合模型(默认启用)
                }
            }
        }

        # ========== Pairs/PairsManager 配置 ==========
        self.pairs_trading = {
            'entry_threshold_lower': 1.2,           # 入场Z-score下限 (提高入场质量,过滤1.0-1.2弱信号)
            'entry_threshold_upper': 1.8,           # 入场Z-score上限 (为止损留0.5σ缓冲,避免即开即止)
            'exit_threshold': 0.3,                  # 出场Z-score阈值 (保持不变,避免假回归)
            'stop_loss_threshold': 2.3,             # 止损Z-score阈值 (配合1.8上限,留0.5σ缓冲)

            'pair_cooldown_days_for_exit': 15,      # 正常回归平仓后的冷却期(天) - Z-score收敛
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
                    'cooldown_days_for_profit': 15,          # 盈利前提下平仓后冷却期
                    'cooldown_days_for_loss': 60             # 亏损前提下平仓后冷却期
                },
                'holding_timeout': {
                    'enabled': True,
                    'priority': 80,
                    'max_days': 30,                          # 最大持仓天数
                    'cooldown_days': 60                     
                }
            }
        }


    def get_module_config(self, module_name):
        """获取模块配置"""
        return getattr(self, module_name, {})