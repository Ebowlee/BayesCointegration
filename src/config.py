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
    start_date: tuple = (2022, 9, 20)
    end_date: tuple = (2024, 9, 20)
    cash: int = 100000
    resolution: Resolution = Resolution.Daily
    brokerage_name: BrokerageName = BrokerageName.InteractiveBrokersBrokerage
    account_type: AccountType = AccountType.Margin

    # 选股调度配置
    schedule_frequency: str = 'MonthStart'                          # 每月初
    schedule_time: tuple = (9, 10)                                  # 9:10 AM

    # 开发配置
    debug_mode: bool = True
    log_level: int = 1


@dataclass
class UniverseConfig:
    """选股配置 - 粗筛参数 + ETF开关 + 财务筛选"""

    # 粗筛设置
    min_price: float = 30
    min_market_cap: float = 1e9
    min_days_since_ipo: int = 360
    min_dollar_volume: float = 1e8
    max_coarse_stocks: int = 500                                    # 按Volume排序取top N

    # ETF订阅开关 (v8.0.5: 从ETFUniverseConfig合并)
    etf_enabled: bool = True                                        # Level 1: 总开关
    sector_etfs_enabled: bool = True                                # Level 2: 11个核心
    industry_etfs_enabled: bool = True                              # Level 3: 7个特种部队

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
        }
    })


@dataclass
class DataProcessorConfig:
    """数据处理配置 (v8.24.0: 扩展数据窗口支持Z-score回算)"""

    # 时间窗口配置 (v8.24.0: 协整窗口 = total - bayesian = 180天)
    total_lookback_days: int = 240                                  # 总数据下载量 (交易日) v8.24.0: 180→240
    bayesian_lookback_days: int = 60                                # 贝叶斯建模窗口 (保持不变)

    # === 数据质量验证 ===
    data_completeness_ratio: float = 1.0                            # 数据完整性要求
    max_annualized_volatility: float = 0.5                          # 年化波动率上限 (70%)
    max_daily_drawdown: float = -0.10                               # 单日最大跌幅 (-10%)


@dataclass
class CointegrationConfig:
    """协整分析配置"""

    # 统计检验
    pvalue_threshold: float = 0.01                                  # Engle-Granger p值阈值

    # 行业分组
    min_stocks_per_industry: int = 10                               # 行业最少股票数
    max_stocks_per_industry: int = 50                               # 行业最多股票数
    max_symbol_repeats: int = 2                                     # 单股最多允许配对数

    # v8.16.0: MCMC算力保护
    max_cointegrated_pairs: int = 30                                # 协整配对数量上限 (随机抽样)


@dataclass
class BayesianModelerConfig:
    """贝叶斯建模配置 (v8.5.0: Empirical Bayes重构)"""

    # === OLS先验 (v8.5.0 Empirical Bayes) ===
    ols_prior_sigma_multiplier: float = 2.0         # OLS标准误放宽倍数 (α, β)
    sigma_eta_scale_factor: float = 0.1             # 状态噪声缩放因子 (sigma_eta = sigma_ols × 0.1)

    # === AR(1)参数先验 ===
    rho_alpha: float = 2.0                          # Beta(2,2) 弱先验
    rho_beta: float = 2.0

    # === 历史后验先验 (跨月复用) ===
    informed_sigma_multiplier: float = 2.0
    informed_validity_days: int = 30
    informed_rho_variance_multiplier: float = 1.2
    informed_rho_variance_safety: float = 0.9
    informed_sigma_eta_multiplier: float = 2.5

    # === MCMC采样配置 ===
    mcmc_chains: int = 4
    mcmc_warmup: int = 1000
    mcmc_draws: int = 1000


@dataclass
class PairSelectorConfig:
    """
    配对筛选配置 (v8.22.0: 三维度可选开关)

    三维度阈值筛选:
    1. CV BETA: β估计的相对不确定性
    2. 半衰期: 残差回归速度
    3. 零轴穿越: 交易活跃度

    v8.9.1: 删除Hurst维度 (60天spread数据不足以稳健计算R/S分析)
    v8.22.0: 新增三维度可选开关，方便回测实验
    """
    # v8.22.0: 维度开关 (用于回测实验)
    cv_beta_enabled: bool = True                                    # CV BETA 筛选开关
    half_life_enabled: bool = True                                  # 半衰期筛选开关
    zero_crossing_enabled: bool = True                              # 零轴穿越筛选开关

    # 维度1: CV BETA 稳定性
    cv_beta_threshold: float = 0.2                                  # CV > 0.2 → 剔除
    min_abs_beta: float = 0.1                                       # |β| < 0.1 → 剔除 (避免CV爆炸)

    # 维度2: 半衰期
    half_life_min: float = 3                                        # < 3天 → 剔除 (太短 = 噪音)
    half_life_max: float = 30                                       # > 30天 → 剔除 (太长 = 回归太慢)

    # 维度3: 零轴穿越
    zero_crossing_min: int = 2                                      # < 2次 → 剔除 (太不活跃)
    zero_crossing_max: int = 60                                     # > 60次 → 剔除 (太嘈杂)


@dataclass
class PairsConfig:
    """配对配置 - 信号阈值、保证金参数、RSI动量 (v8.24.0: 稀有事件捕捉)"""

    # v8.24.0: 稀有事件捕捉 (Rare Event Capture)
    adaptive_entry_enabled: bool = True                            # 总开关
    entry_percentile_lower: float = 99.0                           # 入场下限百分位 (P99 ~2.33σ)
    entry_percentile_upper: float = 99.9                           # 入场上限百分位 (P99.9 ~3.1σ)
    zscore_back_projection_days: int = 180                         # Z-score向后回算窗口 (用于分位数统计)
    exit_threshold: float = 0.5                                    # 出场Z-score阈值

    # 保证金计算参数
    margin_requirement_long: float = 0.5                           # 多头保证金率: 50%
    margin_requirement_short: float = 1.5                          # 空头保证金率: 150%


    # RSI on Z-score 参数 (v8.7.0, v8.17.0重命名)
    rsi_warmup_days: int = 10                                      # 预热天数 (从clean_data加载)
    rsi_period: int = 8                                            # RSI周期
    rsi_lookback_for_extreme: int = 3                              # 查找"近期曾极端"的窗口                                       # RSI周期
    rsi_short_spread_threshold: float = 80.0                       # SHORT_SPREAD入场: RSI曾>此值后回落
    rsi_long_spread_threshold: float = 20.0                        # LONG_SPREAD入场: RSI曾<此值后反弹
    
    # 动量止盈参数 (v8.19.0: Momentum Profit Taking)
    momentum_profit_enabled: bool = True                           # 总开关
    momentum_rsi_threshold: float = 40.0                           # RSI绝对动量阈值 (空头<40, 多头>60)


@dataclass
class PairsManagerConfig:
    """配对管理配置 - 保证金分配、健康检查、冷却期 (v8.23.0: 个性化止损)"""

    # 保证金管理
    margin_usage_ratio: float = 0.98                               # 保证金使用率: 98%

    # 统一资金分配 (v8.10.0: 简化为固定15%)
    fixed_allocation_pct: float = 0.15                             # 统一分配比例 + 地板

    # 开仓排序 (v8.11.0: 按预期收益额排序)
    sort_by_expected_profit: bool = True                           # 开关: 启用预期收益排序

    # 健康检查阈值 (v8.23.0: 个性化止损步长)
    trailing_step_multiplier: float = 1.0                          # 止损步长倍数 (乘以配对Sigma)
    trailing_step_floor: float = 0.5                               # 止损步长地板 (最小0.5σ)
    drawdown_threshold: float = 0.10                               # 10% 统一回撤阈值

    # 动态冷却系数 (v8.13.0: 基于半衰期, cooldown = half_life × multiplier)
    cooldown_multipliers: Dict[str, float] = field(default_factory=lambda: {
        'MEAN_REVERSION': 1.0,                                     # 正常平仓: 1个半衰期
        'TIMEOUT': 2.0,                                            # 超时平仓: 2个半衰期
        'DRAWDOWN': 4.0,                                           # 回撤止损: 4个半衰期
        'TRAILING_STOP': 2.0,                                      # 阶梯止损: 2个半衰期 (v8.18.0)
        'ANOMALY': 99999.0,                                        # 数据异常: 永久冷却
    })

    # 保底半衰期 (当half_life为None时使用)
    default_half_life: float = 10.0


@dataclass
class RiskManagerConfig:
    """风控管理器配置 - VIX市场条件 + Portfolio回撤"""

    # VIX 市场条件
    vix_enabled: bool = True
    vix_symbol: str = 'VIX'
    vix_threshold: int = 35

    # Portfolio 回撤
    drawdown_enabled: bool = True
    drawdown_threshold: float = 0.20                                # 20% 回撤触发
    drawdown_cooldown_days: int = 360                               # 360天冷却期




# ============================================================================
# 第二部分: 统一配置类
# ============================================================================

# Morningstar 行业映射 (55个)
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

# Tier 1: 核心11个ETF → 行业列表 (订阅优先级: 第一批)
SECTOR_ETF_MAPPING = {
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
}

# Tier 2: 特种部队7个ETF → 行业代码 (订阅优先级: 第二批, 替换逻辑)
INDUSTRY_ETF_MAPPING = {
    'SOXX': 31130,                                                              # 半导体 (替代XLK)
    'IBB':  20610,                                                              # 生物科技 (替代XLV)
    'KRE':  10320,                                                              # 银行 (替代XLF)
    'XOP':  30910,                                                              # 油气勘探 (替代XLE)
    'XHB':  10230,                                                              # 房建 (替代XLY)
    'XME':  [10150, 10160],                                                     # 金属矿业+钢铁 (替代XLB)
    'XRT':  10280,                                                              # 周期零售 (替代XLY)
}


class StrategyConfig:
    """策略配置中心 - 统一访问入口"""

    def __init__(self):
        # 运行时配置
        self.main = MainConfig()
        self.universe_selection = UniverseConfig()
        self.data_processor = DataProcessorConfig()
        self.cointegration_analyzer = CointegrationConfig()
        self.bayesian_modeler = BayesianModelerConfig()
        self.pair_selector = PairSelectorConfig()
        self.pairs = PairsConfig()
        self.pairs_manager = PairsManagerConfig()
        self.risk_manager = RiskManagerConfig()

        # 常量映射
        self.constants = {
            'industry_names': INDUSTRY_NAMES,
            'sector_etf_mapping': SECTOR_ETF_MAPPING,
            'industry_etf_mapping': INDUSTRY_ETF_MAPPING,
        }
