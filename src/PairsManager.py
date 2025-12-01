# region imports
from AlgorithmImports import *
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple
from src.Pairs import PositionMode
import math
# endregion


class IndustryData:
    """
    行业数据对象 (v8.1.0: 单一事实来源 - 仅保留trade_history)

    字段:
        industry_code: 行业代码
        trade_history: 交易记录 [(entry_time, exit_time, pnl, invested_capital), ...]

    设计原则:
        - 移除累积字段 (historical_pnl, trade_count等)
        - 所有指标从 trade_history 动态计算
    """

    def __init__(self, industry_code: str):
        self.industry_code = industry_code
        self.trade_history: List[Tuple[datetime, datetime, float, float]] = []


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

        # === 行业数据缓存 (v8.0.17: 时间戳缓存避免重复计算) ===
        self._industry_cache: Dict[str, IndustryData] = {}
        self._industry_cache_time: datetime = None

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
        """
        遍历所有配对，聚合各行业的 trade_history

        v8.1.0: 单一事实来源 - 只聚合 trade_history，其他指标动态计算
        v8.0.17: 时间戳缓存优化
        """
        # === 缓存命中检查 ===
        current_time = self.algorithm.Time
        if self._industry_cache_time == current_time and self._industry_cache:
            return self._industry_cache

        # === 缓存未命中: 重新计算 ===
        industry_data: Dict[str, IndustryData] = {}

        for _, pair in self.all_pairs.items():
            industry_code = str(pair.industry_code)

            # 懒创建 IndustryData 对象
            if industry_code not in industry_data:
                industry_data[industry_code] = IndustryData(industry_code)

            # v8.1.0: 只聚合 trade_history，其他指标动态计算
            industry_data[industry_code].trade_history.extend(pair.trade_history)

        # === 更新缓存 ===
        self._industry_cache = industry_data
        self._industry_cache_time = current_time

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


    # ----- 4B. 情报中心 (行业统计查询 - v8.1.0 单一事实来源) -----
    # v8.1.0: 所有指标从 trade_history 动态计算
    # 四元组结构: (entry_time, exit_time, pnl, invested_capital)

    # --- 统一动态查询 (v8.1.0 新增) ---

    def get_industry_stats(self, industry_code: str, window_days: int = None) -> Dict:
        """
        获取行业统计数据 (v8.1.0: 单一入口，按需聚合)

        Args:
            industry_code: 行业代码
            window_days: 滚动窗口天数 (None=全历史)

        Returns:
            {trade_count, win_count, total_pnl, total_capital, roi, win_rate, avg_holding_days}
        """
        industry_data = self._aggregate_all_industry_data()
        if industry_code not in industry_data:
            return self._empty_stats()

        trades = industry_data[industry_code].trade_history

        # 时间窗口筛选 (使用 exit_time, index=1)
        if window_days is not None:
            cutoff = self.algorithm.Time.replace(tzinfo=None) - timedelta(days=window_days)
            trades = [r for r in trades if r[1].replace(tzinfo=None) >= cutoff]

        if not trades:
            return self._empty_stats()

        # 动态计算所有指标
        trade_count = len(trades)
        win_count = sum(1 for r in trades if r[2] > 0)  # pnl > 0
        total_pnl = sum(r[2] for r in trades)           # pnl
        total_capital = sum(r[3] for r in trades)       # invested_capital
        avg_holding = sum((r[1] - r[0]).days for r in trades) / trade_count  # exit - entry

        return {
            'trade_count': trade_count,
            'win_count': win_count,
            'total_pnl': total_pnl,
            'total_capital': total_capital,
            'roi': total_pnl / total_capital if total_capital > 0 else 0.0,
            'win_rate': win_count / trade_count,
            'avg_holding_days': avg_holding
        }

    def _empty_stats(self) -> Dict:
        """返回空统计数据"""
        return {
            'trade_count': 0,
            'win_count': 0,
            'total_pnl': 0.0,
            'total_capital': 0.0,
            'roi': 0.0,
            'win_rate': 0.0,
            'avg_holding_days': 0.0
        }

    # --- 便捷查询方法 (v8.1.0: 委托给 get_industry_stats) ---

    def get_industry_historical_pnl(self, industry_code: str) -> float:
        """获取指定行业的历史累积盈亏 (已平仓交易)"""
        return self.get_industry_stats(industry_code)['total_pnl']

    def get_industry_historical_invested_capital(self, industry_code: str) -> float:
        """获取指定行业的历史累积投入资本 (已平仓交易)"""
        return self.get_industry_stats(industry_code)['total_capital']

    def get_industry_trade_count(self, industry_code: str) -> int:
        """获取指定行业的交易次数 (已平仓交易计数)"""
        return self.get_industry_stats(industry_code)['trade_count']

    def get_industry_roi(self, industry_code: str) -> float:
        """获取行业级历史累积ROI (total_pnl / total_capital)"""
        return self.get_industry_stats(industry_code)['roi']

    def get_industry_win_rate(self, industry_code: str) -> float:
        """获取行业胜率 (win_count / trade_count)"""
        return self.get_industry_stats(industry_code)['win_rate']

    # --- 实时数据查询 (直接从 pairs 聚合，不走 trade_history) ---

    def get_industry_current_invested_capital(self, industry_code: str) -> float:
        """获取指定行业的当前投入资本 (持仓中，实时数据)"""
        total = 0.0
        for pair in self.all_pairs.values():
            if str(pair.industry_code) == industry_code:
                invested = pair.get_pair_current_invested_capital()
                if invested is not None:
                    total += invested
        return total

    def get_industry_composite_score(self, industry_code: str) -> float:
        """
        计算行业综合得分: rolling_roi × rolling_win_rate

        v8.1.0: 使用四元组结构
        - trade_history 格式: (entry_time, exit_time, pnl, invested_capital)
        """
        window_days = self.config.pairs_manager.rolling_window_days
        min_samples = self.config.pairs_manager.min_samples_for_window

        industry_data = self._aggregate_all_industry_data()
        if industry_code not in industry_data:
            return 0.0

        all_trades = industry_data[industry_code].trade_history
        if not all_trades:
            return 0.0

        # 滚动窗口筛选 (使用 exit_time, index=1)
        cutoff_time = self.algorithm.Time.replace(tzinfo=None) - timedelta(days=window_days)
        window_trades = [r for r in all_trades if r[1].replace(tzinfo=None) >= cutoff_time]

        # 样本量保底
        if len(window_trades) < min_samples:
            window_trades = all_trades[-min_samples:]

        if not window_trades:
            return 0.0

        # v8.1.0: 更新索引 - pnl=r[2], capital=r[3]
        total_pnl = sum(r[2] for r in window_trades)
        total_capital = sum(r[3] for r in window_trades)
        win_count = sum(1 for r in window_trades if r[2] > 0)
        trade_count = len(window_trades)

        rolling_roi = total_pnl / total_capital if total_capital > 0 else 0.0
        rolling_win_rate = win_count / trade_count if trade_count > 0 else 0.0

        return rolling_roi * rolling_win_rate

    # --- Concentration 组 (v8.1.0: 实时数据，直接从 pairs 聚合) ---

    def get_industry_concentration(self, industry_code: str) -> float:
        """获取行业集中度 (industry_current_invested / total_current_invested)"""
        industry_invested = self.get_industry_current_invested_capital(industry_code)

        # 分母: 全策略的当前投入资本 (遍历所有 pairs)
        total_invested = 0.0
        for pair in self.all_pairs.values():
            invested = pair.get_pair_current_invested_capital()
            if invested is not None:
                total_invested += invested

        if total_invested <= 0:
            return 0.0

        return industry_invested / total_invested


    # ----- 4C. 健康检查接口 -----
    # 设计: Pairs层面 + Industry层面 (未来扩展)

    def check_pairs_health(self, data) -> Dict[str, List[str]]:
        """
        配对健康检查 (v8.6.0: 简化版 - 删除盈利/亏损分支，删除VALUE漂移)

        检查维度 (按优先级):
            1. Anomaly: 单边或同向持仓异常
            2. PairBreak: Z-score + β漂移 双重验证 (AND逻辑)
            3. Timeout: 持仓超时
            4. Drawdown: 统一8%阈值

        v8.6.0 关键变更:
            - 新增 AND 逻辑: zscore触发 + beta_drift > threshold → 确认破裂
            - 噪声跳过: zscore触发 BUT beta_drift ≤ threshold → 判定为噪声
            - 删除 VALUE 漂移检查 (被 β 漂移取代)
            - 删除 盈利/亏损分支 (统一阈值)

        返回: {'anomaly': [], 'pair_break': [], 'timeout': [], 'drawdown': []}
        注: 每个配对只返回最高优先级问题
        """
        health_issues = {
            'anomaly': [],
            'pair_break': [],
            'timeout': [],
            'drawdown': []
        }

        # 获取配置阈值
        pm_config = self.module_config
        pair_break_threshold = pm_config.pair_break_threshold
        beta_drift_threshold = pm_config.beta_drift_threshold
        drawdown_threshold = pm_config.drawdown_threshold

        for pair in self.get_pairs_with_position().values():
            pair_id = pair.pair_id

            # 优先级1: Anomaly (最高优先级)
            if pair.has_anomaly_position():
                health_issues['anomaly'].append(pair_id)
                continue

            # 优先级2: PairBreak (Z-score + β漂移 双重验证)
            prices = pair.get_price_from_bar(data)
            if prices is not None:
                # 先更新卡尔曼滤波
                pair.update_kalman_beta(prices[0], prices[1])

                zscore = pair.get_zscore(prices[0], prices[1])
                if zscore is not None:
                    position_mode = pair.position_mode
                    # 方向感知: 检测亏损方向的突破
                    zscore_triggered = (
                        (position_mode == PositionMode.LONG_SPREAD and zscore < -pair_break_threshold) or
                        (position_mode == PositionMode.SHORT_SPREAD and zscore > pair_break_threshold)
                    )

                    if zscore_triggered:
                        # AND 逻辑: 需要 β 漂移确认
                        beta_drift = pair.get_beta_drift()
                        if beta_drift is not None and beta_drift > beta_drift_threshold:
                            # 双重确认: 协整破裂
                            health_issues['pair_break'].append(pair_id)
                            continue
                        else:
                            # 噪声突破: 跳过 PAIR_BREAK
                            if self.algorithm.debug_mode:
                                drift_pct = beta_drift * 100 if beta_drift else 0
                                self.algorithm.Debug(
                                    f"[风控-跳过] {pair_id} | "
                                    f"zscore={zscore:+.2f}σ | "
                                    f"β漂移={drift_pct:.1f}% (<{beta_drift_threshold*100:.0f}%) | "
                                    f"判定为噪声"
                                )

            # 优先级3: Timeout (持仓超时)
            max_days = pair.get_max_holding_days()
            holding_days = pair.get_pair_holding_days()
            if max_days is not None and holding_days is not None:
                if holding_days > max_days:
                    health_issues['timeout'].append(pair_id)
                    continue

            # 优先级4: Drawdown (统一阈值)
            drawdown = pair.get_pair_drawdown()
            if drawdown is not None and drawdown > drawdown_threshold:
                health_issues['drawdown'].append(pair_id)

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


    def _calculate_expected_profit(self, pair, actual_allocated: float, data) -> float:
        """
        计算预期收益额 (v8.11.0: 用于开仓排序)

        公式: expected_profit = actual_allocated × (|z| - exit_threshold) / |z|
        含义: 假设完全回归到出场点的预期收益
        """
        prices = pair.get_price_from_bar(data)
        if prices is None:
            return 0.0

        zscore = pair.get_zscore(prices[0], prices[1])
        if zscore is None or abs(zscore) < 0.01:
            return 0.0

        exit_threshold = pair.exit_threshold  # 0.5

        # 预期收益率 = 回归空间 / 当前偏离
        expected_return_pct = (abs(zscore) - exit_threshold) / abs(zscore)
        expected_return_pct = max(0, expected_return_pct)  # 确保非负

        return actual_allocated * expected_return_pct


    def allocate_margin_to_candidates(self, open_candidates: List[tuple]) -> Dict[tuple, float]:
        """
        为开仓候选配对分配保证金 (v8.10.0: 统一15%分配)

        输入: [(pair, signal, planned_pct), ...]  # v8.12.0: 删除 quality_score
        输出: {pair_id: allocated_amount}

        算法:
            1. 获取初始可用保证金 (动态基准)
            2. 计算最小投资门槛 = INITIAL_CAPITAL × 15% (固定地板)
            3. 顺序分配:
               - 计划分配额 = 初始可用 × 15%
               - 实际分配额 = max(计划分配额, 最小门槛)
            4. 检查剩余资金是否足够实际分配额
        """
        allocations = {}
        fixed_pct = self.module_config.fixed_allocation_pct

        # === Step 1: 获取初始可用保证金（动态基准）===
        initial_available = self.get_available_margin()

        # 计算最小投资门槛（固定地板: INITIAL_CAPITAL × 15%）
        min_threshold = self.INITIAL_CAPITAL * fixed_pct

        # 资金充足性检查 (可用资金必须至少能开一仓)
        if initial_available < min_threshold:
            self.algorithm.Debug(
                f"[资金分配] 可用保证金不足: "
                f"${initial_available:,.0f} < 最小门槛${min_threshold:,.0f} "
                f"({fixed_pct*100:.0f}%初始资金)"
            )
            return {}

        # === Step 2: 顺序分配 ===
        remaining_available = initial_available  # 追踪剩余资金

        for pair, signal, planned_pct in open_candidates:
            # 计划分配额 = 初始可用 × 15%
            planned_allocated = initial_available * planned_pct

            # 实际分配额 = max(计划额, 固定地板)
            actual_allocated = max(planned_allocated, min_threshold)

            # 检查剩余资金是否足够
            if remaining_available >= actual_allocated:
                allocations[pair.pair_id] = actual_allocated
                remaining_available -= actual_allocated
            # else: 剩余资金不足，跳过该配对

        # === Step 3: 返回分配结果 ===
        return allocations


    def get_open_candidates_with_allocation(self, data) -> List[tuple]:
        """
        获取开仓候选并完成资金分配 (v8.11.0: 预期收益排序)

        步骤:
            1. 从current_selected筛选有信号+无持仓+不在冷却期的配对
            2. 构建候选列表 (统一使用fixed_allocation_pct)
            3. 调用allocate_margin_to_candidates分配资金
            4. 按预期收益额排序 (可选)
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

        # Step 2: 构建中间列表 (v8.10.0: 统一使用fixed_allocation_pct)
        fixed_pct = self.module_config.fixed_allocation_pct
        open_candidates = []
        for pair in candidates_with_signal:
            signal = pair.get_signal(data)
            open_candidates.append((pair, signal, fixed_pct))  # v8.12.0: 删除 quality_score

        # Step 3: 资金分配
        allocations = self.allocate_margin_to_candidates(open_candidates)

        # Step 4: 按预期收益额排序 (v8.11.0)
        if self.module_config.sort_by_expected_profit:
            # 构建带预期收益的列表
            candidates_with_profit = []
            for pair, signal, planned_pct in open_candidates:
                actual_allocated = allocations.get(pair.pair_id, 0)
                if actual_allocated > 0:
                    expected_profit = self._calculate_expected_profit(pair, actual_allocated, data)
                    candidates_with_profit.append((pair, signal, actual_allocated, expected_profit))

            # 按预期收益额降序排序
            candidates_with_profit.sort(key=lambda x: x[3], reverse=True)

            # 直接构建最终结果
            return [(pair, signal, allocated) for pair, signal, allocated, _ in candidates_with_profit]

        # Step 5: 合并分配结果 (原逻辑，当排序关闭时执行)
        final_candidates = []
        for pair, signal, planned_pct in open_candidates:
            allocated = allocations.get(pair.pair_id)
            if allocated:
                final_candidates.append((pair, signal, allocated))

        return final_candidates


    # ===== 6. 配置查询路由 =====

    def get_cooldown_required_days(self, last_close_reason: str, half_life: Optional[float] = None) -> int:
        """
        计算冷却期天数 (v8.13.0: 基于半衰期动态计算)

        公式: cooldown_days = half_life × multiplier
        """
        # 获取multiplier (默认1.0)
        multiplier = self.module_config.cooldown_multipliers.get(last_close_reason, 1.0)

        # 使用配对半衰期或默认值
        effective_half_life = half_life if half_life is not None else self.module_config.default_half_life

        # 计算并向上取整
        return math.ceil(effective_half_life * multiplier)


