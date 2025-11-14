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
            'debug_mode': True,                         # True=开发调试(详细日志), False=生产运行(仅关键日志)
            'log_level': 0                              # 0=生产模式(核心日志,10-30年), 1=调试模式(全部日志,1年) 
        }



        # ========== 选股模块配置 ==========
        self.universe_selection = {
            # 基础筛选
            'min_price': 15,                          # 最低股价（美元）
            'min_volume': 5e6,                        # 最低日均成交量（股数）
            'min_days_since_ipo': 360,                # IPO最短时间（天）

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
            'pvalue_threshold': 0.05,                   # Engle-Granger p值阈值

            # 子行业分组
            'min_stocks_per_group': 3,                  # 子行业最少股票数(不足则跳过)
            'max_stocks_per_group': 50,                 # 子行业最多股票数(按市值选TOP)
        }

        # 3. 配对质量评估模块
        self.pair_selector = {
            # 筛选限制
            'max_symbol_repeats': 1,                    # 单股最多配对数(允许高质量股票参与多个配对)

            # 质量门槛
            'min_quality_threshold': 0.60,              # 最低质量分数阈值

            'quality_weights': {
                'half_life': 0.50,                      # 均值回归速度 
                'mean_reversion_certainty': 0.50        # AR(1)显著性 
            },

            'scoring_thresholds': {
                'half_life': {
                    'peak_days': 8,                     # v7.11.0: 峰值8天 (资金效率最优)
                    'sigma_left': 4.0,                  # v7.11.0: 左侧标准差 (4-8天区间,保证5-7天梯度)
                    'sigma_right': 9.0,                 # v7.11.0: 右侧标准差 (8-20天区间,让12-20天缓慢下降)
                    'min_days': 4,                      # 软下界 (4天以下平滑惩罚,避免噪音)
                    'decay_start': 20,                  # v7.11.0: 远端衰减起点 (20天后开始额外衰减)
                    'decay_rate': 0.20                  # v7.11.0: 衰减速率 (放缓至0.20,让20天≈0.42)
                },
                'mean_reversion_certainty': {
                    'time_delta_days': 1.0,              # Δt（日频数据）
                    'logistic_steepness': 2.5,           # a参数: 控制S曲线陡峭度
                    'logistic_midpoint': 2.0,            # b参数: SNR_κ=2 → score=0.5
                    'max_snr_kappa': 10.0                # 上界截断（防止极端值）
                }
            }
        }

        # 4. 贝叶斯建模模块
        self.bayesian_modeler = {
            'mcmc_chains': 4,                           # MCMC链数

            # 先验配置
            'bayesian_priors': {
                'uninformed': {                         # 完全无信息先验(降级方案,OLS失败时使用)
                    'alpha_sigma': 10,                  # 截距项标准差
                    'beta_sigma': 5,                    # 斜率项标准差
                    'sigma_sigma': 5.0,                 # 噪声项标准差

                    # ρ的无信息Beta先验
                    'rho_alpha': 2,                     # Beta(2,2) ≈ 弱信息Uniform
                    'rho_beta': 2
                },
                'informed': {                           # 历史后验先验(强信息,重复建模时使用)
                    'sigma_multiplier': 2.0,            # sigma放大系数
                    'validity_days': 30,                # 历史后验有效期: 上次建模后30天内,复用后验加速收敛; 超过30天则协整关系可能漂移(v7.5.19: 从60天缩短至30天,匹配持仓周期),降级到uninformed prior重新建模
                    # ρ的Beta先验温度化参数
                    'rho_variance_multiplier': 1.2,     # ρ方差放宽系数(τ)
                    'rho_variance_safety': 0.9,         # Beta方差安全边界(c)
                    # σ_η的HalfNormal先验放宽参数
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
            'entry_threshold_lower': 1.2,               # 入场Z-score下限 (提高入场质量,过滤1.0-1.2弱信号)
            'entry_threshold_upper': 1.8,               # 入场Z-score上限 (为止损留0.5σ缓冲,避免即开即止)
            'exit_threshold': 0.3,                      # 出场Z-score阈值 (保持不变,避免假回归)
            'stop_loss_threshold': 2.3,                 # 止损Z-score阈值 (配合1.8上限,留0.5σ缓冲)

            # 仓位管理参数
            'min_investment_ratio': 0.05,               # 质量最低(0.0分)配对投资比例: 5%,同时作为绝对门槛
            'max_investment_ratio': 0.15,               # 质量最高(1.0分)配对投资比例: 15%

            # 保证金管理 (美股规则)
            'margin_requirement_long': 0.5,             # 多头保证金率: 50%
            'margin_requirement_short': 1.5,            # 空头保证金率: 150% (100%借券+50%保证金)
            'margin_usage_ratio': 0.98                  # 保证金使用率: 98% (保留2%动态缓冲)
        }

        # ========== 行业配额配置 (v7.12.0 动态行业分组) ==========
        self.industry_quota = {
            'warmup_days': 180,                         # 预热期: 前180天使用默认配额
            'default_quota': 1,                         # 默认配额: 每个行业最多1个配对

            # 加权收益率分层阈值 (格式: 小数)
            'tier_thresholds': {
                'tier1': 0.05,                          # ≤5%: 1个配对
                'tier2': 0.10,                          # ≤10%: 3个配对
                'tier3': 0.20                           # ≤20%: 6个配对
                                                        # >20%: 9个配对
            },

            # 各层配额数量
            'tier_quotas': {
                'tier1': 1,
                'tier2': 3,
                'tier3': 6,
                'tier4': 9
            }
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
                    'threshold': 0.20,
                    'action': 'portfolio_liquidate_all'
                },
                'portfolio_drawdown': {
                    'enabled': True,
                    'priority': 90,
                    'threshold': 0.10,
                    'action': 'portfolio_liquidate_all'      # 全仓清算
                }
            },

            # ========== Pair层面规则 ==========
            'pair_rules': {
                'pair_anomaly': {
                    'enabled': True,
                    'priority': 100                          # 最高优先级：异常必须立即处理
                },
                'pair_drawdown': {
                    'enabled': True,
                    'priority': 90,
                    'threshold': 0.08,                       # 统一回撤阈值 (单次+累计)
                    'enable_cumulative_check': True          # v7.14.0: 启用累计历史回撤检测
                },
                'holding_timeout': {
                    'enabled': True,
                    'priority': 80,
                    'max_halflife_multiplier': 2.0           # v7.11.0: 动态持有时间 = 半衰期 × 2.0
                }
            }
        }


        # ========== 常量定义区（v7.10.6: 统一管理所有常量、显示文本、冷却天数）==========
        self.constants = {
            # === 1. 交易信号 ===
            'trading_signals': {
                'LONG_SPREAD': 'LONG_SPREAD',
                'SHORT_SPREAD': 'SHORT_SPREAD',
                'CLOSE': 'CLOSE',
                'PAIR_BREAK': 'PAIR_BREAK',  
                'HOLD': 'HOLD',
                'WAIT': 'WAIT',
                'COOLDOWN': 'COOLDOWN',
                'NO_DATA': 'NO_DATA'
            },

            # === 2. 持仓模式 ===
            'position_modes': {
                'NONE': 'NONE',
                'LONG_SPREAD': 'LONG_SPREAD',
                'SHORT_SPREAD': 'SHORT_SPREAD',
                'PARTIAL_LEG1': 'PARTIAL_LEG1',
                'PARTIAL_LEG2': 'PARTIAL_LEG2',
                'ANOMALY_SAME': 'ANOMALY_SAME'
            },

            # === 3. 订单动作 ===
            'order_actions': {
                'OPEN': 'OPEN',
                'CLOSE': 'CLOSE'
            },

            # === 4. 平仓原因（常量+显示文本+冷却天数 统一管理）===
            'close_reasons': {
                # v7.13.0: 正常交易周期结束 - 三分类（统一10天冷却期）
                'MEAN_REVERSION': {
                    'display': '均值回归',
                    'cooldown_days': 10
                },
                'PAIR_BREAK': {
                    'display': '协整破裂',
                    'cooldown_days': 10
                },
                'TIMEOUT': {
                    'display': '持有超时',
                    'cooldown_days': 10
                },

                # 风险触发
                'DRAWDOWN': {
                    'display': '回撤触发',
                    'cooldown_days': 180
                },
                'ANOMALY': {
                    'display': '单腿异常',
                    'cooldown_days': 999999
                },

                # Portfolio级风控（不影响配对选择）
                'PORTFOLIO_DRAWDOWN': {
                    'display': '组合回撤',
                    'cooldown_days': 360
                },
                'ACCOUNT_BLOWUP': {
                    'display': '组合爆仓',
                    'cooldown_days': 999999
                },

                # 补全: 其他常用平仓原因
                'CLOSE': {
                    'display': '正常平仓',
                    'cooldown_days': 10
                },
                'RISK_TRIGGER': {
                    'display': '风险触发',
                    'cooldown_days': 30
                },
                'COOLDOWN_CLEANUP': {
                    'display': '冷却清理',
                    'cooldown_days': 10
                }
            },

            # === 5. Morningstar行业映射（完整55个）===
            'industry_names': {
                # 基础材料 (101xx)
                10110: '农业', 10120: '建材', 10130: '化工',
                10140: '林产品', 10150: '金属矿业', 10160: '钢铁',

                # 消费周期 (102xx)
                10200: '汽车及零部件', 10220: '家具装置', 10230: '房建',
                10240: '服装制造', 10250: '包装容器', 10260: '个人服务',
                10270: '餐厅', 10280: '周期零售', 10290: '旅游休闲',

                # 金融 (103xx)
                10310: '资产管理', 10320: '银行', 10330: '资本市场',
                10340: '保险', 10350: '多元金融', 10360: '信贷服务',

                # 房地产 (104xx)
                10410: '房地产', 10420: 'REITs',

                # 消费防御 (205xx)
                20510: '酒精饮料', 20520: '非酒精饮料', 20525: '消费品',
                20540: '教育', 20550: '防御零售', 20560: '烟草',

                # 医疗保健 (206xx-207xx)
                20610: '生物科技', 20620: '制药', 20630: '医疗计划',
                20645: '医疗服务', 20650: '医疗器械仪器',
                20660: '医疗诊断研究', 20670: '医疗分销',
                20710: '独立电力', 20720: '公用事业',

                # 通信服务 (308xx)
                30810: '电信服务', 30820: '多元媒体', 30830: '互动媒体',

                # 能源 (309xx)
                30910: '油气', 30920: '其他能源',

                # 工业 (310xx)
                31010: '航空国防', 31020: '商业服务', 31030: '企业集团',
                31040: '建筑', 31050: '重型机械', 31060: '工业分销',
                31070: '工业产品', 31080: '运输', 31090: '废物管理',

                # 科技 (311xx)
                31110: '软件', 31120: '硬件', 31130: '半导体',

                # 特殊
                0: '未分类'
            }
        }


    def get_module_config(self, module_name):
        """获取模块配置"""
        return getattr(self, module_name, {})