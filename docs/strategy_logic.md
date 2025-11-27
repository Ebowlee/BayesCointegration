# Strategy Logic Documentation

# Part 1: Universe Selection (选股逻辑)

1. 触发机制 (Trigger)
    频率: 每月一次 (MonthStart)
    时间: 上午 9:10
    状态控制: 设置 selection_on = True，仅在触发后的第一次 Coarse/Fine 调用中执行筛选，其余时间返回缓存结果。

2. 第一阶段：粗选 (Coarse Selection)
    目标：筛选出流动性好、规模大、上市时间长的股票。

    基础过滤 (Filter)
        HasFundamentalData: 必须包含基本面数据 (排除纯ETF/ETN等非股票资产)
        Price >= $20: 股价不低于 20 美元 (避免低价股/仙股)
        MarketCap >= $1B: 市值不低于 10 亿美元 (保证中大型盘)
        DollarVolume >= $100M: 日成交额不低于 1 亿美元 (保证极佳的流动性) 注: Config中默认为1e8
        IPO Date >= 360 Days: 上市时间超过 360 天 (排除次新股，保证数据稳定性)
    排序与截断 (Sort & Take)
        排序依据: DollarVolume (成交金额) 降序排列
        截取数量: Top 200 (取前200名)

3. 第二阶段：精选 (Fine Selection)
    目标：基于财务指标排除“垃圾股”，并合并ETF。

    财务筛选 (Financial Validator)
        估值筛选 (Valuation) - 逻辑关系: OR (满足任意一条即可)
            PE Ratio <= 100: 市盈率不高于 100 (过滤极端泡沫)
            PS Ratio <= 10: 市销率不高于 10 (保护未盈利但高增长的公司)
        负债筛选 (Debt) - 默认关闭 (Enabled: False)
            DebtToAssets <= 0.6
        杠杆筛选 (Leverage) - 默认关闭 (Enabled: False)
            FinancialLeverage <= 6
    ETF 合并 (Merge ETFs)
        来源: 从 ETFUniverseConfig 读取
        核心 ETF (Sector): 11个 (XLK, XLF, XLV 等)
        细分 ETF (Industry): 7个 (SOXX, XME, XOP 等)
        操作: 将这些 ETF 的 Symbol 直接加入最终列表 (不经过财务筛选)

4. 最终输出 (Final Output)
    结果: List[Symbol]
    构成: 经过财务筛选的 Top 股票 + 预设的 ETF 列表


# Part 2: Analysis Pipeline - Step 1: Data Processing (数据处理)

1. 数据获取 (Data Acquisition)
    目标：获取并清洗历史价格数据，为后续协整分析提供高质量的数据基础。

    来源: algorithm.History
    范围: 过去 252 个交易日 (lookback_days)
    分辨率: Daily (日线)
    对象: Universe Selection 选出的所有 Symbol

2. 数据验证 (Data Validation)
    对每只股票的历史数据进行严格检查，任何一项不满足即丢弃该股票。

    完整性检查 (Completeness)
        长度检查: 必须恰好有 252 条数据 (少一天都不行，严格对齐)
        缺失值检查: 不允许任何 NaN 值 (Zero Tolerance)
        列检查: 必须包含 close 列
    合理性检查 (Plausibility)
        价格检查: 所有收盘价必须 > 0
    波动性检查 (Volatility Control)
        年化波动率: <= 80% (0.8)
            计算: std(daily_returns) * sqrt(252)
            目的: 排除波动极其剧烈的妖股，保证协整关系的稳定性。
    极端风险检查 (Drawdown Control)
        单日最大跌幅: >= -20% (-0.20)
            计算: min(daily_returns)
            目的: 排除有过“闪崩”历史的股票，降低尾部风险。

3. 输出结果 (Output)
    Clean Data: Dict[Symbol, DataFrame] (仅包含通过验证的股票数据)
    Valid Symbols: List[Symbol] (有效股票列表)


# Part 3: Analysis Pipeline - Step 2: Cointegration Analyzer (协整初筛)

1. 行业分组 (Industry Grouping)
    目标：将股票按行业分类，只在同行业内部寻找配对，增强逻辑合理性。

    分组来源: Morningstar Industry Group Code (55个行业)
    ETF 处理:
        支持多行业映射 (例如 XME 同时归入金属矿业和钢铁)
        ETF 会被加入到其对应的所有行业分组中
    数量限制 (Size Constraints)
        最少股票数: 3 (太少无法配对)
        最多股票数: 40 (太多计算量爆炸，取 DollarVolume 前 40 名)

2. 协整检验 (Cointegration Test)
    目标：在每个行业分组内，对所有两两组合进行 Engle-Granger 协整检验。

    检验方法: statsmodels.tsa.stattools.coint (Augmented Engle-Granger)
    输入数据: 清洗后的对数价格序列 (Log Prices)
    阈值标准: p-value < 0.01 (99% 置信度)
    异常处理: 
        长度检查: 必须完全一致
        索引检查: 时间轴必须完全对齐 (Index Equality)
        其他: 捕获 statsmodels 潜在报错

3. 结果输出 (Output)
    Pairs: List[Dict]
        包含: symbol1, symbol2, pvalue, industry_code
        排序: 按 p-value 从小到大排序 (显著性越强越靠前)
    Statistics: Dict
        包含: 测试配对总数、通过配对总数、各行业详细统计


# Part 4: Analysis Pipeline - Step 3: Industry Quota (行业配额)

1. 配额计算 (Quota Calculation) - 动态平衡系统 (Dynamic Equilibrium)
    目标：构建一个自我进化的资金分配系统，在“进攻”与“防守”之间寻找动态平衡。

    预热期 (Warmup): 前 90 天不分配配额 (返回空)，使用默认值。
    进攻机制 (Attack - Exponential Weight):
        基于行业综合得分 (Composite Score = Rolling ROI * Rolling WinRate)
        滚动窗口 (Rolling Window):
            时间窗口: 最近 180 天 (快速适应市场风格切换)
            样本保底: 若窗口内不足 20 笔交易，则向后追溯取满 20 笔 (防止小样本噪音)
        正收益: 权重 = ceil(exp(8 * Score)) (指数级增长，迅速放大优势行业的相对权重)
        负收益/无数据: 权重 = 1 (保底)
    防守机制 (Defense - Concentration Cap):
        熔断: 如果某行业当前资金占用超过阈值 (concentration_threshold)，配额直接降为 0。
        作用: 形成负反馈闭环 (赚钱->加仓->触顶->停新)，防止单一行业风险失控。
    分配逻辑 (Allocation - Normalization):
        公式: TotalQuota * (IndustryWeight / TotalWeight)
        保底: 每个行业至少 min_quota (防止饿死，保留翻身机会)

2. 配额应用 (Quota Application)
    目标：从通过协整检验的配对池中，筛选出最终进入下一轮的配对。

    筛选逻辑:
        1. 随机打乱: 既然都通过了 p<0.01，不再按 pvalue 排序，而是随机抽取 (增加多样性)。
        2. 确定性随机: 使用 hash(date + industry) 作为种子，保证回测可复现。
        3. 单股限制: 限制单只股票最多参与 max_symbol_repeats 个配对 (防止单股风险敞口过大)。
    输出:
        Selected Pairs: List[Dict] (经过配额筛选后的精简列表)


# Part 5: Analysis Pipeline - Step 4: PairData Construction (数据封装)

1. 目标 (Goal)
    将通过配额筛选的配对数据封装为不可变对象，为后续贝叶斯建模提供类型安全、预计算的数据基础。

2. 数据结构 (Data Structure)
    类型: Dataclass (Frozen=True, 不可变)
    内容:
        Symbol1, Symbol2: 股票代码
        Prices1, Prices2: 原始价格序列 (np.ndarray)
        LogPrices1, LogPrices2: 对数价格序列 (预计算, np.log)

3. 核心逻辑 (Core Logic)
    工厂方法 (Factory Method): `from_clean_data`
        自动从 clean_data 字典中提取对应股票的 Close 价格。
        自动执行对数转换 (Log Transformation)，避免后续重复计算。
    数据验证 (Validation):
        长度一致性: 确保两只股票的价格序列长度完全相等。
        对数一致性: 确保对数价格长度与原始价格一致。
        异常处理: 若数据缺失或长度不匹配，在对象创建时即抛出异常 (Fail Fast)。

4. 输出 (Output)
    PairData Dictionary: Dict[Tuple[Symbol, Symbol], PairData]
    供后续 Bayesian Modeler 直接使用。
