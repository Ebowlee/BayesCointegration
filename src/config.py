# region imports
from AlgorithmImports import *
from dataclasses import dataclass, field
from typing import Dict
# endregion


# ============================================================================
# 第一部分: 运行时配置类 (按策略执行流程排序)
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
    schedule_frequency: str = 'MonthStart'                          # 每月初
    schedule_time: tuple = (9, 10)                                  # 9:10 AM

    # 开发配置
    debug_mode: bool = True                                         # True=开发调试(详细日志), False=生产运行(仅关键日志)
    log_level: int = 1                                              # 0=生产模式(核心日志,10-30年), 1=调试模式(全部日志,1年)


@dataclass
class UniverseConfig:
    """选股配置 - 筛选参数"""

    # 粗筛设置
    min_price: float = 20
    min_market_cap: float = 1e9
    min_days_since_ipo: int = 360
    min_dollar_volume: float = 5e7                                  # 最小成交额 
    max_coarse_stocks: int = 500                                    # 按Volume排序取top N

    # 财务筛选器配置
    financial_filters: Dict = field(default_factory=lambda: {
        # 估值OR逻辑 (PE≤100 OR PS≤10) 避免抹杀高成长公司(如特斯拉等PS估值为主的科技公司)
        'valuation': {
            'enabled': True,
            'type': 'or',
            'rules': [
                {
                    'path': 'ValuationRatios.PERatio',
                    'operator': 'le',
                    'threshold': 100
                },
                {
                    'path': 'ValuationRatios.PSRatio',
                    'operator': 'le',
                    'threshold': 10
                }
            ],
            'fail_key': 'valuation_failed'
        },
        'debt_ratio': {
            'enabled': False,
            'path': 'OperationRatios.DebtToAssets.Value',
            'operator': 'le',
            'threshold': 0.6,
            'fail_key': 'debt_failed'
        },
        'leverage': {
            'enabled': False,
            'path': 'OperationRatios.FinancialLeverage.Value',
            'operator': 'le',
            'threshold': 6,
            'fail_key': 'leverage_failed'
        }
    })


@dataclass
class AnalysisConfig:
    """分析模块配置 - 合并 analysis_shared 和 data_processor"""
    lookback_days: int = 252                                        # 历史数据回看天数
    data_completeness_ratio: float = 1.0                            # 数据完整性要求


@dataclass
class CointegrationConfig:
    """协整分析配置"""

    # 统计检验
    pvalue_threshold: float = 0.01                                  # Engle-Granger p值阈值

    # 行业分组
    min_stocks_per_industry: int = 3                                # 行业最少股票数
    max_stocks_per_industry: int = 40                               # 行业最多股票数
    max_symbol_repeats: int = 2                                     # 单股最多允许配对数


@dataclass
class PriorConfig:
    """Uninformed先验配置 (默认值)"""
    alpha_sigma: float = 10.0
    beta_sigma: float = 5.0
    sigma_sigma: float = 5.0
    rho_alpha: float = 2.0
    rho_beta: float = 2.0


@dataclass
class InformedPriorConfig:
    """Informed先验特殊配置"""
    sigma_multiplier: float = 2.0
    validity_days: int = 30
    rho_variance_multiplier: float = 1.2
    rho_variance_safety: float = 0.9
    sigma_eta_multiplier: float = 2.5


@dataclass
class JointStagePriorConfig:
    """Joint Single Stage先验配置"""
    sigma_eta_prior: float = 0.1
    mcmc_chains: int = 4
    mcmc_warmup: int = 1000
    mcmc_draws: int = 1000
    enable: bool = True


@dataclass
class BayesianModelerConfig:
    """贝叶斯建模配置"""
    uninformed: PriorConfig = field(default_factory=PriorConfig)
    informed: InformedPriorConfig = field(default_factory=InformedPriorConfig)
    joint_single_stage: JointStagePriorConfig = field(default_factory=JointStagePriorConfig)


@dataclass
class PairSelectorConfig:
    """配对质量评估配置"""
    # v7.31.0: max_symbol_repeats已迁移到CointegrationConfig
    min_quality_threshold: float = 0.50                             # 最低质量分数阈值
    quality_weights: Dict = field(default_factory=lambda: {
        'half_life': 0.40,                     
        'mean_reversion_certainty': 0.30,      
        'zero_crossing': 0.30                  
    })

    # 评分函数阈值设置
    scoring_thresholds: Dict = field(default_factory=lambda: {
        'half_life': {
            'peak_days': 10,           # v7.38.0: 从8放宽至10 (慢速配对友好)
            'sigma_left': 5.0,         # v7.38.0: 从4.0放宽至5.0 (左侧宽度)
            'sigma_right': 12.0,       # v7.38.0: 从9.0放宽至12.0 (右侧宽度)
            'min_days': 4,
            'decay_start': 25,         # v7.38.0: 从20延后至25 (衰减起点)
            'decay_rate': 0.15         # v7.38.0: 从0.20降低至0.15 (衰减速率)
        },
        'mean_reversion_certainty': {
            'time_delta_days': 1.0,
            'logistic_steepness': 2.5,
            'logistic_midpoint': 2.0,
            'max_snr_kappa': 10.0
        },
        'zero_crossing': {
            'min_crossings': 6,      # 左端硬截断（两个月1次）
            'peak_crossings': 12,    # 峰值点（每月1次）
            'half_peak_high': 18,    # 右侧半峰值起点
            'plateau_end': 24,       # 半峰平台结束点
            'max_crossings': 36      # 右端硬截断
        }
    })


@dataclass
class PairsTradingConfig:
    """配对交易配置 - 信号/仓位参数"""

    # 信号阈值
    entry_threshold_lower: float = 1.2                             # 入场Z-score下限
    entry_threshold_upper: float = 1.8                             # 入场Z-score上限
    exit_threshold: float = 0.3                                    # 出场Z-score阈值
    stop_loss_threshold: float = 2.3                               # 止损Z-score阈值

    # 仓位管理参数
    min_investment_ratio: float = 0.05                             # 质量最低(0.0分)配对投资比例: 5%

    # v7.35.0: 基于行业tier的最大投资比例映射 (更保守的配置)
    tier_max_investment_ratio: Dict[str, float] = field(default_factory=lambda: {
        'tier0': 0.10,  # <0%: 负收益 → 最低配置
        'tier1': 0.16,  # [0%, 5%)
        'tier2': 0.18,  # [5%, 10%)
        'tier3': 0.20,  # [10%, 15%)
        'tier4': 0.22   # ≥15%: 高回报 → 高配置
    })

    # 保证金管理
    margin_requirement_long: float = 0.5                           # 多头保证金率: 50%
    margin_requirement_short: float = 1.5                          # 空头保证金率: 150%
    margin_usage_ratio: float = 0.98                               # 保证金使用率: 98%
    max_leverage_cap: float = 2.0                                  # 放大模式最大杠杆倍数: 2.0倍


@dataclass
class MarketConditionConfig:
    """市场条件检查配置"""
    enabled: bool = True
    vix_symbol: str = 'VIX'
    vix_resolution: Resolution = Resolution.Daily
    vix_threshold: int = 35


@dataclass
class AccountBlowupRuleConfig:
    """账户爆仓规则配置"""
    enabled: bool = True
    priority: int = 100
    threshold: float = 0.20
    cooldown_days: int = 999999
    action: str = 'portfolio_liquidate_all'


@dataclass
class PortfolioDrawdownRuleConfig:
    """组合回撤规则配置"""
    enabled: bool = True
    priority: int = 90
    threshold: float = 0.15
    cooldown_days: int = 360
    action: str = 'portfolio_liquidate_all'


@dataclass
class PairAnomalyRuleConfig:
    """配对异常规则配置"""
    enabled: bool = True
    priority: int = 100
    cooldown_days: int = 999999


@dataclass
class PairCumulativeLossRuleConfig:
    """配对累积亏损规则配置"""
    enabled: bool = True
    priority: int = 90
    threshold: float = 0.08
    cooldown_days: int = 360


@dataclass
class PairDrawdownRuleConfig:
    """配对回撤规则配置"""
    enabled: bool = True
    priority: int = 80
    threshold: float = 0.04
    cooldown_days: int = 180


@dataclass
class HoldingTimeoutRuleConfig:
    """持仓超时规则配置 (v7.38.1: 移除max_halflife_multiplier)"""
    enabled: bool = True
    priority: int = 70
    cooldown_days: int = 90


@dataclass
class PortfolioRulesConfig:
    """组合层面规则配置"""
    account_blowup: AccountBlowupRuleConfig = field(default_factory=AccountBlowupRuleConfig)
    portfolio_drawdown: PortfolioDrawdownRuleConfig = field(default_factory=PortfolioDrawdownRuleConfig)


@dataclass
class PairRulesConfig:
    """配对层面规则配置"""
    pair_anomaly: PairAnomalyRuleConfig = field(default_factory=PairAnomalyRuleConfig)
    pair_cumulative_loss: PairCumulativeLossRuleConfig = field(default_factory=PairCumulativeLossRuleConfig)
    pair_drawdown: PairDrawdownRuleConfig = field(default_factory=PairDrawdownRuleConfig)
    holding_timeout: HoldingTimeoutRuleConfig = field(default_factory=HoldingTimeoutRuleConfig)


@dataclass
class RiskManagementConfig:
    """风险管理配置"""
    enabled: bool = True
    market_condition: MarketConditionConfig = field(default_factory=MarketConditionConfig)
    portfolio_rules: PortfolioRulesConfig = field(default_factory=PortfolioRulesConfig)
    pair_rules: PairRulesConfig = field(default_factory=PairRulesConfig)


@dataclass
class IndustryQuotaConfig:
    """行业配额配置"""
    warmup_days: int = 90                                                   # 自适应行业偏好预热时间
    default_quota: int = 1                                                  # 每个行业初始的协整对配额数量

    # 回报率与配额数量的关系 (v7.35.0: 更保守的阈值和配额)
    tier_thresholds: Dict[str, float] = field(default_factory=lambda: {
        'tier0': 0.00,                                                      # 负收益
        'tier1': 0.05,                                                      # [0%, 5%)
        'tier2': 0.10,                                                      # [5%, 10%)
        'tier3': 0.15                                                       # [10%, 15%)
    })
    tier_quotas: Dict[str, int] = field(default_factory=lambda: {
        'tier0': 1,                                                         # <0%: 负收益 → 最低配额
        'tier1': 2,                                                         # [0%, 5%)
        'tier2': 3,                                                         # [5%, 10%)
        'tier3': 4,                                                         # [10%, 15%)
        'tier4': 5                                                          # ≥15%: 高回报 → 高配额
    })


# ============================================================================
# 第二部分: 常量定义类 (永久不变的枚举映射)
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
            'cooldown_days': 30,                                 # Pairs层冷却期
            'category': 'NORMAL_SIGNAL'
        },
        'PAIR_BREAK': {
            'display': '协整破裂',
            'cooldown_days': 180,                                # Pairs层冷却期
            'category': 'NORMAL_SIGNAL'
        },

        # === 组2: Pair级风控触发 ===
        'TIMEOUT': {
            'display': '持有超时',
            'category': 'PAIR_RISK'                              # Pair风控触发
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
# 第三部分: 统一配置类 (对外接口 - 向后兼容)
# ============================================================================

class StrategyConfig:
    """策略配置中心 - 统一访问入口"""

    def __init__(self):

        # ========== 按新执行流程初始化所有dataclass配置 ==========
        # 1. 主程序配置
        self.main = MainConfig()

        # 2. 选股配置
        self.universe_selection = UniverseConfig()

        # 3. 分析模块配置 (合并 analysis_shared + data_processor)
        self.analysis = AnalysisConfig()

        # 4. 协整分析配置
        self.cointegration_analyzer = CointegrationConfig()

        # 5. 贝叶斯建模配置
        self.bayesian_modeler = BayesianModelerConfig()

        # 6. 配对选择配置
        self.pair_selector = PairSelectorConfig()

        # 7. 配对交易配置
        self.pairs_trading = PairsTradingConfig()

        # 8. 风险管理配置
        self.risk_management = RiskManagementConfig()

        # 9. 行业配额配置
        self.industry_quota = IndustryQuotaConfig()

        # 10. 常量配置 (保持dict - 枚举性质)
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
