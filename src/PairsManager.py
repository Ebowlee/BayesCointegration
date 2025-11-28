# region imports
from AlgorithmImports import *
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple
# endregion


class IndustryData:
    """
    行业数据对象 - Value Object (v7.56.0)

    设计原则:
        - 纯数据对象: 存储单个行业的聚合统计
        - 渐进式扩展: 从单字段开始,逐步添加更多字段
        - 外部类: 与 PairsManager 同级,便于测试和访问

    职责:
        - 存储行业级聚合数据
        - 提供类型安全的属性访问

    字段说明 (对称设计):
        PnL维度:
            - unrealized_pnl: 未实现盈亏 (持仓中配对的浮动盈亏)
            - realized_pnl: 已实现盈亏 (已平仓交易的累计盈亏)
        投入资本维度:
            - current_invested_capital: 当前投入资本 (持仓中配对的投入)
            - past_invested_capital: 历史投入资本 (已平仓交易的累计投入)
        交易质量维度 (v7.57.0):
            - trade_count: 交易次数 (已平仓交易计数)
            - win_count: 盈利次数 (pnl > 0 的交易计数)
            - past_total_holding_days: 累计持仓天数 (已平仓交易)
        滚动窗口维度 (v8.0.0):
            - trade_history: 单笔交易记录列表 [(exit_time, pnl, invested_capital), ...]

    使用场景:
        - 由 PairsManager._aggregate_*() 方法创建
        - 供查询接口 get_industry_*() / get_total_*() 返回数据
    """

    def __init__(self, industry_code: str,
                 unrealized_pnl: float = 0.0,
                 realized_pnl: float = 0.0,
                 current_invested_capital: float = 0.0,
                 past_invested_capital: float = 0.0,
                 trade_count: int = 0,
                 win_count: int = 0,
                 past_total_holding_days: float = 0.0,
                 trade_history: List[Tuple[datetime, float, float]] = None):
        """
        初始化行业数据对象

        Args:
            industry_code: 行业代码 (字符串格式)
            unrealized_pnl: 未实现盈亏 (持仓中)
            realized_pnl: 已实现盈亏 (已平仓累计)
            current_invested_capital: 当前投入资本 (持仓中)
            past_invested_capital: 历史投入资本 (已平仓累计)
            trade_count: 交易次数 (已平仓交易计数, v7.57.0)
            win_count: 盈利次数 (pnl > 0, v7.57.0)
            past_total_holding_days: 累计持仓天数 (已平仓交易, v7.57.0)
            trade_history: 单笔交易记录列表 (v8.0.0 滚动窗口)
        """
        self.industry_code = industry_code
        self.unrealized_pnl = unrealized_pnl
        self.realized_pnl = realized_pnl
        self.current_invested_capital = current_invested_capital
        self.past_invested_capital = past_invested_capital
        # 交易质量维度 (v7.57.0)
        self.trade_count = trade_count
        self.win_count = win_count
        self.past_total_holding_days = past_total_holding_days
        # 滚动窗口维度 (v8.0.0)
        self.trade_history: List[Tuple[datetime, float, float]] = trade_history if trade_history is not None else []


class PairsManager:
    """
    配对管理器 - 管理整个回测周期内所有配对的生命周期

    核心职责:
        1. 配对生命周期管理 (创建、更新、分类)
        2. 配对查询接口 (按状态、按持仓)
        3. 情报中心 (行业级数据聚合与查询)
        4. 配置查询路由 (冷却期、分配比例)

    架构设计 (v7.53.4 经典5层):
        - 1. 初始化层: __init__
        - 2. 纯计算层: (保留位置, 目前为空)
        - 3. 数据访问层: 读取数据, 委托纯计算
        - 4. 业务逻辑层: 组合数据访问, 条件判断
        - 5. 外部接口层: 对外暴露的核心接口

    情报中心设计:
        - industry_stats: 类似Excel工作簿,每个行业是一个工作表
        - 外部接口: 行业级查询 + 全局聚合查询
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
        """
        通过pair_id获取Pairs对象

        Args:
            pair_id: 配对ID元组 (symbol1, symbol2)

        Returns:
            Pairs对象 或 None
        """
        return self.all_pairs.get(pair_id)


    def _aggregate_all_industry_data(self) -> Dict[str, IndustryData]:
        """
        一次遍历聚合所有行业数据 (v7.59.0 敞口扩展)

        设计理念:
            - 将独立聚合方法合并为1个,避免重复遍历 all_pairs
            - 一次遍历填充 IndustryData 的所有10个字段

        数据源:
            PnL维度:
                - pair.get_pair_unrealized_pnl(): 未实现盈亏 (持仓中)
                - pair.pair_accum_realized_pnl: 累积已实现盈亏 (已平仓累计, v8.0.5)
            投入资本维度:
                - pair.get_pair_current_invested_capital(): 当前投入 (持仓中)
                - pair.pair_past_invested_capital: 历史投入 (已平仓累计)
            交易质量维度:
                - pair.trade_count: 交易次数
                - pair.win_count: 盈利次数
                - pair.pair_past_total_holding_days: 累计持仓天数

        Returns:
            Dict[str, IndustryData]: 行业代码 → IndustryData 对象 (完整填充)

        Example:
            >>> data = self._aggregate_all_industry_data()
            >>> data['31169001'].unrealized_pnl      # 软件行业浮盈
            >>> data['31169001'].win_count           # 软件行业盈利次数
        """
        industry_data: Dict[str, IndustryData] = {}

        for _, pair in self.all_pairs.items():
            industry_code = str(pair.industry_code)

            # 懒创建 IndustryData 对象
            if industry_code not in industry_data:
                industry_data[industry_code] = IndustryData(industry_code)

            data = industry_data[industry_code]

            # === PnL维度 ===
            unrealized_pnl = pair.get_pair_unrealized_pnl()
            if unrealized_pnl is not None:
                data.unrealized_pnl += unrealized_pnl
            data.realized_pnl += pair.pair_accum_realized_pnl  # v8.0.5: 更新属性名

            # === 投入资本维度 ===
            current_invested = pair.get_pair_current_invested_capital()
            if current_invested is not None:
                data.current_invested_capital += current_invested
            data.past_invested_capital += pair.pair_past_invested_capital

            # === 交易质量维度 ===
            data.trade_count += pair.trade_count
            data.win_count += pair.win_count
            data.past_total_holding_days += pair.pair_past_total_holding_days

            # === 滚动窗口维度 (v8.0.0) ===
            data.trade_history.extend(pair.trade_history)

        return industry_data


    # ===== 3. 业务逻辑层 (Business Logic) =====
    # 特征: 组合数据访问层方法, 包含条件判断, 实现复杂业务逻辑

    # ----- 3A. 配对生命周期管理 -----

    def classify_pairs(self, new_pairs_dict: Dict):
        """
        每月选股后分类管理配对 (v7.53.2: 增量索引更新, v7.73.0: 重命名)

        Args:
            new_pairs_dict: {pair_id: Pairs对象} 外部创建的新配对字典

        设计原则:
            - 增量更新: 使用集合操作替代全量重建
            - 单一职责: 持仓检查放在外部, update_params只负责更新参数
        """
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
        """
        获取所有有持仓的配对 (v7.55.0 简化)

        Returns:
            Dict[tuple, Pairs]: {pair_id: Pairs对象}
        """
        return {
            pair_id: pair
            for pair_id, pair in self.all_pairs.items()
            if pair.has_position()
        }


    # ----- 4B. 情报中心 (行业统计查询 - v7.58.0 统一聚合) -----
    # 设计: 五组对称结构 (PnL组 / 投入资本组 / ROI组 / 交易质量组 / Drift组)
    # 优化: 所有查询统一调用 _aggregate_all_industry_data(), 一次遍历

    # --- PnL 组 (4个方法) ---

    def get_industry_unrealized_pnl(self, industry_code: str) -> float:
        """获取指定行业的未实现盈亏 (持仓中浮盈)"""
        industry_data = self._aggregate_all_industry_data()
        if industry_code in industry_data:
            return industry_data[industry_code].unrealized_pnl
        return 0.0

    def get_industry_realized_pnl(self, industry_code: str) -> float:
        """获取指定行业的已实现盈亏 (已平仓累计)"""
        industry_data = self._aggregate_all_industry_data()
        if industry_code in industry_data:
            return industry_data[industry_code].realized_pnl
        return 0.0

    def get_industry_total_pnl(self, industry_code: str) -> float:
        """获取指定行业的总盈亏 (unrealized + realized)"""
        industry_data = self._aggregate_all_industry_data()
        if industry_code not in industry_data:
            return 0.0
        data = industry_data[industry_code]
        return data.unrealized_pnl + data.realized_pnl

    # --- 投入资本组 (4个方法) ---

    def get_industry_current_invested_capital(self, industry_code: str) -> float:
        """获取指定行业的当前投入资本 (持仓中)"""
        industry_data = self._aggregate_all_industry_data()
        if industry_code in industry_data:
            return industry_data[industry_code].current_invested_capital
        return 0.0

    def get_industry_past_invested_capital(self, industry_code: str) -> float:
        """获取指定行业的历史投入资本 (已平仓累计)"""
        industry_data = self._aggregate_all_industry_data()
        if industry_code in industry_data:
            return industry_data[industry_code].past_invested_capital
        return 0.0

    def get_industry_total_invested_capital(self, industry_code: str) -> float:
        """获取指定行业的总投入资本 (current + past)"""
        industry_data = self._aggregate_all_industry_data()
        if industry_code not in industry_data:
            return 0.0
        data = industry_data[industry_code]
        return data.current_invested_capital + data.past_invested_capital


    # --- ROI 组 (4个方法, v7.92.0 扩展) ---

    def get_industry_total_roi(self, industry_code: str) -> float:
        """
        获取指定行业的总ROI (历史+当前混合) (v7.92.0 重命名)

        公式: (realized_pnl + unrealized_pnl) / (past_invested + current_invested)

        Returns:
            总ROI (如 0.15 表示 15%), 无投入资本时返回 0.0
        """
        industry_data = self._aggregate_all_industry_data()
        if industry_code not in industry_data:
            return 0.0
        data = industry_data[industry_code]
        total_pnl = data.unrealized_pnl + data.realized_pnl
        total_invested = data.current_invested_capital + data.past_invested_capital
        if total_invested <= 0:
            return 0.0
        return total_pnl / total_invested

    def get_industry_realized_roi(self, industry_code: str,
                                   window_days: Optional[int] = None) -> float:
        """
        获取指定行业的已实现ROI (v8.0.0: 支持滚动窗口, v8.0.1: 配置化参数)

        公式: sum(pnl) / sum(invested_capital)

        参数:
            industry_code: 行业代码
            window_days: 滚动窗口天数
                - None: 使用 config.industry_quota.rolling_window_days (默认180天)
                - -1: 使用累计值 (向后兼容)
                - 正整数: 使用指定窗口天数

        滚动窗口逻辑:
            1. 筛选 exit_time >= (当前时间 - window_days) 的交易
            2. 样本量保底: 如果窗口内 < min_samples 笔, 取最近 min_samples 笔
            3. 计算 sum(pnl) / sum(invested_capital)

        Returns:
            已实现ROI (如 0.15 表示 15%), 无数据时返回 0.0
        """
        # === v8.0.1: 从config读取默认值 ===
        if window_days is None:
            window_days = self.config.industry_quota.rolling_window_days
        min_samples = self.config.industry_quota.min_samples_for_window

        industry_data = self._aggregate_all_industry_data()
        if industry_code not in industry_data:
            return 0.0

        data = industry_data[industry_code]

        # === 向后兼容: window_days=-1 时使用累计值 ===
        if window_days == -1:
            if data.past_invested_capital <= 0:
                return 0.0
            return data.realized_pnl / data.past_invested_capital

        # === 滚动窗口计算 ===
        cutoff_time = self.algorithm.Time - timedelta(days=window_days)
        window_records = [r for r in data.trade_history if r[0] >= cutoff_time]

        # 样本量保底: 窗口内不足 min_samples 时, 取最近 min_samples 笔
        if len(window_records) < min_samples:
            window_records = data.trade_history[-min_samples:]

        if not window_records:
            return 0.0

        total_pnl = sum(r[1] for r in window_records)
        total_capital = sum(r[2] for r in window_records)

        if total_capital <= 0:
            return 0.0

        return total_pnl / total_capital

    def get_industry_unrealized_roi(self, industry_code: str) -> float:
        """
        获取指定行业的未实现ROI (当前持仓) (v7.92.0 新增)

        公式: unrealized_pnl / current_invested_capital

        用途:
            - 实时监控当前持仓的浮盈/浮亏比例

        Returns:
            未实现ROI (如 -0.05 表示 -5%), 无当前持仓时返回 0.0
        """
        industry_data = self._aggregate_all_industry_data()
        if industry_code not in industry_data:
            return 0.0
        data = industry_data[industry_code]
        if data.current_invested_capital <= 0:
            return 0.0
        return data.unrealized_pnl / data.current_invested_capital


    # --- 交易质量组 (3个方法, v7.57.0) ---

    def get_industry_trade_count(self, industry_code: str) -> int:
        """获取指定行业的交易次数 (已平仓交易计数)"""
        industry_data = self._aggregate_all_industry_data()
        if industry_code in industry_data:
            return industry_data[industry_code].trade_count
        return 0

    def get_industry_win_rate(self, industry_code: str,
                               window_days: Optional[int] = None) -> float:
        """
        获取指定行业的胜率 (v8.0.0: 支持滚动窗口, v8.0.1: 配置化参数)

        参数:
            industry_code: 行业代码
            window_days: 滚动窗口天数
                - None: 使用 config.industry_quota.rolling_window_days (默认180天)
                - -1: 使用累计值 (向后兼容)
                - 正整数: 使用指定窗口天数

        滚动窗口逻辑:
            1. 筛选 exit_time >= (当前时间 - window_days) 的交易
            2. 样本量保底: 如果窗口内 < min_samples 笔, 取最近 min_samples 笔
            3. 计算 wins / len(window_records)

        Returns:
            胜率 (如 0.65 表示 65%), 无交易时返回 0.0
        """
        # === v8.0.1: 从config读取默认值 ===
        if window_days is None:
            window_days = self.config.industry_quota.rolling_window_days
        min_samples = self.config.industry_quota.min_samples_for_window

        industry_data = self._aggregate_all_industry_data()
        if industry_code not in industry_data:
            return 0.0

        data = industry_data[industry_code]

        # === 向后兼容: window_days=-1 时使用累计值 ===
        if window_days == -1:
            if data.trade_count <= 0:
                return 0.0
            return data.win_count / data.trade_count

        # === 滚动窗口计算 ===
        cutoff_time = self.algorithm.Time - timedelta(days=window_days)
        window_records = [r for r in data.trade_history if r[0] >= cutoff_time]

        # 样本量保底: 窗口内不足 min_samples 时, 取最近 min_samples 笔
        if len(window_records) < min_samples:
            window_records = data.trade_history[-min_samples:]

        if not window_records:
            return 0.0

        # 计算胜率: pnl > 0 即为盈利
        wins = sum(1 for r in window_records if r[1] > 0)
        return wins / len(window_records)

    def get_industry_avg_holding_days(self, industry_code: str) -> float:
        """
        获取指定行业的平均持仓天数 (past_total_holding_days / trade_count)

        Returns:
            平均持仓天数, 无交易时返回 0.0
        """
        industry_data = self._aggregate_all_industry_data()
        if industry_code not in industry_data:
            return 0.0
        data = industry_data[industry_code]
        if data.trade_count <= 0:
            return 0.0
        return data.past_total_holding_days / data.trade_count
    
    def get_industry_composite_score(self, industry_code: str) -> float:
        """
        计算行业综合得分 = realized_roi × win_rate (v7.92.0 修正)

        设计理念:
            - 时间口径一致: 两者都是纯历史数据 (已平仓交易)
            - 乘法复合: 自动惩罚低胜率的高收益 (可能是运气)
            - 数学意义: 期望收益 = 每笔收益 × 成功概率

        Args:
            industry_code: 行业代码

        Returns:
            综合得分 (通常在 -0.05 ~ 0.15 范围)

        示例:
            - 行业A: realized_roi=20%, win_rate=80% → 0.20 × 0.80 = 0.16
            - 行业B: realized_roi=30%, win_rate=50% → 0.30 × 0.50 = 0.15
            - 行业A 虽然ROI低，但综合得分更高 (更稳定)
        """
        realized_roi = self.get_industry_realized_roi(industry_code)
        win_rate = self.get_industry_win_rate(industry_code)
        return realized_roi * win_rate
  

    # --- Concentration 组 (v7.91.0) ---

    def get_industry_concentration(self, industry_code: str) -> float:
        """
        获取行业集中度 (v7.91.0)

        公式:
            concentration = industry_current_invested / total_current_invested

        含义:
            - 0.25 表示该行业占用了 25% 的当前资金
            - 用于开仓阶段检查 (后续版本)

        Returns:
            集中度比例 (0.0 ~ 1.0), 无持仓时返回 0.0
        """
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
        配对层面健康检查 (v7.90.0 更新)

        检查维度 (按优先级排序):
            1. Anomaly: 单边或同向持仓异常
            2. Drawdown: 配对回撤超过阈值 (v7.86.0)
            3. Drift: 对冲漂移超过阈值 (v7.87.0)
            4. Timeout: 持仓超时 (v7.87.1)

        Returns:
            Dict[str, List[str]]: {'anomaly': [...], 'drawdown': [...], 'drift': [...], 'timeout': [...]}
            - 每个配对只返回最高优先级问题
            - 便于 main.py 按类型批量处理

        使用示例:
            health_issues = pairs_manager.check_pairs_health()
            for pair_id in health_issues.get('anomaly', []):
                pair = pairs_manager.get_pair_by_id(pair_id)
                intent = pair.get_close_intent(reason='ANOMALY')
                # ... 执行平仓
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
        """
        检查行业集中度 (v7.94.0: 重命名自 check_industry_health)

        检测单行业资金占用超过阈值的行业。

        Returns:
            List[str]: 超过集中度阈值的行业代码列表
        """
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
        """
        获取当前可用保证金（v7.62.1: 使用固定 buffer）

        公式:
            available = Portfolio.MarginRemaining - FIXED_BUFFER
            FIXED_BUFFER = INITIAL_CAPITAL × (1 - margin_usage_ratio)

        设计要点:
            - FIXED_BUFFER 在 __init__ 时计算，整个回测周期不变
            - 用于预留交易手续费，防止 margin call

        Returns:
            可用保证金（美元），最小为 0

        示例:
            初始: INITIAL_CAPITAL=$100k, margin_usage_ratio=98%
            → FIXED_BUFFER = $2k（固定）

            盈利20%后: MarginRemaining=$120k
            → available = $120k - $2k = $118k（buffer 仍为 $2k）

            亏损20%后: MarginRemaining=$80k
            → available = $80k - $2k = $78k（buffer 仍为 $2k）
        """
        # 直接使用初始化时计算的固定 buffer
        current_margin = self.algorithm.Portfolio.MarginRemaining
        available = current_margin - self.FIXED_BUFFER  # 使用固定值

        return max(0, available)


    def allocate_margin_to_candidates(self, open_candidates: List[tuple]) -> Dict[tuple, float]:
        """
        为开仓候选配对分配保证金（v7.99.3: 重命名 entry → open）

        Args:
            open_candidates: [(pair, signal, quality_score, planned_pct), ...]
                - pair: Pairs 对象
                - signal: TradingSignal（LONG_SPREAD/SHORT_SPREAD）
                - quality_score: 配对质量分数（0-1）
                - planned_pct: 计划分配比例（由 get_planned_allocation_pct 计算）

        Returns:
            Dict[pair_id, allocated_amount]
            {
                ('AAPL', 'MSFT'): 25000.0,
                ('GOOG', 'GOOGL'): 20000.0,
                ...
            }

        核心算法（v7.62.2 极简比例分配）:
            1. 获取初始可用保证金（固定基准，只调用一次）
            2. 计算最小投资门槛（基于固定的 INITIAL_CAPITAL）
            3. 顺序分配（按质量分数降序遍历）:
               a. 分配额 = 初始可用 × 计划比例（所有配对使用相同基准）
               b. 如果 ≥ 门槛 且 剩余资金充足: 执行分配并扣减追踪器
               c. 否则: 跳过该配对
            4. 返回分配结果

        设计理念:
            - 比例分配天然具备动态缩放: 盈利放大，亏损收缩
            - 固定基准确保公平: 所有候选配对基于同一个 initial_available
            - 固定门槛提供保护: 极端亏损时自动过滤小额分配
            - 无需模式判断: 数学自动处理，代码简洁优雅

        数学示例:
            初始: INITIAL_CAPITAL=$100k, 盈利到$200k
            initial_available = $196k (假设buffer=$4k)
            min_threshold = $100k × 5% = $5k (固定)

            配对A: planned_pct=15% → $196k × 15% = $29.4k ✅
            配对B: planned_pct=12% → $196k × 12% = $23.5k ✅
            (两个配对都基于$196k计算，公平分配)
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
        """
        计算配对的计划分配比例 (v7.99.3: 基于平均交易回报的层级分配)

        逻辑:
            1. trade_count=0 → 返回默认分配 (allocation_default)
            2. 根据 avg_return_per_trade 查找匹配层级
            3. 超过所有层级 → 返回最大分配 (allocation_max)

        Args:
            pair: Pairs对象 (调用 get_avg_return_per_trade())

        Returns:
            计划分配比例 (小数形式, 如 0.10 = 10%)
        """
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
        获取开仓候选并完成资金分配 (v7.99.3: 重命名 entry → open)

        核心理念:
            只有本轮通过协整+质量筛选的配对才配得上开仓交易
            past_selected已失去协整关系,只能被动平仓,不能主动开仓

        Returns:
            [(pair, signal, allocated_margin), ...]
            按质量分数降序排列,已完成资金分配

        步骤:
            1. 从current_selected中筛选有信号+无持仓的配对
            2. 按质量分数降序排序
            3. 计算planned_pct并构建中间列表
            4. 调用allocate_margin_to_candidates进行资金分配
            5. 合并分配结果,返回最终列表
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
        """
        查询冷却期天数 (v7.97.0: 统一从 cooldown_days Dict 读取)

        Args:
            last_close_reason: 平仓原因 (MEAN_REVERSION/PAIR_BREAK/TIMEOUT等)

        Returns:
            冷却期天数，默认10天
        """
        return self.module_config.cooldown_days.get(last_close_reason, 10)


