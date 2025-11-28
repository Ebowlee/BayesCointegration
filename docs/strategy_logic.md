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


# Part 6: Analysis Pipeline - Step 5: Bayesian Modeler (贝叶斯建模)

1. 目标 (Goal)
    对通过协整初筛的配对进行精细化的参数估计，量化均值回归特性。
    核心优势：利用贝叶斯方法融合历史先验信息，提高参数估计的稳定性。

2. 先验选择 (Prior Selection)
    策略：二级先验体系
    Level 1: 历史后验先验 (Informed Prior)
        条件: 存在有效的历史后验记录 (within validity_days)。
        $\alpha, \beta$: Normal 分布，均值/方差源自历史后验。
        $\rho$: Beta 分布，使用矩匹配 (Moment Matching) 拟合历史均值和方差。
        $\sigma_\eta$: HalfNormal 分布，基于历史波动率放大 (Relaxation)。
    Level 2: 无信息先验 (Uninformed Prior)
        条件: 无历史记录或记录过期。
        $\alpha, \beta$: Normal 分布 (宽泛方差)。
        $\rho$: Beta(2, 2) (偏好 0.5 但允许全域)。
        $\sigma_\eta$: HalfNormal (0.1)。

3. 联合建模 (Joint Modeling)
    模型: 单一联合贝叶斯模型 (Joint Bayesian Model)
    核心变换: 为解决 PyMC 观测值依赖问题，将 OU 过程转换为 AR(1) 形式：
    $$y_t = \alpha(1-\rho) + \beta(x_t - \rho x_{t-1}) + \rho y_{t-1} + \eta_t$$
    参数定义:
        $\beta$: 对冲比例 (Hedge Ratio)
        $\alpha$: 截距项 (Intercept)
        $\rho$: 均值回归系数 (Mean Reversion Coefficient), $\rho \in (0,1)$ (由 Beta 分布天然保证平稳性)
        $\sigma_\eta$: 残差波动率 (Residual Volatility)
    MCMC 设置 (PyMC):
        Chains: 4
        Draws: 1000 (采样次数)
        Tune: 1000 (预热次数)

4. 输出 (Output)
    Modeling Results: List[Dict]
    包含:
        参数后验均值: alpha_mean, beta_mean, rho_mean, sigma_mean
        参数后验标准差: alpha_std, beta_std, rho_std, sigma_std
        衍生指标: Half Life (半衰期) = $-\ln(2) / \ln(\rho)$
        元数据: modeling_type (informed/uninformed)


# Part 7: Analysis Pipeline - Step 6: Pair Selector (配对筛选)

1. 目标 (Goal)
    基于贝叶斯后验参数，从多维度评估配对质量，筛选出最具有均值回归潜力的配对。

2. 评分维度 (Scoring Dimensions)
    采用三维评分系统，加权计算总分 (Quality Score)。

    维度 1: Half-life (半衰期) - 权重 25%
        定义: 均值回归一半所需的时间，衡量回归速度。
        公式: $HalfLife = -\ln(2) / \ln(\rho)$
        评分函数: 非对称高斯分布 (Asymmetric Gaussian)
            峰值: 8天 (最理想)
            核心区间: 5-10天
            惩罚: <4天 (过度交易) 或 >12天 (回归太慢)

    维度 2: Mean-reversion Certainty (MR确定性) - 权重 40%
        定义: 衡量均值回归强度的统计显著性 (信噪比)。
        指标: SNR_κ = Mean(κ) / Std(κ)，其中 $\kappa = -\ln(\rho) / \Delta t$
        评分函数: Logistic 函数 (S曲线)
            特点: SNR越高分数越高，对高确定性给予奖励。

    维度 3: Zero-crossing (零轴穿越) - 权重 35%
        定义: Spread 穿越均值的次数，衡量波动的“往复性”。
        评分函数: 分段线性函数 (Piecewise Linear)
            峰值: 12次/年 (每月1次) -> 1.0分
            区间: 6-36次 (合理区间)
            惩罚: <6次 (信号稀缺) 或 >36次 (噪声过大)

3. 筛选逻辑 (Selection Logic)
    Step 1: 门槛过滤 - Quality Score > 0.50 (宁缺毋滥)
    Step 1.5: 历史ROI过滤 (v8.0.9) - 预热期后生效
        - 条件: Cumulative ROI < -10% (历史累积亏损超过10%)
        - 数据来源: `pairs_manager.get_pair_by_id(pair_id).get_pair_cumulative_roi()`
        - 设计理念: 即使协整关系仍然成立，长期亏损的配对也不应再被选中
        - 预热期: 前90天不过滤，给所有配对公平机会
    Step 2: 排序 - 按 Quality Score 降序排列
    输出: 最终入选的配对列表 (Selected Pairs)。


# Part 8: Analysis Pipeline - Step 7 & 8: Pairs Creation & Management (配对创建与管理)

1. 目标 (Goal)
    将筛选出的数学模型结果转换为可交易的实体对象 (Pairs Object)，并纳入生命周期管理。

2. 对象创建 (Pairs Creation) - Step 7
    工厂方法: `Pairs.from_model_result`
    初始化内容:
        - 基础信息: Symbol1, Symbol2, Industry Code
        - 贝叶斯参数: $\alpha, \beta, \mu_{res}, \sigma_{res}$
        - 质量指标: Quality Score, Half Life
        - 交易阈值: 从 Config 读取 (Entry: 1.2-1.8$\sigma$, Exit: 0.3$\sigma$, Stop: 2.3$\sigma$)
    设计原则:
        - 充血模型 (Rich Model): Pairs 对象不仅存储数据，还封装了 Z-score 计算、信号生成、意图生成等核心逻辑。

3. 分类管理 (Lifecycle Management) - Step 8
    管理器: `PairsManager`
    分类逻辑:
        - Current Selected: 本轮被选中的配对 (有资格开新仓)。
        - Past Selected: 历史曾被选中但本轮落选 (只能平仓，不能开新仓)。
    更新机制:
        - 增量更新: 仅更新参数，保持对象引用不变。
        - 参数冻结: 如果配对当前有持仓，则**不更新**其统计参数 (Alpha/Beta等)，防止参数漂移导致信号混乱 ("让信号说话")。

4. 核心职责 (Core Responsibilities)
    - Pairs: 负责单体逻辑 (信号、状态、PnL计算)。
    - PairsManager: 负责群体逻辑 (索引管理、行业聚合、健康检查、资金分配)。


# Part 9: Execution Pipeline - Step 1 & 2: Portfolio Risk Management (组合级风控)

1. 概述 (Overview)
    `OnData` 事件触发后的首要步骤，作为策略的“总闸”和“熔断器”。优先级最高，一旦触发，后续所有逻辑（开仓、平仓、信号）全部跳过。

2. 步骤 1: 冷却期检查 (Cooldown Check) - The "Hard Stop"
    目标: 强制策略在经历重大回撤后休息，避免在市场动荡期反复亏损。
    逻辑:
        - 检查 `risk_manager.is_in_portfolio_cooldown()`。
        - 如果 `CurrentTime < CooldownUntil`:
            - 直接 `return`。
            - 策略完全停止运作 (不看行情，不发信号，不交易)。

3. 步骤 2: 回撤检查 (Drawdown Check) - The "Eject Button"
    目标: 账户级的最后一道防线，保住本金。
    逻辑:
        - 更新高水位 (HWM): `HWM = max(HWM, CurrentTotalPortfolioValue)`
        - 计算回撤: `Drawdown = (HWM - CurrentValue) / HWM`
        - 触发判断: 如果 `Drawdown >= Threshold` (默认 15%):
            - 动作 1: `Liquidate()` (立即市价强平所有持仓)。
            - 动作 2: `activate_portfolio_cooldown()` (激活冷却期，如 360 天)。
            - 动作 3: 重置 HWM 为当前低点 (防止冷却结束后立即再次触发)。


# Part 10: Execution Pipeline - Step 3: Pair-level Health Check (配对级风控)

1. 概述 (Overview)
    对所有**持仓中**的配对进行全面体检。如果发现健康问题，立即触发强制平仓。
    检查逻辑由 `PairsManager.check_pairs_health()` 集中管理，`main.py` 负责执行平仓。

2. 检查维度 (Check Dimensions)
    按优先级从高到低依次检查，一旦发现问题立即报告并跳过后续检查（Short-circuit evaluation）。

    优先级 1: Anomaly (异常持仓)
        - 定义: 单边持仓 (只有 Leg1 或 Leg2) 或 同向持仓 (Leg1, Leg2 同为多或同为空)。
        - 动作: 立即平仓修正。

    优先级 2: Drawdown (单体回撤)
        - 定义: 该配对当前浮动回撤超过阈值 (默认 20%)。
        - 公式: `Drawdown = (PairHWM - CurrentPairValue) / PairHWM`
        - 动作: 止损平仓。

    优先级 3: Drift (对冲漂移)
        - 定义: 持仓市值偏离 Dollar Neutral 的程度。
        - 公式: `Drift = NetExposure / GrossExposure`
        - 阈值: 默认 25% (即净敞口占总敞口的 25% 以上)。
        - 动作: 平仓 (视为对冲失效，暴露了过大的 Beta 风险)。

    优先级 4: Timeout (持仓超时)
        - 定义: 持仓时间超过理论最大持有期。
        - 理论周期: $MaxDays = HalfLife \times \log_{0.5}(ExitThreshold / EntryZscore)$
        - 动作: 强制平仓 (承认均值回归失效)。

3. 执行机制 (Execution)
    - `PairsManager` 返回问题字典: `{'anomaly': [...], 'drawdown': [...], 'drift': [...], 'timeout': [...]}`。
    - `main.py` 遍历字典，为每个问题配对生成 `CloseIntent` (Reason = 问题类型)。
    - 调用 `OrderExecutor` 执行平仓。


# Part 11: Execution Pipeline - Step 4: Normal Close (正常平仓)

1. 概述 (Overview)
    处理非强制性的、符合策略预期的平仓信号。
    包括：均值回归获利平仓 (Take Profit) 和 协整破裂止损 (Stop Loss)。

2. 信号检测 (Signal Detection)
    由 `Pairs.get_signal(data)` 生成信号。

    场景 A: 均值回归 (CLOSE)
        - 条件: `abs(Z-score) < ExitThreshold` (默认 0.3σ)。
        - 含义: 价差已回归到均值附近，套利完成。
        - 动作: 生成 `CloseIntent` (Reason='MEAN_REVERSION')。

    场景 B: 协整破裂 (BREAK_UPPER / BREAK_LOWER) (v8.0.11)
        - 条件:
            - 多头持仓 (Long Spread) 且 `Z-score < -StopLossThreshold` (默认 -2.3σ) → 信号 `BREAK_LOWER`
            - 空头持仓 (Short Spread) 且 `Z-score > StopLossThreshold` (默认 2.3σ) → 信号 `BREAK_UPPER`
        - 含义: 价差不仅没有回归，反而向不利方向突破了统计边界，假设协整关系已失效。
        - 动作: 生成 `CloseIntent` (Reason='PAIR_BREAK')，原因层统一为 PAIR_BREAK。

3. 执行逻辑 (Execution Logic)
    - 遍历所有持仓配对 (`pairs_with_position`)。
    - 检查订单锁 (`is_pair_locked`): 防止在订单执行过程中重复发单。
    - 获取信号并执行:
        - 收到 `CLOSE` -> 执行均值回归平仓。
        - 收到 `BREAK_UPPER` 或 `BREAK_LOWER` -> 执行止损平仓 (v8.0.11: 信号层区分方向)。
    - 冷却期触发: 平仓完成后，`PairsManager` 会根据平仓原因 (Reason) 设定该配对的冷却期 (如止损后冷却 30 天，正常平仓冷却 0 天)。


# Part 12: Execution Pipeline - Step 5: VIX Check (开仓安全检查)

1. 目标 (Goal)
    在开新仓之前，检查市场恐慌指数 (VIX)。如果市场处于极度恐慌状态，暂停一切开仓活动，防止在系统性风险中逆势接飞刀。

2. 逻辑 (Logic)
    - 获取 VIX 最新值 (CBOE Volatility Index)。
    - 阈值判断:
        - 如果 `VIX >= Threshold` (默认 35):
            - 判定为恐慌状态 (Panic Mode)。
            - 禁止开仓 (`return`)。
        - 如果 `VIX < Threshold`:
            - 判定为安全状态。
            - 允许继续执行开仓逻辑。
    - 缺失处理: 如果 VIX 数据缺失，采取激进策略 (Aggressive)，默认允许开仓。


# Part 13: Execution Pipeline - Step 6: Normal Open (正常开仓)

1. 概述 (Overview)
    这是 `OnData` 的最后一步。经过了层层风控筛选后，终于可以寻找新的交易机会了。

2. 候选筛选 (Candidate Selection)
    由 `PairsManager.get_open_candidates_with_allocation(data)` 负责。
    筛选条件 (必须全部满足):
        - 资格: 必须是 `Current Selected` (本轮入选的配对)。
        - 信号: `Pairs.get_signal` 返回 `LONG_SPREAD` 或 `SHORT_SPREAD`。
        - 状态: 当前无持仓 (`has_position() == False`)。
        - 冷却: 不在冷却期内 (`is_in_cooldown() == False`)。
        - 锁: 没有未完成的订单锁 (`is_pair_locked() == False`)。

3. 排序与分配 (Sorting & Allocation)
    - 排序: 按 **平均交易回报 (Avg Return Per Trade)** 降序排列。优先交易那些历史表现好的“王牌配对”。
    - 资金分配:
        - 计算计划比例 (`planned_pct`): 根据历史回报分层 (如 Top Tier 给 10%，Low Tier 给 5%)。
        - 计算实际金额: `Allocated = InitialAvailableMargin * planned_pct`。
        - 门槛检查: 如果分配额 < 最小门槛 (如 $5000)，则放弃开仓 (资金太少不够磨损)。
        - 循环分配: 直到可用资金耗尽。

4. 执行开仓 (Execution)
    - 遍历分配到资金的候选配对。
    - 生成 `OpenIntent`: 计算具体的买卖数量 (Beta对冲)。
    - 调用 `OrderExecutor.execute_open(intent)` 发送订单。


================================================================================

# Appendix: Class Reference (类参考手册)

## Class: Pairs
核心数据对象，封装了配对的统计参数、持仓状态及信号逻辑。

### 1. Attributes (属性)

#### 1.1 Identity & Metadata (身份与元数据)
    - `pair_id`: Tuple[str, str]
        - 描述: 配对的唯一标识符，如 `('AAPL', 'MSFT')`。
    - `symbol1` / `symbol2`: Symbol
        - 描述: 构成配对的两个 QuantConnect Symbol 对象。
    - `industry_code`: str
        - 描述: 所属行业代码 (Morningstar Industry Group Code)。

#### 1.2 Model Parameters (模型参数)
    - `alpha`: float
        - 描述: 截距项 (Intercept)。
    - `beta`: float
        - 描述: 协整系数 (Cointegration Coefficient)。
    - `rho`: float
        - 描述: 自回归系数 (Autoregressive Coefficient)，决定均值回归速度。
    - `sigma_eta`: float
        - 描述: 残差标准差 (Residual Std Dev)，衡量波动率。
    - `model_result`: Dict
        - 描述: 存储完整的贝叶斯建模结果字典。

#### 1.3 Quality Metrics (质量指标)
    - `quality_score`: float
        - 描述: 配对综合质量评分 (0.0-1.0)，基于 Half-life, SNR, Zero-crossing。
    - `half_life`: float
        - 描述: 均值回归半衰期 (天)。

#### 1.4 State Management (状态管理)
    - `position_mode`: PositionMode (Property)
        - 描述: 当前持仓状态，值域: `NONE`, `LONG_SPREAD`, `SHORT_SPREAD`, `PARTIAL_LEG1`, `PARTIAL_LEG2`, `ANOMALY_SAME`。
    - `tracked_qty1` / `tracked_qty2`: int
        - 描述: 配对专属的持仓数量追踪。
    - `entry_zscore`: float
        - 描述: 开仓时的 Z-score，用于后续计算超时阈值。
    - `entry_price1` / `entry_price2`: float
        - 描述: 开仓时的成交均价，用于计算成本和 PnL。
    - `entry_time`: datetime
        - 描述: 开仓时间，用于计算持仓天数。
    - `max_holding_days`: float
        - 描述: 理论最大持仓天数，开仓时动态计算。

#### 1.5 Statistics (历史统计)
    - `trade_history`: List[Tuple[datetime, float, float]]
        - 描述: 历史交易记录 (平仓时间, PnL, 投入资本)。
    - `trade_count`: int
        - 描述: 累计交易次数。
    - `win_count`: int
        - 描述: 累计盈利次数。
    - `total_pnl`: float
        - 描述: 累计盈亏金额。
    - `realized_roi`: float
        - 描述: 累计已实现 ROI。
    - `win_rate`: float
        - 描述: 胜率 (win_count / trade_count)。

#### 1.6 Configuration (配置阈值)
    - `entry_threshold_lower` / `entry_threshold_upper`: float (1.9 - 2.3)
    - `exit_threshold`: float (0.3)
    - `stop_loss_threshold`: float (2.5)

### 2. Methods (方法)

#### 2.1 Core Logic (核心逻辑)
    - `get_zscore(price1, price2) -> float`
        - 描述: 计算当前价格对应的 Z-score。
    - `get_signal(data) -> str`
        - 描述: 生成交易信号 (`LONG_SPREAD`, `SHORT_SPREAD`, `CLOSE`, `BREAK_UPPER`, `BREAK_LOWER`, `WAIT`, `HOLD`) (v8.0.11)。
    - `get_open_intent(amount_allocated, data) -> OpenIntent`
        - 描述: 生成开仓意图，计算 Beta 对冲后的目标股数。
    - `get_close_intent(reason) -> CloseIntent`
        - 描述: 生成平仓意图，根据当前持仓生成反向指令。

#### 2.2 Lifecycle Callbacks (生命周期回调)
    - `on_position_filled(action, fill_time, tickets, reason)`
        - 描述: 订单完全成交后的回调。
        - 逻辑: 更新持仓状态 (`tracked_qty`)，记录开仓价格/时间，或结算平仓 PnL 并更新历史统计。
    - `update_params(new_pair)`
        - 描述: 使用新一轮建模结果更新统计参数 (仅当无持仓时)。

#### 2.3 Calculations & Analytics (计算与分析)
    - `get_leg_values(allocated_amount, signal, data) -> (float, float)`
        - 描述: 计算两腿的目标市值 (基于 Beta 对冲)。
    - `get_pair_holding_days() -> int`
        - 描述: 获取当前持仓天数。
    - `get_max_holding_days() -> float`
        - 描述: 获取理论最大持仓天数 (基于 Ornstein-Uhlenbeck 过程)。
    - `get_pair_drawdown() -> float`
        - 描述: 计算当前持仓的浮动回撤率。
    - `get_hedge_drift() -> float`
        - 描述: 计算对冲漂移率 (Net Exposure / Gross Exposure)。
    - `get_pair_cumulative_roi() -> float`
        - 描述: 计算历史累计 ROI (v8.0.8: 仅已平仓部分)。
        - 公式: `pair_accum_realized_pnl / pair_past_invested_capital`
        - 用途: 供 PairSelector 历史ROI过滤使用 (v8.0.9)
    - `get_avg_return_per_trade() -> float`
        - 描述: 计算平均每笔交易回报率 (用于资金分配排序)。

#### 2.4 State Queries (状态查询)
    - `has_position() -> bool`
    - `has_normal_position() -> bool`
    - `has_anomaly_position() -> bool`
    - `is_in_cooldown() -> bool`
    - `get_pair_unrealized_pnl() -> float`
    - `get_pair_current_invested_capital() -> float`
    - `get_hedge_drift() -> float`


================================================================================

## Class: PairsManager
配对管理器，负责管理整个回测周期内所有配对的生命周期、行业数据聚合及资金分配。

### 1. Attributes (属性)

#### 1.1 Pair Collections (配对集合)
    - `all_pairs`: Dict[tuple, Pairs]
        - 描述: 存储所有已创建的配对对象 (Key: pair_id)。
    - `current_selected_pair_ids`: Set[tuple]
        - 描述: 当前选股周期内入选的配对 ID 集合 (Active Universe)。
    - `past_selected_pair_ids`: Set[tuple]
        - 描述: 历史曾入选但当前落选的配对 ID 集合 (Passive Universe)。

#### 1.2 Industry Data (行业数据)
    - `industry_data_map`: Dict[str, IndustryData]
        - 描述: 存储各行业的聚合统计数据 (Key: industry_code)。
        - 内容: 包含该行业的总 ROI、胜率、盈亏、敞口等。

#### 1.3 Configuration (配置)
    - `module_config`: PairsManagerConfig
        - 描述: 包含保证金比例、风控阈值、冷却期设置等。

### 2. Methods (方法)

#### 2.1 Lifecycle Management (生命周期管理)
    - `classify_pairs(new_pairs_dict)`
        - 描述: 每月选股后调用，更新 `current_selected` 和 `past_selected` 集合。
        - 逻辑: 增量更新，对已存在的配对仅更新参数 (`update_params`)，对新配对进行注册。
    - `get_pair_by_id(pair_id) -> Pairs`
        - 描述: 根据 ID 获取配对对象。
    - `get_pairs_with_position() -> Dict`
        - 描述: 获取当前所有持仓配对。

#### 2.2 Industry Analytics (行业分析)
    - `_aggregate_all_industry_data()`
        - 描述: 遍历所有配对，聚合计算各行业的统计指标。
    - `get_industry_composite_score(industry_code) -> float`
        - 描述: 计算行业综合得分 (Realized ROI × Win Rate)，用于配额分配。
    - `get_industry_realized_roi(industry_code, window_days) -> float`
        - 描述: 获取行业已实现 ROI (支持滚动窗口)。
    - `get_industry_win_rate(industry_code, window_days) -> float`
        - 描述: 获取行业胜率 (支持滚动窗口)。
    - `get_industry_concentration(industry_code) -> float`
        - 描述: 计算行业资金集中度 (该行业持仓市值 / 总持仓市值)。

#### 2.3 Capital Allocation (资金分配)
    - `get_open_candidates_with_allocation(data) -> List`
        - 描述: 筛选开仓候选并完成资金分配。
        - 流程: 筛选(Signal+NoPosition+NoCooldown) -> 排序(AvgReturn) -> 分配(Allocate)。
    - `allocate_margin_to_candidates(open_candidates) -> Dict`
        - 描述: 根据候选配对的层级分配保证金。
    - `get_available_margin() -> float`
        - 描述: 计算当前可用保证金 (TotalMargin - FixedBuffer)。

#### 2.4 Health & Risk (健康与风控)
    - `check_pairs_health() -> Dict`
        - 描述: 执行所有配对的健康检查 (Anomaly, Drawdown, Drift, Timeout)。
    - `get_cooldown_required_days(reason) -> int`
        - 描述: 根据平仓原因查询所需的冷却天数。


## Class: IndustryData
行业数据对象 (Value Object)，封装单个行业的聚合统计数据。

### 1. 设计原则
    - 纯数据对象: 存储单个行业的聚合统计
    - 渐进式扩展: 从单字段开始,逐步添加更多字段
    - 外部类: 与 PairsManager 同级,便于测试和访问

### 2. Attributes (属性)

#### 2.1 PnL 维度
    - `unrealized_pnl`: float
        - 描述: 未实现盈亏 (持仓中配对的浮动盈亏)
    - `realized_pnl`: float
        - 描述: 已实现盈亏 (已平仓交易的累计盈亏)

#### 2.2 投入资本维度
    - `current_invested_capital`: float
        - 描述: 当前投入资本 (持仓中配对的投入)
    - `past_invested_capital`: float
        - 描述: 历史投入资本 (已平仓交易的累计投入)

#### 2.3 交易质量维度 (v7.57.0)
    - `trade_count`: int
        - 描述: 交易次数 (已平仓交易计数)
    - `win_count`: int
        - 描述: 盈利次数 (pnl > 0 的交易计数)
    - `past_total_holding_days`: float
        - 描述: 累计持仓天数 (已平仓交易)

#### 2.4 敞口维度 (v7.59.0)
    - `net_exposure`: float
        - 描述: 净敞口 (long_value - short_value)
    - `gross_exposure`: float
        - 描述: 总敞口 (long_value + short_value)

#### 2.5 滚动窗口维度 (v8.0.0)
    - `trade_history`: List[Tuple[datetime, float, float]]
        - 描述: 单笔交易记录列表
        - 格式: [(exit_time, pnl, invested_capital), ...]
        - 用途: 供行业级滚动窗口计算 (180天/20笔最小样本)

### 3. 使用场景
    - 由 `PairsManager._aggregate_*()` 方法创建
    - 供查询接口 `get_industry_*()` / `get_total_*()` 返回数据


