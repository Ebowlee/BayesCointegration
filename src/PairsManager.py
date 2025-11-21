# region imports
from AlgorithmImports import *
from typing import Dict, Set
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
        敞口维度 (v7.59.0, 内部字段用于计算 drift):
            - net_exposure: 净敞口 (long_value - short_value)
            - gross_exposure: 总敞口 (long_value + short_value)

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
                 net_exposure: float = 0.0,
                 gross_exposure: float = 0.0):
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
            net_exposure: 净敞口 (v7.59.0, 内部字段)
            gross_exposure: 总敞口 (v7.59.0, 内部字段)
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
        # 敞口维度 (v7.59.0)
        self.net_exposure = net_exposure
        self.gross_exposure = gross_exposure


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


    # ===== 2. 纯计算层 (Pure Computation) =====
    # 特征: @staticmethod, 无self依赖, 纯函数, 可独立单元测试
    # 注: v7.53.4 - 分类逻辑改用增量集合操作, 纯计算层暂时为空


    # ===== 3. 数据访问层 (Data Access) =====
    # 特征: 读取self属性或外部数据, 委托给纯计算层

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
                - pair.pair_realized_pnl: 已实现盈亏 (已平仓累计)
            投入资本维度:
                - pair.get_pair_current_invested_capital(): 当前投入 (持仓中)
                - pair.pair_past_invested_capital: 历史投入 (已平仓累计)
            交易质量维度:
                - pair.trade_count: 交易次数
                - pair.win_count: 盈利次数
                - pair.pair_past_total_holding_days: 累计持仓天数
            敞口维度 (v7.59.0):
                - pair.get_net_exposure(): 净敞口 (持仓中)
                - pair.get_gross_exposure(): 总敞口 (持仓中)

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
            data.realized_pnl += pair.pair_realized_pnl

            # === 投入资本维度 ===
            current_invested = pair.get_pair_current_invested_capital()
            if current_invested is not None:
                data.current_invested_capital += current_invested
            data.past_invested_capital += pair.pair_past_invested_capital

            # === 交易质量维度 ===
            data.trade_count += pair.trade_count
            data.win_count += pair.win_count
            data.past_total_holding_days += pair.pair_past_total_holding_days

            # === 敞口维度 (v7.59.0) ===
            net_exp = pair.get_net_exposure()
            if net_exp is not None:
                data.net_exposure += net_exp
            gross_exp = pair.get_gross_exposure()
            if gross_exp is not None:
                data.gross_exposure += gross_exp

        return industry_data


    # ===== 4. 业务逻辑层 (Business Logic) =====
    # 特征: 组合数据访问层方法, 包含条件判断, 实现复杂业务逻辑

    # ----- 4A. 配对生命周期管理 -----

    def update_pairs(self, new_pairs_dict: Dict):
        """
        每月选股后更新配对 (v7.53.2: 增量索引更新)

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
        for pid in demoted_ids:
            if self.all_pairs[pid].has_position():
                self.algorithm.Debug(f"[选中变化] {pid} 本轮未被选中但仍有持仓")
        self.past_selected_pair_ids |= demoted_ids
        self.current_selected_pair_ids -= demoted_ids

        # 2b. 加入本轮新选中的 (包括新创建的和从past升级的)
        self.current_selected_pair_ids |= new_pair_ids

        # 输出统计
        self.log_statistics()


    # ===== 5. 外部接口层 (Public API) =====
    # 特征: 对外暴露的核心接口, 整合各层实现完整功能

    # ----- 5A. 配对查询接口 -----

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


    # ----- 5B. 情报中心 (行业统计查询 - v7.58.0 统一聚合) -----
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

    def get_total_pnl(self) -> float:
        """获取全局总盈亏 (所有行业 unrealized + realized)"""
        industry_data = self._aggregate_all_industry_data()
        return sum(d.unrealized_pnl + d.realized_pnl for d in industry_data.values())

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

    def get_total_invested_capital(self) -> float:
        """获取全局总投入资本 (所有行业 current + past)"""
        industry_data = self._aggregate_all_industry_data()
        return sum(d.current_invested_capital + d.past_invested_capital
                   for d in industry_data.values())

    # --- ROI 组 (2个方法) ---

    def get_industry_roi(self, industry_code: str) -> float:
        """
        获取指定行业的ROI (total_pnl / total_invested_capital)

        Returns:
            ROI 百分比 (如 0.15 表示 15%), 无投入资本时返回 0.0
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

    def get_total_roi(self) -> float:
        """
        获取全局ROI (total_pnl / total_invested_capital)

        Returns:
            ROI 百分比 (如 0.15 表示 15%), 无投入资本时返回 0.0
        """
        industry_data = self._aggregate_all_industry_data()
        total_pnl = sum(d.unrealized_pnl + d.realized_pnl for d in industry_data.values())
        total_invested = sum(d.current_invested_capital + d.past_invested_capital
                             for d in industry_data.values())
        if total_invested <= 0:
            return 0.0
        return total_pnl / total_invested

    # --- 交易质量组 (3个方法, v7.57.0) ---

    def get_industry_trade_count(self, industry_code: str) -> int:
        """获取指定行业的交易次数 (已平仓交易计数)"""
        industry_data = self._aggregate_all_industry_data()
        if industry_code in industry_data:
            return industry_data[industry_code].trade_count
        return 0

    def get_industry_win_rate(self, industry_code: str) -> float:
        """
        获取指定行业的胜率 (win_count / trade_count)

        Returns:
            胜率 (如 0.65 表示 65%), 无交易时返回 0.0
        """
        industry_data = self._aggregate_all_industry_data()
        if industry_code not in industry_data:
            return 0.0
        data = industry_data[industry_code]
        if data.trade_count <= 0:
            return 0.0
        return data.win_count / data.trade_count

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

    # --- Drift 组 (2个方法, v7.59.0) ---

    def get_industry_drift(self, industry_code: str) -> float:
        """
        获取指定行业的 Drift (net_exposure / gross_exposure)

        Drift = 净敞口 / 总敞口
            - 0 表示完美对冲
            - 正值表示净多头偏离
            - 负值表示净空头偏离

        Returns:
            Drift 比例 (如 0.05 表示 5% 偏离), 无持仓时返回 0.0
        """
        industry_data = self._aggregate_all_industry_data()
        if industry_code not in industry_data:
            return 0.0
        data = industry_data[industry_code]
        if data.gross_exposure <= 0:
            return 0.0
        return data.net_exposure / data.gross_exposure

    def get_total_drift(self) -> float:
        """
        获取全局 Drift (sum(net_exposure) / sum(gross_exposure))

        Returns:
            Drift 比例 (如 0.05 表示 5% 偏离), 无持仓时返回 0.0
        """
        industry_data = self._aggregate_all_industry_data()
        total_net = sum(d.net_exposure for d in industry_data.values())
        total_gross = sum(d.gross_exposure for d in industry_data.values())
        if total_gross <= 0:
            return 0.0
        return total_net / total_gross


    # ----- 5D. 投资分配中心 - 行业配额管理 (v7.60.0) -----
    # 设计: 综合 ROI × WIN_RATE 复合评分确定配额
    # 复用: 情报中心的 get_industry_roi() 和 get_industry_win_rate()

    def _is_in_warmup_period(self) -> bool:
        """
        检查是否在预热期 (前180天)

        Returns:
            True: 在预热期，使用默认配额
            False: 预热期结束，使用动态配额
        """
        warmup_days = self.config.pairs_manager.warmup_days
        days_running = (self.algorithm.Time - self.algorithm.StartDate).days
        return days_running < warmup_days

    def _calculate_composite_score(self, industry_code: str) -> float:
        """
        计算行业综合得分 = ROI × WIN_RATE

        设计理念:
            - 乘法复合: 自动惩罚低胜率的高收益 (可能是运气)
            - 数学意义: 期望收益 = 每笔收益 × 成功概率

        Args:
            industry_code: 行业代码

        Returns:
            综合得分 (通常在 -0.05 ~ 0.15 范围)

        示例:
            - 行业A: ROI=20%, WIN_RATE=80% → 0.20 × 0.80 = 0.16
            - 行业B: ROI=30%, WIN_RATE=50% → 0.30 × 0.50 = 0.15
            - 行业A 虽然ROI低，但综合得分更高 (更稳定)
        """
        roi = self.get_industry_roi(industry_code)
        win_rate = self.get_industry_win_rate(industry_code)
        return roi * win_rate

    def _get_tier_by_composite_score(self, score: float) -> str:
        """
        根据综合得分确定tier

        Args:
            score: 综合得分 (ROI × WIN_RATE)

        Returns:
            tier名称 ('tier0'/'tier1'/'tier2'/'tier3'/'tier4')

        分层逻辑 (基于 ROI × WIN_RATE 乘积):
            score < 0.00  → tier0 (负收益或亏损)
            score < 0.03  → tier1 (约 6%ROI × 50%胜率)
            score < 0.06  → tier2 (约 10%ROI × 60%胜率)
            score < 0.10  → tier3 (约 15%ROI × 67%胜率)
            score >= 0.10 → tier4 (高ROI + 高胜率)
        """
        thresholds = self.config.pairs_manager.tier_thresholds
        if score < thresholds['tier0']:
            return 'tier0'
        elif score < thresholds['tier1']:
            return 'tier1'
        elif score < thresholds['tier2']:
            return 'tier2'
        elif score < thresholds['tier3']:
            return 'tier3'
        else:
            return 'tier4'

    def _get_quota_by_tier(self, tier: str) -> int:
        """
        根据tier获取配额

        Args:
            tier: tier名称 ('tier0'-'tier4')

        Returns:
            配额数量 (1/2/3/4/5)
        """
        quotas = self.config.pairs_manager.tier_quotas
        return quotas.get(tier, quotas['tier0'])

    def get_industry_quota(self, industry_code: str) -> int:
        """
        获取单个行业的配对配额

        Args:
            industry_code: 行业代码

        Returns:
            配额数量 (1-5)

        逻辑:
            1. 预热期: 返回默认配额 (1)
            2. 正常期: 计算 composite_score → tier → quota
        """
        # 预热期使用默认配额
        if self._is_in_warmup_period():
            return self.config.pairs_manager.default_quota

        # 检查是否有交易历史
        trade_count = self.get_industry_trade_count(industry_code)
        if trade_count == 0:
            return self.config.pairs_manager.default_quota

        # 计算综合得分并获取配额
        score = self._calculate_composite_score(industry_code)
        tier = self._get_tier_by_composite_score(score)
        return self._get_quota_by_tier(tier)

    def get_all_industry_quotas(self) -> Dict[str, Dict]:
        """
        获取所有行业的配额信息 (v7.60.1: 复用 get_industry_quota)

        Returns:
            {industry_code: {'quota': int, 'tier': str, 'composite_score': float}}
            预热期返回空字典 (由调用方使用默认配额)
        """
        # 预热期返回空字典
        if self._is_in_warmup_period():
            warmup_days = self.config.pairs_manager.warmup_days
            days_running = (self.algorithm.Time - self.algorithm.StartDate).days
            self.algorithm.Debug(
                f"[行业配额] 预热期 ({days_running}/{warmup_days}天), "
                f"所有行业使用默认配额: {self.config.pairs_manager.default_quota}"
            )
            return {}

        # 遍历所有行业，复用单个查询方法
        industry_data = self._aggregate_all_industry_data()
        result = {}
        industry_names = self.config.constants['industry_names']
        default_quota = self.config.pairs_manager.default_quota

        for industry_code, data in industry_data.items():
            if data.trade_count == 0:
                continue

            # 复用已有方法 (避免重复计算)
            score = self._calculate_composite_score(industry_code)
            tier = self._get_tier_by_composite_score(score)
            quota = self._get_quota_by_tier(tier)

            result[industry_code] = {
                'quota': quota,
                'tier': tier,
                'composite_score': score
            }

            # 日志输出 (只显示非默认配额)
            if quota != default_quota:
                roi = self.get_industry_roi(industry_code)
                win_rate = self.get_industry_win_rate(industry_code)
                industry_name = industry_names.get(int(industry_code), f'未知({industry_code})')
                self.algorithm.Debug(
                    f"[行业配额] {industry_name}: "
                    f"ROI={roi*100:+.1f}%, 胜率={win_rate*100:.1f}% "
                    f"→ 综合得分={score:.4f} → tier={tier}, 配额={quota}",
                    level=1
                )

        # 汇总日志
        if not any(v['quota'] != default_quota for v in result.values()):
            self.algorithm.Debug(f"[行业配额] 本月所有行业使用默认配额: {default_quota}")

        return result


    # ===== 6. 待重构区域 (Pending Refactor) =====
    # 注: 以下方法未来将迁移到其他模块 (如 ExecutionManager 或独立的 ConfigRouter)

    def get_cooldown_required_days(self, last_close_reason: str) -> int:
        """
        查询冷却期需要天数

        职责: 统一配置查询路由,消除Pairs对全局配置的依赖

        Args:
            last_close_reason: 平仓原因 (MEAN_REVERSION/PAIR_BREAK/TIMEOUT等)

        Returns:
            冷却期天数

        配置来源:
            - NORMAL_SIGNAL: config.constants['close_reasons'][reason]['cooldown_days']
            - 风控规则: config.risk_management.pair_rules[rule_name].cooldown_days
            - 默认兜底: 10天
        """
        close_reasons = self.algorithm.config.constants['close_reasons']

        # NORMAL_SIGNAL: 从CLOSE_REASONS读取
        if last_close_reason in close_reasons:
            reason_config = close_reasons[last_close_reason]
            return reason_config.get('cooldown_days', 10)

        # 风控规则: 从risk_management.pair_rules读取
        risk_config = self.algorithm.config.risk_management.pair_rules
        reason_to_config = {
            'TIMEOUT': risk_config.holding_timeout.cooldown_days,
            'DRAWDOWN': risk_config.pair_drawdown.cooldown_days,
            'CUMULATIVE_LOSS': risk_config.pair_cumulative_loss.cooldown_days,
            'ANOMALY': risk_config.pair_anomaly.cooldown_days,
        }

        return reason_to_config.get(last_close_reason, 10)


    def get_planned_allocation_pct(self, pair) -> float:
        """
        计算配对的计划分配比例 (v7.60.0: 使用 composite_score)

        计算逻辑:
            1. 计算行业 composite_score = ROI × WIN_RATE
            2. 根据 composite_score 确定 tier
            3. planned_pct = min_pct + quality_score × (max_pct - min_pct)

        Args:
            pair: Pairs对象 (提供quality_score和industry_code)

        Returns:
            计划分配比例 (0.05-0.22之间)
        """
        config = self.algorithm.config.pairs_manager
        min_pct = config.min_investment_ratio

        # 查询行业tier (v7.60.0: 使用 composite_score 计算)
        industry_code = str(pair.industry_code)

        # 预热期或无交易历史时使用默认tier
        if self._is_in_warmup_period():
            tier = 'tier0'
        elif self.get_industry_trade_count(industry_code) == 0:
            tier = 'tier0'
        else:
            score = self._calculate_composite_score(industry_code)
            tier = self._get_tier_by_composite_score(score)

        # 获取tier对应的max_pct
        tier_max = config.tier_max_investment_ratio
        max_pct = tier_max.get(tier, tier_max['tier0'])

        return min_pct + pair.quality_score * (max_pct - min_pct)
