# region imports
from AlgorithmImports import *
from typing import Dict, Set
# endregion


class IndustryData:
    """
    行业数据对象 - Value Object (v7.54.0)

    设计原则:
        - 纯数据对象: 存储单个行业的聚合统计
        - 渐进式扩展: 从单字段开始,逐步添加更多字段
        - 外部类: 与 PairsManager 同级,便于测试和访问

    职责:
        - 存储行业级聚合数据
        - 提供类型安全的属性访问

    字段说明:
        - unrealized_pnl: 未实现盈亏 (所有持仓配对的浮动盈亏之和)
        - realized_pnl: 已实现盈亏 (所有已平仓交易的累计盈亏)

    使用场景:
        - 由 PairsManager._aggregate_unrealized_pnl() 创建 (未实现盈亏)
        - 由 PairsManager._aggregate_realized_pnl() 创建 (已实现盈亏)
        - 供查询接口 get_industry_*_pnl() / get_total_*_pnl() 返回数据
    """

    def __init__(self, industry_code: str, unrealized_pnl: float = 0.0, realized_pnl: float = 0.0):
        """
        初始化行业数据对象

        Args:
            industry_code: 行业代码 (字符串格式)
            unrealized_pnl: 未实现盈亏 (所有持仓配对的浮动盈亏之和)
            realized_pnl: 已实现盈亏 (所有已平仓交易的累计盈亏)
        """
        self.industry_code = industry_code
        self.unrealized_pnl = unrealized_pnl
        self.realized_pnl = realized_pnl


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
        self.current_selected_pair_ids = set()              # 本轮被PairSelector选中
        self.past_selected_pair_ids = set()                 # 历史配对 (曾被选中,本轮未选中)

        # === 统计信息 ===
        self.update_count = 0                                   # 更新次数(选股轮次)
        self.last_update_time = None                            # 上次更新时间


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


    def _aggregate_unrealized_pnl(self) -> Dict[str, IndustryData]:
        """
        聚合未实现盈亏 (专用聚合方法 - v7.52.1)

        职责:
            - 遍历所有配对,按行业分组聚合 unrealized_pnl
            - 返回 IndustryData 对象 (类型安全)

        流程:
            1. 遍历 all_pairs
            2. 按 industry_code 分组
            3. 累加每个配对的 get_pair_unrealized_pnl()
            4. 返回 {industry_code: IndustryData} 字典

        Returns:
            Dict[str, IndustryData]: 行业代码 → IndustryData 对象

        Example:
            >>> data = self._aggregate_unrealized_pnl()
            >>> data['31169001'].unrealized_pnl  # 软件行业浮盈
        """
        industry_data: Dict[str, IndustryData] = {}

        for _, pair in self.all_pairs.items():
            industry_code = str(pair.industry_code)

            # 懒创建 IndustryData 对象
            if industry_code not in industry_data:
                industry_data[industry_code] = IndustryData(industry_code)

            # 聚合未实现盈亏
            unrealized_pnl = pair.get_pair_unrealized_pnl()
            if unrealized_pnl is not None:
                industry_data[industry_code].unrealized_pnl += unrealized_pnl

        return industry_data


    def _aggregate_realized_pnl(self) -> Dict[str, IndustryData]:
        """
        聚合已实现盈亏 (专用聚合方法 - v7.54.0)

        职责:
            - 遍历所有配对,按行业分组聚合 realized_pnl
            - 返回 IndustryData 对象 (类型安全)

        流程:
            1. 遍历 all_pairs
            2. 按 industry_code 分组
            3. 累加每个配对的 get_pair_realized_pnl()
            4. 返回 {industry_code: IndustryData} 字典

        Returns:
            Dict[str, IndustryData]: 行业代码 → IndustryData 对象

        Example:
            >>> data = self._aggregate_realized_pnl()
            >>> data['31169001'].realized_pnl  # 软件行业已实现盈亏
        """
        industry_data: Dict[str, IndustryData] = {}

        for _, pair in self.all_pairs.items():
            industry_code = str(pair.industry_code)

            # 懒创建 IndustryData 对象
            if industry_code not in industry_data:
                industry_data[industry_code] = IndustryData(industry_code)

            # 聚合已实现盈亏
            realized_pnl = pair.get_pair_realized_pnl()
            industry_data[industry_code].realized_pnl += realized_pnl

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

    @property
    def tradeable_ids(self) -> Set:
        """
        可交易配对ID集合 (v7.53.0 简化版)

        可交易 = 本轮选中 + 历史配对中有持仓的

        设计说明:
            - 本轮选中: 全部可交易
            - 历史配对: 只有仍有持仓的才可交易 (需要平仓)
            - 动态计算: 每次调用时检查 has_position()

        Returns:
            Set[tuple]: 可交易配对的 pair_id 集合
        """
        # 历史配对中有持仓的 (需要平仓管理)
        past_with_position = {
            pid for pid in self.past_selected_pair_ids
            if self.all_pairs[pid].has_position()
        }
        return self.current_selected_pair_ids | past_with_position


    def get_pairs_with_position(self) -> Dict:
        """
        获取所有有持仓的可交易配对

        Returns:
            Dict[tuple, Pairs]: {pair_id: Pairs对象}
        """
        result = {}
        for pair_id in self.tradeable_ids:
            pair = self.all_pairs[pair_id]
            if pair.has_position():
                result[pair_id] = pair
        return result


    # ----- 5B. 情报中心 (行业统计查询) -----

    def get_industry_unrealized_pnl(self, industry_code: str) -> float:
        """
        获取指定行业的未实现盈亏 (行业级查询 - v7.52.1)

        Args:
            industry_code: 行业代码 (字符串格式)

        Returns:
            该行业所有持仓配对的未实现盈亏之和

        数据流:
            Pairs.get_pair_unrealized_pnl()
                ↓
            _aggregate_unrealized_pnl() → Dict[str, IndustryData]
                ↓
            get_industry_unrealized_pnl() → float (单行业)

        Example:
            >>> pnl = pairs_manager.get_industry_unrealized_pnl('31169001')
            >>> print(f"软件行业浮盈: ${pnl:,.2f}")
        """
        industry_data = self._aggregate_unrealized_pnl()
        if industry_code in industry_data:
            return industry_data[industry_code].unrealized_pnl
        return 0.0


    def get_total_unrealized_pnl(self) -> float:
        """
        获取全局未实现盈亏 (跨行业聚合 - v7.52.1)

        Returns:
            所有行业未实现盈亏之和

        数据流:
            Pairs.get_pair_unrealized_pnl()
                ↓
            _aggregate_unrealized_pnl() → Dict[str, IndustryData]
                ↓
            get_total_unrealized_pnl() → float (全局汇总)

        Example:
            >>> total_pnl = pairs_manager.get_total_unrealized_pnl()
            >>> print(f"全局浮盈: ${total_pnl:,.2f}")
        """
        industry_data = self._aggregate_unrealized_pnl()
        return sum(data.unrealized_pnl for data in industry_data.values())


    def get_top_pairs_by_unrealized_pnl(self, n: int = 5, ascending: bool = False) -> list:
        """
        获取未实现盈亏排名前N的配对 (通用查询 - v7.52.2)

        Args:
            n: 返回数量 (默认5)
            ascending: True=从小到大(亏损最多), False=从大到小(盈利最多)

        Returns:
            List[Tuple[pair_id, unrealized_pnl]]: [(pair_id, unrealized_pnl), ...]

        Example:
            >>> top5 = pairs_manager.get_top_pairs_by_unrealized_pnl(n=5)
            >>> top10_loss = pairs_manager.get_top_pairs_by_unrealized_pnl(n=10, ascending=True)
        """
        pairs_unrealized_pnl = []
        for pair_id, pair in self.all_pairs.items():
            unrealized_pnl = pair.get_pair_unrealized_pnl()
            if unrealized_pnl is not None:
                pairs_unrealized_pnl.append((pair_id, unrealized_pnl))

        pairs_unrealized_pnl.sort(key=lambda x: x[1], reverse=not ascending)
        return pairs_unrealized_pnl[:n]


    def get_industry_realized_pnl(self, industry_code: str) -> float:
        """
        获取指定行业的已实现盈亏 (行业级查询 - v7.54.0)

        Args:
            industry_code: 行业代码 (字符串格式)

        Returns:
            该行业所有已平仓交易的累计盈亏之和

        数据流:
            Pairs.get_pair_realized_pnl()
                ↓
            _aggregate_realized_pnl() → Dict[str, IndustryData]
                ↓
            get_industry_realized_pnl() → float (单行业)

        Example:
            >>> pnl = pairs_manager.get_industry_realized_pnl('31169001')
            >>> print(f"软件行业已实现盈亏: ${pnl:,.2f}")
        """
        industry_data = self._aggregate_realized_pnl()
        if industry_code in industry_data:
            return industry_data[industry_code].realized_pnl
        return 0.0


    def get_total_realized_pnl(self) -> float:
        """
        获取全局已实现盈亏 (跨行业聚合 - v7.54.0)

        Returns:
            所有行业已实现盈亏之和

        数据流:
            Pairs.get_pair_realized_pnl()
                ↓
            _aggregate_realized_pnl() → Dict[str, IndustryData]
                ↓
            get_total_realized_pnl() → float (全局汇总)

        Example:
            >>> total_pnl = pairs_manager.get_total_realized_pnl()
            >>> print(f"全局已实现盈亏: ${total_pnl:,.2f}")
        """
        industry_data = self._aggregate_realized_pnl()
        return sum(data.realized_pnl for data in industry_data.values())


    # ----- 5C. 诊断统计 -----

    def get_statistics(self) -> Dict:
        """
        获取配对管理统计信息 (v7.53.0 简化为两分类)

        Returns:
            Dict: 包含update_count, current_selected_count等字段

        使用场景:
            - 日志输出
            - 监控面板
            - 单元测试
        """
        # 计算历史配对中有持仓的数量
        past_with_position_count = sum(
            1 for pid in self.past_selected_pair_ids
            if self.all_pairs[pid].has_position()
        )

        return {
            'update_count': self.update_count,
            'current_selected_count': len(self.current_selected_pair_ids),
            'past_selected_count': len(self.past_selected_pair_ids),
            'past_with_position_count': past_with_position_count,
            'total_count': len(self.all_pairs),
            'tradeable_count': len(self.tradeable_ids),
            'last_update_time': self.last_update_time
        }


    def log_statistics(self):
        """输出统计信息 - 使用 get_statistics()"""
        pass


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
        计算配对的计划分配比例

        计算逻辑:
            planned_pct = min_pct + quality_score × (max_pct - min_pct)

        Args:
            pair: Pairs对象 (提供quality_score和industry_code)

        Returns:
            计划分配比例 (0.05-0.22之间)
        """
        config = self.algorithm.config.pairs_trading
        min_pct = config.min_investment_ratio

        # 查询行业tier
        tier = self._get_industry_tier(pair.industry_code)

        # 获取tier对应的max_pct
        tier_max = config.tier_max_investment_ratio
        max_pct = tier_max.get(tier, tier_max['tier1'])

        return min_pct + pair.quality_score * (max_pct - min_pct)
