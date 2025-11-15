# region imports
from AlgorithmImports import *
from dataclasses import dataclass, field
from typing import Dict
# endregion


# ============================================================================
# 第一部分: 业务参数配置类 (经常调整的回测参数)
# ============================================================================

@dataclass
class MainConfig:
    """主程序配置 - 回测基础参数"""

    # 回测基础配置
    start_date: tuple = (2023, 9, 20)
    end_date: tuple = (2024, 9, 20)
    cash: int = 100000
    resolution: Resolution = Resolution.Daily
    brokerage_name: BrokerageName = BrokerageName.InteractiveBrokersBrokerage
    account_type: AccountType = AccountType.Margin

    # 选股调度配置
    schedule_frequency: str = 'MonthStart'                  # 每月初
    schedule_time: tuple = (9, 10)                          # 9:10 AM

    # 开发配置
    debug_mode: bool = True                                 # True=开发调试(详细日志), False=生产运行(仅关键日志)
    log_level: int = 1                                      # 0=生产模式(核心日志,10-30年), 1=调试模式(全部日志,1年)


@dataclass
class UniverseConfig:
    """选股配置 - 筛选参数"""

    # 基础筛选
    min_price: float = 15                                   # 最低股价（美元）
    min_volume: float = 5e6                                 # 最低日均成交量（股数）
    min_days_since_ipo: int = 360                           

    # 财务筛选器配置 (嵌套保留,因为逻辑上是一组)
    financial_filters: Dict = field(default_factory=lambda: {
        'pe_ratio': {
            'enabled': True,
            'path': 'ValuationRatios.PERatio',
            'operator': 'lt',
            'threshold': 100,
            'fail_key': 'pe_failed'
        },
        'roe': {
            'enabled': False,
            'path': 'OperationRatios.ROE.Value',
            'operator': 'gt',
            'threshold': 0,
            'fail_key': 'roe_failed'
        },
        'debt_ratio': {
            'enabled': True,
            'path': 'OperationRatios.DebtToAssets.Value',
            'operator': 'lt',
            'threshold': 0.6,
            'fail_key': 'debt_failed'
        },
        'leverage': {
            'enabled': True,
            'path': 'OperationRatios.FinancialLeverage.Value',
            'operator': 'lt',
            'threshold': 6,
            'fail_key': 'leverage_failed'
        }
    })


@dataclass
class PairsTradingConfig:
    """配对交易配置 - 信号/仓位参数"""

    # 信号阈值
    entry_threshold_lower: float = 1.2                      # 入场Z-score下限
    entry_threshold_upper: float = 1.8                      # 入场Z-score上限
    exit_threshold: float = 0.3                             # 出场Z-score阈值
    stop_loss_threshold: float = 2.3                        # 止损Z-score阈值

    # 仓位管理参数
    min_investment_ratio: float = 0.05                      # 质量最低(0.0分)配对投资比例: 5%
    max_investment_ratio: float = 0.15                      # 质量最高(1.0分)配对投资比例: 15%

    # 保证金管理 (美股规则)
    margin_requirement_long: float = 0.5                    # 多头保证金率: 50%
    margin_requirement_short: float = 1.5                   # 空头保证金率: 150%
    margin_usage_ratio: float = 0.98                        # 保证金使用率: 98%


# ============================================================================
# 第二部分: 算法参数配置类 (学术研究确定后很少改动)
# ============================================================================

@dataclass
class CointegrationConfig:
    """协整分析配置"""

    # 统计检验
    pvalue_threshold: float = 0.05                          # Engle-Granger p值阈值

    # 子行业分组
    min_stocks_per_group: int = 3                           # 子行业最少股票数
    max_stocks_per_group: int = 50                          # 子行业最多股票数


@dataclass
class PairSelectorConfig:
    """配对质量评估配置"""

    # 筛选限制
    max_symbol_repeats: int = 1                             # 单股最多配对数

    # 质量门槛
    min_quality_threshold: float = 0.60                     # 最低质量分数阈值

    # 质量权重
    quality_weights: Dict = field(default_factory=lambda: {
        'half_life': 0.50,                                  
        'mean_reversion_certainty': 0.50                    
    })

    # 评分阈值 (复杂嵌套保留字典)
    scoring_thresholds: Dict = field(default_factory=lambda: {
        'half_life': {
            'peak_days': 8,
            'sigma_left': 4.0,
            'sigma_right': 9.0,
            'min_days': 4,
            'decay_start': 20,
            'decay_rate': 0.20
        },
        'mean_reversion_certainty': {
            'time_delta_days': 1.0,
            'logistic_steepness': 2.5,
            'logistic_midpoint': 2.0,
            'max_snr_kappa': 10.0
        }
    })


# ============================================================================
# 第三部分: 常量定义类 (永久不变的枚举映射)
# ============================================================================

class Constants:
    """常量定义 - 枚举映射和配置常量"""

    # === 1. 交易信号 ===
    TRADING_SIGNALS = {
        'LONG_SPREAD': 'LONG_SPREAD',
        'SHORT_SPREAD': 'SHORT_SPREAD',
        'CLOSE': 'CLOSE',
        'PAIR_BREAK': 'PAIR_BREAK',
        'HOLD': 'HOLD',
        'WAIT': 'WAIT',
        'COOLDOWN': 'COOLDOWN',
        'NO_DATA': 'NO_DATA'
    }

    # === 2. 持仓模式 ===
    POSITION_MODES = {
        'NONE': 'NONE',
        'LONG_SPREAD': 'LONG_SPREAD',
        'SHORT_SPREAD': 'SHORT_SPREAD',
        'PARTIAL_LEG1': 'PARTIAL_LEG1',
        'PARTIAL_LEG2': 'PARTIAL_LEG2',
        'ANOMALY_SAME': 'ANOMALY_SAME'
    }

    # === 3. 订单动作 ===
    ORDER_ACTIONS = {
        'OPEN': 'OPEN',
        'CLOSE': 'CLOSE'
    }

    # === 4. 平仓原因（常量+显示文本+分类）===
    CLOSE_REASONS = {
        # === 组1: 正常交易信号触发 ===
        'MEAN_REVERSION': {
            'display': '均值回归',
            'cooldown_days': 10,                                # Pairs层冷却期
            'category': 'NORMAL_SIGNAL'                         
        },
        'PAIR_BREAK': {
            'display': '协整破裂',
            'cooldown_days': 30,                                # Pairs层冷却期
            'category': 'NORMAL_SIGNAL'
        },

        # === 组2: Pair级风控触发 ===
        'TIMEOUT': {
            'display': '持有超时',
            'category': 'PAIR_RISK'                             # Pair风控触发
        },
        'CUMULATIVE_LOSS': {
            'display': '累计亏损',
            'category': 'PAIR_RISK'
        },
        'DRAWDOWN': {
            'display': '回撤触发',
            'category': 'PAIR_RISK'
        },
        'ANOMALY': {
            'display': '单腿异常',
            'category': 'PAIR_RISK'
        },

        # === 组3: Portfolio级风控 ===
        'PORTFOLIO_DRAWDOWN': {
            'display': '组合回撤',
            'category': 'PORTFOLIO_RISK'                        # Portfolio风控触发
        },
        'ACCOUNT_BLOWUP': {
            'display': '组合爆仓',
            'category': 'PORTFOLIO_RISK'
        }
    }

    # === 5. Morningstar行业映射（完整55个）===
    INDUSTRY_NAMES = {
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


# ============================================================================
# 统一配置类 (对外接口 - 向后兼容)
# ============================================================================

class StrategyConfig:
    """策略配置中心 - 统一访问入口"""

    def __init__(self):

        # ========== 第一部分: 业务参数 (经常调整) ==========
        self.main = MainConfig()
        self.universe_selection = UniverseConfig()
        self.pairs_trading = PairsTradingConfig()

        # 行业配额 (保持字典,结构简单)
        self.industry_quota = {
            'warmup_days': 180,
            'default_quota': 1,

            'tier_thresholds': {
                'tier1': 0.05,
                'tier2': 0.10,
                'tier3': 0.20
            },

            'tier_quotas': {
                'tier1': 1,
                'tier2': 3,
                'tier3': 6,
                'tier4': 9
            }
        }


        # ========== 第二部分: 算法参数 (稳定不变) ==========
        # 共享参数
        self.analysis_shared = {
            'lookback_days': 252
        }

        # 数据处理模块
        self.data_processor = {
            'data_completeness_ratio': 1.0
        }

        # 协整分析模块
        self.cointegration_analyzer = CointegrationConfig()

        # 配对选择模块
        self.pair_selector = PairSelectorConfig()

        # 贝叶斯建模模块 (保持字典,先验配置复杂)
        self.bayesian_modeler = {
            'mcmc_chains': 4,

            'bayesian_priors': {
                'uninformed': {
                    'alpha_sigma': 10,
                    'beta_sigma': 5,
                    'sigma_sigma': 5.0,
                    'rho_alpha': 2,
                    'rho_beta': 2
                },
                'informed': {
                    'sigma_multiplier': 2.0,
                    'validity_days': 30,
                    'rho_variance_multiplier': 1.2,
                    'rho_variance_safety': 0.9,
                    'sigma_eta_multiplier': 2.5
                },
                'joint_single_stage': {
                    'sigma_eta_prior': 0.1,
                    'mcmc_warmup': 1000,
                    'mcmc_draws': 1000,
                    'enable': True
                }
            }
        }


        # ========== 第三部分: 风控+常量 (永久不变) ==========
        # 风险管理配置 (保持字典,规则配置动态)
        self.risk_management = {
            'enabled': True,

            # 市场条件检查 (v7.28.1: VIX-Only前瞻指标)
            'market_condition': {
                'enabled': True,
                'vix_symbol': 'VIX',
                'vix_resolution': Resolution.Daily,
                'vix_threshold': 35,                                # 阻止开仓阈值
                'vix_warning_threshold': 30                         # 警告阈值 (>= 30 打印警告)
            },

            # Portfolio层面规则
            'portfolio_rules': {
                'account_blowup': {
                    'enabled': True,
                    'priority': 100,
                    'threshold': 0.15,
                    'cooldown_days': 999999,                    
                    'action': 'portfolio_liquidate_all'
                },
                'portfolio_drawdown': {
                    'enabled': True,
                    'priority': 90,
                    'threshold': 0.10,
                    'cooldown_days': 180,                     
                    'action': 'portfolio_liquidate_all'
                }
            },

            # Pair层面规则
            'pair_rules': {
                'pair_anomaly': {
                    'enabled': True,
                    'priority': 100,
                    'cooldown_days': 999999                  
                },
                'pair_cumulative_loss': {
                    'enabled': True,
                    'priority': 90,
                    'threshold': 0.08,                         
                    'cooldown_days': 360                        
                },
                'pair_drawdown': {
                    'enabled': True,
                    'priority': 80,
                    'threshold': 0.08,                          
                    'cooldown_days': 180                        
                },
                'holding_timeout': {
                    'enabled': True,
                    'priority': 70,
                    'max_halflife_multiplier': 2.0,
                    'cooldown_days': 30                         
                }
            }
        }

        # 常量定义
        self.constants = self._init_constants()


    def _init_constants(self) -> dict:
        """
        初始化常量定义 (独立方法,减少__init__视觉噪音)

        包含:
        - trading_signals: 交易信号枚举
        - position_modes: 持仓模式枚举
        - order_actions: 订单动作枚举
        - close_reasons: 平仓原因+冷却天数
        - industry_names: Morningstar行业映射(55个)
        """
        return {
            'trading_signals': Constants.TRADING_SIGNALS,
            'position_modes': Constants.POSITION_MODES,
            'order_actions': Constants.ORDER_ACTIONS,
            'close_reasons': Constants.CLOSE_REASONS,
            'industry_names': Constants.INDUSTRY_NAMES
        }


    def get_module_config(self, module_name):
        """
        获取模块配置 (向后兼容方法)

        支持两种访问方式:
        - dataclass对象: 转换为字典返回
        - 字典: 直接返回
        """
        config = getattr(self, module_name, {})

        # 如果是dataclass对象,转换为字典 (向后兼容)
        if hasattr(config, '__dataclass_fields__'):
            from dataclasses import asdict
            return asdict(config)

        return config
