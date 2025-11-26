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
class DataProcessorConfig:
    """数据处理配置 (v7.96.0: 重命名自 AnalysisConfig)"""
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
class BayesianModelerConfig:
    """贝叶斯建模配置 (v7.96.0: 扁平化结构)"""

    # === Uninformed先验 (默认值) ===
    alpha_sigma: float = 10.0
    beta_sigma: float = 5.0
    sigma_sigma: float = 5.0
    rho_alpha: float = 2.0
    rho_beta: float = 2.0

    # === Informed先验 (历史后验) ===
    informed_sigma_multiplier: float = 2.0
    informed_validity_days: int = 30
    informed_rho_variance_multiplier: float = 1.2
    informed_rho_variance_safety: float = 0.9
    informed_sigma_eta_multiplier: float = 2.5

    # === Joint Single Stage (MCMC) ===
    sigma_eta_prior: float = 0.1
    mcmc_chains: int = 4
    mcmc_warmup: int = 1000
    mcmc_draws: int = 1000
    joint_enable: bool = True


@dataclass
class IndustryQuotaManagerConfig:
    """行业配额管理配置"""

    # 全局配额
    total_quota: int = 25                                          # 全局配额总量 (控制贝叶斯建模输入)

    # 权重计算参数
    exp_scale_factor: float = 8.0                                  # 指数缩放系数 (正CS段)
    min_quota_per_industry: int = 1                                # 单行业最低配额保底

    # 预热期配置
    warmup_days: int = 90                                          # 预热期天数


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
class PairsManagerConfig:
    """配对管理配置 (v7.97.0: 合并 PairHealthCheckConfig, 删除 tier_thresholds)"""

    # === 保证金管理 ===
    margin_usage_ratio: float = 0.98                               # 保证金使用率: 98%

    # === 行业集中度控制 ===
    concentration_threshold: float = 0.40                          # 单行业资金占用上限 (40%)

    # === 资金分配 (v7.97.0: 简化为 min/max, 移除未实现的 tier 逻辑) ===
    min_investment_ratio: float = 0.05                             # 最低投资比例: 5%
    max_investment_ratio: float = 0.10                             # 最高投资比例: 10%

    # === 健康检查阈值 (从 PairHealthCheckConfig 合并) ===
    drawdown_threshold: float = 0.04                               # 4% 回撤触发
    drift_threshold: float = 0.25                                  # 25% 漂移触发
    cumulative_roi_threshold: float = 0.10                         # 8% 累积亏损触发

    # === 统一冷却期配置 (v7.97.0: Dict结构) ===
    cooldown_days: Dict[str, int] = field(default_factory=lambda: {
        # 正常信号
        'MEAN_REVERSION': 7,
        'PAIR_BREAK': 30,
        # Pair风控
        'TIMEOUT': 30,
        'DRAWDOWN': 30,
        'DRIFT': 30,
        'ANOMALY': 999999,
        'CUMULATIVE_ROI': 360,
    })


@dataclass
class RiskManagerConfig:
    """
    风控管理器配置 (v7.98.1: 扁平化)

    整合 MarketCondition + PortfolioDrawdown，删除冗余字段
    """
    # VIX 市场条件
    vix_enabled: bool = True
    vix_symbol: str = 'VIX'
    vix_threshold: int = 35

    # Portfolio 回撤
    drawdown_enabled: bool = True
    drawdown_threshold: float = 0.15                                # 15% 回撤触发
    drawdown_cooldown_days: int = 360                               # 360天冷却期




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

    # === 4. 平仓原因（显示文本+分类，冷却期统一在 PairsManagerConfig.cooldown_days）===
    CLOSE_REASONS = {
        # 正常信号
        'MEAN_REVERSION': {'display': '均值回归', 'category': 'NORMAL_SIGNAL'},
        'PAIR_BREAK': {'display': '协整破裂', 'category': 'NORMAL_SIGNAL'},
        # Pair风控
        'TIMEOUT': {'display': '持有超时', 'category': 'PAIR_RISK'},
        'CUMULATIVE_LOSS': {'display': '累计亏损', 'category': 'PAIR_RISK'},
        'CUMULATIVE_ROI': {'display': '累积ROI', 'category': 'PAIR_RISK'},
        'DRAWDOWN': {'display': '回撤触发', 'category': 'PAIR_RISK'},
        'DRIFT': {'display': '对冲漂移', 'category': 'PAIR_RISK'},
        'ANOMALY': {'display': '单腿异常', 'category': 'PAIR_RISK'},
        # Portfolio风控
        'PORTFOLIO_DRAWDOWN': {'display': '组合回撤', 'category': 'PORTFOLIO_RISK'},
        'ACCOUNT_BLOWUP': {'display': '组合爆仓', 'category': 'PORTFOLIO_RISK'},
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

        # 4. 数据处理配置 (v7.96.0: 重命名自 AnalysisConfig)
        self.data_processor = DataProcessorConfig()

        # 5. 协整分析配置
        self.cointegration_analyzer = CointegrationConfig()

        # 6. 贝叶斯建模配置
        self.bayesian_modeler = BayesianModelerConfig()

        # 7. 配对选择配置
        self.pair_selector = PairSelectorConfig()

        # 8. 配对配置 (v7.61.0: 拆分为 pairs + pairs_manager)
        self.pairs = PairsConfig()

        # 9. 配对管理配置 (v7.97.0: 合并 PairHealthCheckConfig)
        self.pairs_manager = PairsManagerConfig()

        # 10. 行业配额管理配置
        self.industry_quota = IndustryQuotaManagerConfig()

        # 11. 风控管理配置 (v7.98.1: 扁平化)
        self.risk_manager = RiskManagerConfig()

        # 12. 常量配置 (保持dict - 枚举性质)
        self.constants = self._init_constants()


    def _init_constants(self) -> dict:
        """
        初始化常量定义 (独立方法,减少__init__视觉噪音)

        包含:
        - trading_signals: 交易信号枚举
        - position_modes: 持仓模式枚举
        - order_actions: 订单动作枚举
        - close_reasons: 平仓原因元数据 (冷却期已移至 PairsManagerConfig.cooldown_days)
        - industry_names: Morningstar行业映射(55个)
        """
        return {
            'trading_signals': Constants.TRADING_SIGNALS,
            'position_modes': Constants.POSITION_MODES,
            'order_actions': Constants.ORDER_ACTIONS,
            'close_reasons': Constants.CLOSE_REASONS,
            'industry_names': Constants.INDUSTRY_NAMES
        }
