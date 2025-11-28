# region imports
from AlgorithmImports import *
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple
# endregion


class IndustryData:
    """
    行业数据对象 - 存储单个行业的聚合统计

    字段:
        historical_pnl: 历史累积盈亏 (已平仓)
        current_invested_capital: 当前投入资本 (持仓中)
        historical_invested_capital: 历史累积投入 (已平仓)
        trade_count: 交易次数
        win_count: 盈利次数
        trade_history: 交易记录 [(exit_time, pnl, invested_capital), ...]
    """

    def __init__(self, industry_code: str,
                 historical_pnl: float = 0.0,
                 current_invested_capital: float = 0.0,
                 historical_invested_capital: float = 0.0,
                 trade_count: int = 0,
                 win_count: int = 0,
                 trade_history: List[Tuple[datetime, float, float]] = None):
        self.industry_code = industry_code
        self.historical_pnl = historical_pnl
        self.current_invested_capital = current_invested_capital
        self.historical_invested_capital = historical_invested_capital
        self.trade_count = trade_count
        self.win_count = win_count
        self.trade_history: List[Tuple[datetime, float, float]] = trade_history if trade_history is not None else []


class PairsManager:
    """
    配对管理器 - 管理配对生命周期、行业数据聚合、资金分配

    核心职责:
        1. 配对生命周期管理 (创建、更新、分类)
        2. 配对查询接口 (按状态、按持仓)
        3. 行业数据聚合与查询
        4. 资金分配管理
    """

    # ===== 1. 初始化层 (Initialization) =====
    # 特征: 属性初始化, 无业务逻辑

    def __init__(self, algorithm, config):
        """
        初始化配对管理器

        Args:
            algorithm: QCAlgorithm实例
            config: 策略配置对象
        """
        self.algorithm = algorithm
        self.config = config

        # === 主存储 ===
        self.all_pairs = {}  # {pair_id: Pairs对象}

        # === 分类索引 (只存储pair_id) - v7.53.0 简化为两分类 ===
        self.current_selected_pair_ids = set()                      # 本轮被PairSelector选中
        self.past_selected_pair_ids = set()                         # 历史配对 (曾被选中,本轮未选中)

        # === 统计信息 ===
        self.update_count = 0                                       # 更新次数(选股轮次)
        self.last_update_time = None                                # 上次更新时间

        # === 固定初始资金基准（整个回测周期不变，v7.62.1）===
        self.INITIAL_CAPITAL = algorithm.Portfolio.TotalPortfolioValue
        self.FIXED_BUFFER = self.INITIAL_CAPITAL * (1 - config.pairs_manager.margin_usage_ratio)

        # === 模块配置引用 (v7.98.4: 修复未初始化bug) ===
        self.module_config = config.pairs_manager

        algorithm.Debug(
            f"[PairsManager] 初始化完成: "
            f"初始资金=${self.INITIAL_CAPITAL:,.0f}, "
            f"固定Buffer=${self.FIXED_BUFFER:,.0f} ({(1-config.pairs_manager.margin_usage_ratio)*100:.0f}%)"
        )


    # ===== 2. 数据访问层 (Data Access) =====
    # 特征: 读取self属性或外部数据

    def get_pair_by_id(self, pair_id):
        """通过pair_id获取Pairs对象，返回None表示不存在"""
        return self.all_pairs.get(pair_id)


    def _aggregate_all_industry_data(self) -> Dict[str, IndustryData]:
        """遍历所有配对，聚合各行业的统计数据"""
        industry_data: Dict[str, IndustryData] = {}

        for _, pair in self.all_pairs.items():
            industry_code = str(pair.industry_code)

            # 懒创建 IndustryData 对象
            if industry_code not in industry_data:
                industry_data[industry_code] = IndustryData(industry_code)

            data = industry_data[industry_code]

            # === PnL维度 ===
            data.historical_pnl += pair.pair_historical_pnl

            # === 投入资本维度 ===
            current_invested = pair.get_pair_current_invested_capital()
            if current_invested is not None:
                data.current_invested_capital += current_invested
            data.historical_invested_capital += pair.pair_historical_invested_capital

            # === 交易质量维度 ===
            data.trade_count += pair.trade_count
            data.win_count += pair.win_count

            # === 滚动窗口维度 (v8.0.0) ===
            data.trade_history.extend(pair.trade_history)

        return industry_data


    # ===== 3. 业务逻辑层 (Business Logic) =====
    # 特征: 组合数据访问层方法, 包含条件判断, 实现复杂业务逻辑

    # ----- 3A. 配对生命周期管理 -----

    def classify_pairs(self, new_pairs_dict: Dict):
        """每月选股后分类管理配对 (增量索引更新)"""
        self.update_count += 1
        self.last_update_time = self.algorithm.Time

        new_pair_ids = set(new_pairs_dict.keys())

        # Step 1: 更新 all_pairs (持仓检查放在外面, 单一职责)
        for pair_id, new_pair in new_pairs_dict.items():
            if pair_id in self.all_pairs:
                old_pair = self.all_pairs[pair_id]
                if not old_pair.has_position():  # 有持仓时不更新参数
                    old_pair.update_params(new_pair)
            else:
                # 新配对: 直接添加
                self.all_pairs[pair_id] = new_pair

        # Step 2: 增量索引更新
        # 2a. 从 current 降级到 past (本轮未被选中的)
        demoted_ids = self.current_selected_pair_ids - new_pair_ids
        self.past_selected_pair_ids |= demoted_ids
        self.current_selected_pair_ids -= demoted_ids

        # 2b. 加入本轮新选中的 (包括新创建的和从past升级的)
        self.current_selected_pair_ids |= new_pair_ids

        # 输出统计
        self.algorithm.Debug(
            f"[配对分类] 总配对={len(self.all_pairs)}, "
            f"当前选中={len(self.current_selected_pair_ids)}, "
            f"历史配对={len(self.past_selected_pair_ids)}"
        )


    # ===== 4. 外部接口层 (Public API) =====
    # 特征: 对外暴露的核心接口, 整合各层实现完整功能

    # ----- 4A. 配对查询接口 -----

    def get_pairs_with_position(self) -> Dict:
        """获取所有有持仓的配对"""
        return {
            pair_id: pair
            for pair_id, pair in self.all_pairs.items()
            if pair.has_position()
        }


    # ----- 4B. 情报中心 (行业统计查询 - v8.0.13 简化) -----
    # 优化: 所有查询统一调用 _aggregate_all_industry_data(), 一次遍历

    # --- PnL 组 ---

    def get_industry_historical_pnl(self, industry_code: str) -> float:
        """获取指定行业的历史累积盈亏 (已平仓交易)"""
        industry_data = self._aggregate_all_industry_data()
        if industry_code in industry_data:
            return industry_data[industry_code].historical_pnl
        return 0.0

    # --- 投入资本组 ---

    def get_industry_current_invested_capital(self, industry_code: str) -> float:
        """获取指定行业的当前投入资本 (持仓中)"""
        industry_data = self._aggregate_all_industry_data()
        if industry_code in industry_data:
            return industry_data[industry_code].current_invested_capital
        return 0.0

    def get_industry_historical_invested_capital(self, industry_code: str) -> float:
        """获取指定行业的历史累积投入资本 (已平仓交易)"""
        industry_data = self._aggregate_all_industry_data()
        if industry_code in industry_data:
            return industry_data[industry_code].historical_invested_capital
        return 0.0

    # --- 交易质量组 ---

    def get_industry_trade_count(self, industry_code: str) -> int:
        """获取指定行业的交易次数 (已平仓交易计数)"""
        industry_data = self._aggregate_all_industry_data()
        if industry_code in industry_data:
            return industry_data[industry_code].trade_count
        return 0

    def get_industry_composite_score(self, industry_code: str) -> float:
        """
        计算行业综合得分: rolling_roi × rolling_win_rate

        步骤:
            1. 聚合行业交易数据
            2. 筛选滚动窗口内的交易 (rolling_window_days天，不足时取最近min_samples笔)
            3. 计算 rolling_roi = sum(pnl) / sum(invested_capital)
            4. 计算 rolling_win_rate = win_count / trade_count
            5. 返回 roi × win_rate (用于配额分配权重)
        """
        # === 步骤1: 读取配置 (v8.0.26: 配置路径归属PairsManagerConfig) ===
        window_days = self.config.pairs_manager.rolling_window_days
        min_samples = self.config.pairs_manager.min_samples_for_window

        # === 步骤2: 聚合行业数据 ===
        industry_data = self._aggregate_all_industry_data()
        if industry_code not in industry_data:
            return 0.0

        data = industry_data[industry_code]

        # === 步骤3: 获取该行业所有交易记录 ===
        # trade_history 格式: List[(exit_time, pnl, invested_capital)]
        all_trades = data.trade_history
        if not all_trades:
            return 0.0

        # === 步骤4: 滚动窗口筛选 ===
        # v8.0.6: 统一为 timezone-naive 避免 "offset-naive and offset-aware" 比较错误
        cutoff_time = self.algorithm.Time.replace(tzinfo=None) - timedelta(days=window_days)
        window_trades = [r for r in all_trades if r[0].replace(tzinfo=None) >= cutoff_time]

        # 样本量保底: 窗口内不足 min_samples 时, 取最近 min_samples 笔
        if len(window_trades) < min_samples:
            window_trades = all_trades[-min_samples:]

        if not window_trades:
            return 0.0

        # === 步骤5: 计算指标 ===
        total_pnl = sum(r[1] for r in window_trades)
        total_capital = sum(r[2] for r in window_trades)
        win_count = sum(1 for r in window_trades if r[1] > 0)
        trade_count = len(window_trades)

        # 计算 ROI 和 Win Rate
        rolling_roi = total_pnl / total_capital if total_capital > 0 else 0.0
        rolling_win_rate = win_count / trade_count if trade_count > 0 else 0.0

        return rolling_roi * rolling_win_rate

    def get_industry_roi(self, industry_code: str) -> float:
        """
        获取行业级历史累积ROI

        公式: historical_pnl / historical_invested_capital
        口径: 只计算已平仓交易，不含当前持仓
        """
        industry_data = self._aggregate_all_industry_data()
        if industry_code not in industry_data:
            return 0.0

        data = industry_data[industry_code]
        if data.historical_invested_capital <= 0:
            return 0.0

        return data.historical_pnl / data.historical_invested_capital

    def get_industry_win_rate(self, industry_code: str) -> float:
        """获取行业胜率 (win_count / trade_count, 平仓口径)"""
        industry_data = self._aggregate_all_industry_data()
        if industry_code not in industry_data:
            return 0.0

        data = industry_data[industry_code]
        if data.trade_count <= 0:
            return 0.0

        return data.win_count / data.trade_count

    # --- Concentration 组 (v7.91.0) ---

    def get_industry_concentration(self, industry_code: str) -> float:
        """获取行业集中度 (industry_current_invested / total_current_invested)"""
        industry_data = self._aggregate_all_industry_data()

        # 分子: 目标行业的当前投入资本
        if industry_code not in industry_data:
            return 0.0
        industry_invested = industry_data[industry_code].current_invested_capital

        # 分母: 全策略的当前投入资本
        total_invested = sum(d.current_invested_capital for d in industry_data.values())

        if total_invested <= 0:
            return 0.0

        return industry_invested / total_invested


    # ----- 4C. 健康检查接口 -----
    # 设计: Pairs层面 + Industry层面 (未来扩展)

    def check_pairs_health(self) -> Dict[str, List[str]]:
        """
        配对健康检查，按优先级返回问题配对

        检查维度 (按优先级):
            1. Anomaly: 单边或同向持仓异常
            2. Drawdown: 配对回撤超过阈值
            3. Drift: 对冲漂移超过阈值
            4. Timeout: 持仓超时

        返回: {'anomaly': [...], 'drawdown': [...], 'drift': [...], 'timeout': [...]}
        注: 每个配对只返回最高优先级问题
        """
        health_issues = {'anomaly': [], 'drawdown': [], 'drift': [], 'timeout': []}

        # 获取配置阈值 (v7.97.0: 从 pairs_manager 读取)
        pm_config = self.module_config
        drawdown_threshold = pm_config.drawdown_threshold
        drift_threshold = pm_config.drift_threshold

        for pair in self.get_pairs_with_position().values():
            # 优先级1: Anomaly (最高优先级)
            if pair.has_anomaly_position():
                health_issues['anomaly'].append(pair.pair_id)
                continue  # 跳过后续检查

            # 优先级2: Drawdown (回撤超过阈值)
            drawdown = pair.get_pair_drawdown()
            if drawdown is not None and drawdown > drawdown_threshold:
                health_issues['drawdown'].append(pair.pair_id)
                continue  # 跳过后续检查

            # 优先级3: Drift (对冲漂移超过阈值) (v7.87.0, v7.87.1: 小数形式)
            drift = pair.get_hedge_drift()
            if drift is not None and abs(drift) > drift_threshold:
                health_issues['drift'].append(pair.pair_id)
                continue  # 跳过后续检查

            # 优先级4: Timeout (持仓超时) (v7.87.1)
            # 复用 Pairs.get_max_holding_days() 和 get_pair_holding_days()
            max_days = pair.get_max_holding_days()
            holding_days = pair.get_pair_holding_days()
            if max_days is not None and holding_days is not None:
                if holding_days > max_days:
                    health_issues['timeout'].append(pair.pair_id)

        return health_issues


    def check_industry_concentration(self) -> List[str]:
        """检测超过集中度阈值的行业，返回行业代码列表"""
        over_concentrated = []

        # 从配置获取阈值
        threshold = self.module_config.concentration_threshold

        # 获取所有行业数据
        industry_data = self._aggregate_all_industry_data()

        for industry_code in industry_data.keys():
            concentration = self.get_industry_concentration(industry_code)
            if concentration > threshold:
                over_concentrated.append(industry_code)

        return over_concentrated


    # ----- 4D 资金分配管理 (v7.62.0 从 MarginAllocator 迁移) -----
    # 职责: 计算可用保证金 + 为入场候选配对分配资金
    # 设计: 双模式分配(放大 vs 保护) + Fixed Buffer

    def get_available_margin(self) -> float:
        """获取可用保证金 (MarginRemaining - FIXED_BUFFER)"""
        # 直接使用初始化时计算的固定 buffer
        current_margin = self.algorithm.Portfolio.MarginRemaining
        available = current_margin - self.FIXED_BUFFER  # 使用固定值

        return max(0, available)


    def allocate_margin_to_candidates(self, open_candidates: List[tuple]) -> Dict[tuple, float]:
        """
        为开仓候选配对分配保证金

        输入: [(pair, signal, quality_score, planned_pct), ...]
        输出: {pair_id: allocated_amount}

        算法:
            1. 获取初始可用保证金 (固定基准)
            2. 计算最小投资门槛 (基于INITIAL_CAPITAL)
            3. 顺序分配: 分配额 = 初始可用 × planned_pct
            4. 检查门槛+剩余资金，不足则跳过
        """
        allocations = {}

        # === Step 1: 获取初始可用保证金（固定基准）===
        initial_available = self.get_available_margin()

        # 计算最小投资门槛（基于固定的 INITIAL_CAPITAL）
        min_threshold = self.INITIAL_CAPITAL * self.config.pairs_manager.min_investment_ratio

        # 资金充足性检查
        if initial_available < min_threshold:
            self.algorithm.Debug(
                f"[资金分配] 可用保证金不足: "
                f"${initial_available:,.0f} < 最小门槛${min_threshold:,.0f} "
                f"({self.config.pairs_manager.min_investment_ratio*100:.0f}%初始资金)"
            )
            return {}

        # === Step 2: 顺序分配 ===
        remaining_available = initial_available  # 追踪剩余资金

        for pair, signal, quality_score, planned_pct in open_candidates:
            # 基于固定基准计算分配额（天然动态缩放）
            planned_allocated = initial_available * planned_pct

            # 检查门槛 + 检查剩余资金
            if planned_allocated >= min_threshold and remaining_available >= planned_allocated:
                allocations[pair.pair_id] = planned_allocated
                remaining_available -= planned_allocated
            else:
                continue  # 跳过该配对

        # === Step 3: 返回分配结果 ===
        return allocations


    def get_planned_allocation_pct(self, pair) -> float:
        """计算配对的计划分配比例 (基于avg_return_per_trade层级匹配)"""
        cfg = self.module_config

        avg_return = pair.get_avg_return_per_trade()

        # 无交易历史 → 默认分配
        if avg_return is None:
            return cfg.allocation_default

        # 查找匹配的层级
        for threshold, allocation_pct in cfg.allocation_tiers:
            if avg_return <= threshold:
                return allocation_pct

        # 超过所有层级 → 最大分配
        return cfg.allocation_max


    def get_open_candidates_with_allocation(self, data) -> List[tuple]:
        """
        获取开仓候选并完成资金分配

        步骤:
            1. 从current_selected筛选有信号+无持仓+不在冷却期的配对
            2. 按avg_return_per_trade降序排序
            3. 计算每个配对的planned_pct
            4. 调用allocate_margin_to_candidates分配资金
            5. 合并结果返回

        返回: [(pair, signal, allocated_margin), ...]
        """
        # Step 1: 筛选候选池 (只要current_selected)
        candidates_with_signal = []

        for pair_id in self.current_selected_pair_ids:
            pair = self.all_pairs[pair_id]

            # 过滤条件: 有开仓信号 + 无持仓 + 不在冷却期 + 无订单锁
            signal = pair.get_signal(data)
            if signal not in ['LONG_SPREAD', 'SHORT_SPREAD']:
                continue

            if pair.has_position():
                continue

            if pair.is_in_cooldown():
                continue

            if self.algorithm.tickets_manager.is_pair_locked(pair.pair_id):
                continue

            candidates_with_signal.append(pair)

        # Step 2: 按平均交易回报降序排序 (v7.99.5: 与分配逻辑统一)
        # None (无交易历史) 排在最后，让有历史表现的配对优先开仓
        candidates_with_signal.sort(
            key=lambda p: p.get_avg_return_per_trade() if p.get_avg_return_per_trade() is not None else -float('inf'),
            reverse=True
        )

        # Step 3: 构建中间列表 (添加planned_pct)
        open_candidates = []
        for pair in candidates_with_signal:
            signal = pair.get_signal(data)
            planned_pct = self.get_planned_allocation_pct(pair)
            open_candidates.append((pair, signal, pair.quality_score, planned_pct))

        # Step 4: 资金分配
        allocations = self.allocate_margin_to_candidates(open_candidates)

        # Step 5: 合并分配结果
        final_candidates = []
        for pair, signal, quality_score, planned_pct in open_candidates:
            allocated = allocations.get(pair.pair_id)
            if allocated:
                final_candidates.append((pair, signal, allocated))

        return final_candidates


    # ===== 6. 配置查询路由 =====

    def get_cooldown_required_days(self, last_close_reason: str) -> int:
        """查询冷却期天数 (从配置Dict读取，默认10天)"""
        return self.module_config.cooldown_days.get(last_close_reason, 7)


