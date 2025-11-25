# region imports
from AlgorithmImports import *
from dataclasses import dataclass, field
from typing import Dict, List, Union
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
class ETFUniverseConfig:
    """
    ETF Universe配置 (v8.0.0)

    设计原则:
    - 三级开关: enabled → sector_etfs_enabled → industry_etfs_enabled
    - 订阅优先级: 先订阅11个核心ETF, 再订阅7个特种部队ETF
    - 特种部队逻辑: 当industry_etfs_enabled=True时,特种部队ETF"替换"核心ETF对应行业

    示例:
    - 31130半导体: sector_etfs只有XLK → industry_etfs启用后用SOXX替换
    - 10150金属矿业: sector_etfs用XLB → industry_etfs启用后用XME替换

    覆盖验证:
    - 11个核心: 覆盖47个行业 (包含10150/10160但会被XME替换)
    - 7个特种部队: 覆盖8个行业 (XME覆盖2个)
    - 总计: 55个行业100%覆盖
    """
    # === 三级开关 ===
    enabled: bool = True                                                            # Level 1: 总开关
    sector_etfs_enabled: bool = True                                                # Level 2: 11个核心 
    industry_etfs_enabled: bool = True                                              # Level 3: 7个特种部队

    # === 两层映射 ===
    # Tier 1: 核心11个ETF → 行业列表 (订阅优先级: 第一批)
    sector_etf_mapping: Dict[str, List[int]] = field(default_factory=lambda: {
        'XLB': [10110, 10120, 10130, 10140, 10150, 10160, 10250],                   # 基础材料 (包含10150/10160,会被XME替换)
        'XLY': [10200, 10220, 10240, 10260, 10270, 10290],                          # 消费周期 (10230被XHB替换, 10280被XRT替换)
        'XLF': [10310, 10330, 10340, 10350, 10360],                                 # 金融 (10320被KRE替换)
        'XLRE': [10410, 10420],                                                     # 房地产
        'XLP': [20510, 20520, 20525, 20540, 20550, 20560],                          # 消费防御
        'XLV': [20620, 20630, 20645, 20650, 20660, 20670],                          # 医疗 (20610被IBB替换)
        'XLU': [20710, 20720],                                                      # 公用事业
        'XLC': [30810, 30820, 30830],                                               # 通信
        'XLE': [30920],                                                             # 能源 (30910被XOP替换)
        'XLI': [31010, 31020, 31030, 31040, 31050, 31060, 31070, 31080, 31090],     # 工业
        'XLK': [31110, 31120],                                                      # 科技 (31130被SOXX替换)
    })

    # Tier 2: 特种部队7个ETF → 行业代码 (订阅优先级: 第二批, 替换逻辑)
    industry_etf_mapping: Dict[str, Union[int, List[int]]] = field(default_factory=lambda: {
        'SOXX': 31130,                                                              # 半导体 (替代XLK)
        'IBB':  20610,                                                              # 生物科技 (替代XLV)
        'KRE':  10320,                                                              # 银行 (替代XLF)
        'XOP':  30910,                                                              # 油气勘探 (替代XLE)
        'XHB':  10230,                                                              # 房建 (替代XLY)
        'XME':  [10150, 10160],                                                     # 金属矿业+钢铁 (替代XLB)
        'XRT':  10280,                                                              # 周期零售 (替代XLY)
    })


@dataclass
class UniverseConfig:
    """选股配置 - 筛选参数"""

    # 粗筛设置
    min_price: float = 20
    min_market_cap: float = 1e9
    min_days_since_ipo: int = 360
    min_dollar_volume: float = 1e8                                
    max_coarse_stocks: int = 200                                                    # 按Volume排序取top N

    # 财务筛选器配置
    financial_filters: Dict = field(default_factory=lambda: {
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
    max_annualized_volatility: float = 0.8                          # 年化波动率上限 (80%)
    max_daily_drawdown: float = -0.20                               # 单日最大跌幅 (-20%)


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
    min_quality_threshold: float = 0.50                             # 最低质量分数阈值
    quality_weights: Dict = field(default_factory=lambda: {
        'half_life': 0.25,                     
        'mean_reversion_certainty': 0.40,      
        'zero_crossing': 0.35                  
    })

    # 评分函数阈值设置
    scoring_thresholds: Dict = field(default_factory=lambda: {
        'half_life': {
            'peak_days': 10,                                        # v7.38.0: 从8放宽至10 (慢速配对友好)
            'sigma_left': 5.0,                                      # v7.38.0: 从4.0放宽至5.0 (左侧宽度)
            'sigma_right': 12.0,                                    # v7.38.0: 从9.0放宽至12.0 (右侧宽度)
            'min_days': 4,
            'decay_start': 25,                                      # v7.38.0: 从20延后至25 (衰减起点)
            'decay_rate': 0.15                                      # v7.38.0: 从0.20降低至0.15 (衰减速率)
        },
        'mean_reversion_certainty': {
            'time_delta_days': 1.0,
            'logistic_steepness': 2.5,
            'logistic_midpoint': 2.0,
            'max_snr_kappa': 10.0
        },
        'zero_crossing': {
            'min_crossings': 6,                                     # 左端硬截断（两个月1次）
            'peak_crossings': 12,                                   # 峰值点（每月1次）
            'half_peak_high': 18,                                   # 右侧半峰值起点
            'plateau_end': 24,                                      # 半峰平台结束点
            'max_crossings': 36                                     # 右端硬截断
        }
    })


@dataclass
class PairsConfig:
    """配对配置 - Pairs.py 使用 (v7.61.0 从 PairsTradingConfig 拆分)"""

    # 信号阈值
    entry_threshold_lower: float = 1.2                             # 入场Z-score下限
    entry_threshold_upper: float = 1.8                             # 入场Z-score上限
    exit_threshold: float = 0.3                                    # 出场Z-score阈值
    stop_loss_threshold: float = 2.3                               # 止损Z-score阈值

    # 保证金计算参数 (v7.41.0: 配置层保持监管语义)
    margin_requirement_long: float = 0.5                           # 多头保证金率: 50%
    margin_requirement_short: float = 1.5                          # 空头保证金率: 150%


@dataclass
class IndustryQuotaManagerConfig:
    """
    行业配额管理配置 - IndustryQuotaManager.py 使用 (v7.71.0: 指数权重系统)

    职责:
    - 协整阶段的行业配额管理
    - 基于composite_score的指数权重分配
    - 全局配额约束控制贝叶斯建模输入数量

    权重函数:
        f(x) = ceil(e^x)      当 x ≤ 0  (负CS/零CS,权重=1)
        f(x) = ceil(e^(8x))   当 x > 0  (正CS,指数增长)

        其中 x = composite_score = industry_roi × win_rate

    使用场景:
    - 精确控制全局配额=15,优化MCMC建模性能
    - 高收益行业获得指数级更多配额
    - 负收益行业统一权重=1,避免挤占
    """

    # 全局配额
    total_quota: int = 15                                          # 全局配额总量 (控制贝叶斯建模输入)

    # 权重计算参数
    exp_scale_factor: float = 8.0                                  # 指数缩放系数 (正CS段)
    min_quota_per_industry: int = 1                                # 单行业最低配额保底

    # 预热期配置
    warmup_days: int = 90                                          # 预热期天数


@dataclass
class PairsManagerConfig:
    """
    配对管理配置 - PairsManager.py 使用
    (v7.67.0 移除配额相关配置,专注资金分配职责)

    职责:
    - 资金分配分层(基于composite_score)
    - 保证金管理

    使用场景:
    - 交易阶段确定每个配对的资金分配比例
    - 基于质量分数动态调整投资比例
    """

    # 资金分配分层阈值 (基于composite_score = ROI × WIN_RATE)
    tier_thresholds: Dict[str, float] = field(default_factory=lambda: {
        'tier0': 0.00,                                             # 负收益
        'tier1': 0.03,                                             # 约6%ROI × 50%胜率
        'tier2': 0.06,                                             # 约10%ROI × 60%胜率
        'tier3': 0.10,                                             # 约15%ROI × 67%胜率
        'tier4': 0.15                                              # 高ROI + 高胜率
    })

    # 资金分配分层 (基于composite_score = ROI × WIN_RATE)
    min_investment_ratio: float = 0.05                             # 质量最低(0.0分)配对投资比例: 5%
    tier_max_investment_ratio: Dict[str, float] = field(default_factory=lambda: {
        'tier0': 0.10,                                             # <0: 负得分 → 最低配置
        'tier1': 0.16,                                             # [0, 0.03): 约6%ROI × 50%胜率
        'tier2': 0.18,                                             # [0.03, 0.06): 约12%ROI × 50%胜率
        'tier3': 0.20,                                             # [0.06, 0.10): 约18%ROI × 50%胜率
        'tier4': 0.22                                              # ≥0.10: 高ROI + 高胜率
    })

    # 保证金管理
    margin_usage_ratio: float = 0.98                               # 保证金使用率: 98%


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
class PairDriftRuleConfig:
    """配对漂移规则配置 (v7.87.0)

    检测对冲漂移率，衡量持仓偏离 Dollar Neutral 的程度:
        Drift% = (Net Exposure / Gross Exposure) × 100

    数值解读:
        - 0%: 完美对冲
        - 15%: 警戒区
        - 25%: 触发阈值 (默认)
        - 30%+: 危险区

    Note:
        threshold 使用比例形式 (0.25 = 25%)
        实际检测时: abs(drift) > threshold * 100
    """
    enabled: bool = True
    priority: int = 75              # 在 Drawdown(80) 之后
    threshold: float = 0.25         # 25% (比例形式)
    cooldown_days: int = 30         # 冷却期 30 天


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
    pair_drift: PairDriftRuleConfig = field(default_factory=PairDriftRuleConfig)
    holding_timeout: HoldingTimeoutRuleConfig = field(default_factory=HoldingTimeoutRuleConfig)


@dataclass
class RiskManagementConfig:
    """风险管理配置"""
    enabled: bool = True
    market_condition: MarketConditionConfig = field(default_factory=MarketConditionConfig)
    portfolio_rules: PortfolioRulesConfig = field(default_factory=PortfolioRulesConfig)
    pair_rules: PairRulesConfig = field(default_factory=PairRulesConfig)




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
        'DRIFT': {
            'display': '对冲漂移',
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

        # 2. ETF Universe配置 (v8.0.0)
        self.etf_universe = ETFUniverseConfig()

        # 3. 选股配置
        self.universe_selection = UniverseConfig()

        # 4. 分析模块配置 (合并 analysis_shared + data_processor)
        self.analysis = AnalysisConfig()

        # 5. 协整分析配置
        self.cointegration_analyzer = CointegrationConfig()

        # 6. 贝叶斯建模配置
        self.bayesian_modeler = BayesianModelerConfig()

        # 7. 配对选择配置
        self.pair_selector = PairSelectorConfig()

        # 8. 配对配置 (v7.61.0: 拆分为 pairs + pairs_manager)
        self.pairs = PairsConfig()

        # 9. 配对管理配置 (v7.67.0: 移除配额字段)
        self.pairs_manager = PairsManagerConfig()

        # 9.5. 行业配额管理配置 (v7.67.0: 新增独立配置)
        self.industry_quota = IndustryQuotaManagerConfig()

        # 10. 风险管理配置
        self.risk_management = RiskManagementConfig()

        # 11. 常量配置 (保持dict - 枚举性质)
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
