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
    min_price: float = 20
    min_market_cap: float = 1e9
    min_days_since_ipo: int = 360
    min_dollar_volume: float = 1e8
    max_coarse_stocks: int = 400                                    # 按Volume排序取top N

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
                    'threshold': 80
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
    """数据处理配置"""
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
    """贝叶斯建模配置"""

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

    # 权重计算参数 (v8.0.21: 分段函数)
    exp_scale_factor: float = 6.0                                  # 非负CS段指数缩放系数
    weight_offset: int = 2                                         # 非负CS段权重偏移量 (起点=ceil(e^0)+offset=3)
    min_quota_per_industry: int = 1                                # 单行业最低配额保底

    # 预热期配置 (v8.0.26: 从MainConfig移入，专属于IQM)
    warmup_days: int = 90                                          # IQM预热期 (跳过配额计算)


@dataclass
class PairSelectorConfig:
    """配对质量评估配置"""
    min_quality_threshold: float = 0.50                             # 最低质量分数阈值 (v8.0.24: 应用于scaled_score)
    roi_scaling_enabled: bool = True                                # ROI缩放开关 (v8.0.24: 替代二元过滤)

    quality_weights: Dict = field(default_factory=lambda: {
        'half_life': 0.25,                     
        'mean_reversion_certainty': 0.40,      
        'zero_crossing': 0.35                  
    })

    # 评分函数阈值设置
    scoring_thresholds: Dict = field(default_factory=lambda: {
        'half_life': {
            'peak_days': 10,
            'sigma_left': 5.0,
            'sigma_right': 12.0,
            'min_days': 4,
            'decay_start': 25,
            'decay_rate': 0.15
        },
        'mean_reversion_certainty': {
            'time_delta_days': 1.0,
            'logistic_steepness': 2.5,
            'logistic_midpoint': 2.0,
            'max_snr_kappa': 10.0
        },
        'zero_crossing': {
            'min_crossings': 6,
            'peak_crossings': 12,
            'half_peak_high': 18,
            'plateau_end': 24,
            'max_crossings': 36
        }
    })


@dataclass
class PairsConfig:
    """配对配置 - 信号阈值、保证金参数、交易历史"""

    # 信号阈值
    entry_threshold_lower: float = 1.65                            # 入场Z-score下限
    entry_threshold_upper: float = 1.95                            # 入场Z-score上限
    exit_threshold: float = 0.5                                    # 出场Z-score阈值
    stop_loss_threshold: float = 2.58                              # 止损Z-score阈值

    # 保证金计算参数
    margin_requirement_long: float = 0.5                           # 多头保证金率: 50%
    margin_requirement_short: float = 1.5                          # 空头保证金率: 150%


@dataclass
class PairsManagerConfig:
    """配对管理配置 - 保证金、健康检查、冷却期、滚动窗口"""

    # 保证金管理
    margin_usage_ratio: float = 0.98                               # 保证金使用率: 98%
    concentration_threshold: float = 0.40                          # 单行业资金占用上限 (40%)
    min_investment_ratio: float = 0.05                             # 最低投资比例: 5%

    # 滚动窗口配置 
    rolling_window_days: int = 90                                  # CS计算滚动窗口
    # 备注: Pairs.trade_history 保留期 = rolling_window_days × 2 (180天)
    min_samples_for_window: int = 20                               # 样本量保底: 窗口内<20笔时取最近20笔

    # 健康检查阈值
    drawdown_threshold: float = 0.04                               # 4% 回撤触发
    drift_threshold: float = 0.50                                  # 50% 漂移触发

    # 冷却期配置 (key=reason, value=天数)
    cooldown_days: Dict[str, int] = field(default_factory=lambda: {
        'MEAN_REVERSION': 7,
        'PAIR_BREAK': 30,
        'TIMEOUT': 30,
        'DRAWDOWN': 30,
        'DRIFT': 30,
        'ANOMALY': 999999,
    })

    # 资金分配层级配置 (v7.99.3: 基于平均交易回报)
    # 格式: [(阈值上限, 分配比例), ...] - 小数表示
    allocation_tiers: List[tuple] = field(default_factory=lambda: [
        (0.00, 0.10),    # avg_return ≤ 0%   → 10%
        (0.10, 0.15),    # avg_return ≤ 10%  → 15%
        (0.20, 0.18),    # avg_return ≤ 20%  → 18%
        (0.25, 0.20),    # avg_return ≤ 25%  → 20%
    ])
    allocation_default: float = 0.10   # trade_count=0 时的默认分配
    allocation_max: float = 0.25       # avg_return > 25% 时的最大分配


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
        self.industry_quota = IndustryQuotaManagerConfig()
        self.risk_manager = RiskManagerConfig()

        # 常量映射
        self.constants = {
            'industry_names': INDUSTRY_NAMES,
            'sector_etf_mapping': SECTOR_ETF_MAPPING,
            'industry_etf_mapping': INDUSTRY_ETF_MAPPING,
        }
