# region imports
from AlgorithmImports import *
from typing import Dict, Set
# endregion


class PairState:
    """
    配对状态常量与分类逻辑

    三分类原则(基于本轮协整检验结果):
        - COINTEGRATED: 本轮通过协整检验的配对(current cointegrated)
        - LEGACY: 本轮未通过协整检验,但仍有持仓(failed current test, has position)
        - ARCHIVED: 本轮未通过协整检验,且无持仓(failed current test, no position)

    设计说明:
        - 使用类常量而非 Enum: 保持与其他常量类(TradingSignal, PositionMode)一致
        - 字符串值保持简短: 便于日志输出和调试
        - 合并分类逻辑: 状态定义和分类规则内聚在同一个类中
    """
    # 状态常量(简短值,便于日志输出)
    COINTEGRATED = 'cointegrated'   # 本轮通过协整检验(current cointegrated pairs)
    LEGACY = 'legacy'               # 本轮未通过协整检验,但仍有持仓(failed current test, has position)
    ARCHIVED = 'archived'           # 本轮未通过协整检验,且无持仓(failed current test, no position)

    @staticmethod
    def classify(pair_id: tuple, pair, current_pair_ids: Set) -> str:
        """
        配对分类逻辑

        分类规则:
            - COINTEGRATED: 本轮通过协整检验(在 current_pair_ids 中)
            - LEGACY: 本轮未通过协整检验但仍有持仓(需要继续管理风险)
            - ARCHIVED: 本轮未通过协整检验且无持仓(已归档,不参与交易)

        Args:
            pair_id: 配对ID元组 (symbol1, symbol2)
            pair: Pairs 对象
            current_pair_ids: 本轮建模成功的配对ID集合

        Returns:
            str: PairState.COINTEGRATED | PairState.LEGACY | PairState.ARCHIVED

        Example:
            >>> PairState.classify(('AAPL', 'MSFT'), pair, {('AAPL', 'MSFT')})
            'cointegrated'
        """
        if pair_id in current_pair_ids:
            return PairState.COINTEGRATED
        elif pair.has_position():
            return PairState.LEGACY
        else:
            return PairState.ARCHIVED


class PairsManager:
    """管理整个回测周期内所有配对的生命周期"""

    # ===== 1. 初始化 =====

    def __init__(self, algorithm, config):
        """
        初始化配对管理器
        Args:
            algorithm: QCAlgorithm实例
            config: 风控配置字典
        """
        self.algorithm = algorithm
        self.config = config  

        # 主存储:所有曾经出现过的配对
        self.all_pairs = {}  # {pair_id: Pairs对象}

        # 分类索引(只存储pair_id) - 命名与PairState常量保持一致
        self.cointegrated_ids = set()  # 对应 PairState.COINTEGRATED
        self.legacy_ids = set()         # 对应 PairState.LEGACY
        self.archived_ids = set()       # 对应 PairState.ARCHIVED

        # 统计信息
        self.update_count = 0  # 更新次数(选股轮次)
        self.last_update_time = None  # 上次更新时间


    # ===== 2. 核心管理 =====

    @property
    def tradeable_ids(self) -> Set:
        """
        可交易配对ID集合 (协整配对 + 遗留配对)

        设计说明:
            - 使用 @property 而非方法: 语义上这是"属性查询"而非"操作"
            - DRY原则: 消除代码中的重复集合运算
            - 可读性: self.tradeable_ids 比集合运算更清晰
            - 可维护性: 将来修改"可交易"定义只需改这一处

        包含范围:
            - COINTEGRATED: 本轮通过协整检验的配对
            - LEGACY: 未通过检验但有持仓的配对
            - 排除 ARCHIVED: 未通过检验且无持仓的配对

        Returns:
            Set[tuple]: 可交易配对的 pair_id 集合
        """
        return self.cointegrated_ids | self.legacy_ids


    def update_pairs(self, new_pairs_dict: Dict):
        """
        每月选股后更新配对
        new_pairs_dict: {pair_id: Pairs对象} 外部创建的新配对字典
        """
        self.update_count += 1
        self.last_update_time = self.algorithm.Time

        # 记录本轮出现的配对
        current_pair_ids = set(new_pairs_dict.keys())

        # 第一步:处理本轮出现的配对
        for pair_id, new_pair in new_pairs_dict.items():
            if pair_id in self.all_pairs:
                # 已存在的配对:调用 update_params 并检查返回值
                old_pair = self.all_pairs[pair_id]
                old_pair.update_params(new_pair)
            else:
                # 新配对:直接添加
                self.all_pairs[pair_id] = new_pair

        # 第一点五步:协整复查预警（方案A - 监控失去协整性但仍有持仓的配对）
        for pair_id in self.cointegrated_ids:  # 上一轮是cointegrated
            if pair_id not in current_pair_ids:  # 本轮未通过协整检验
                pair = self.all_pairs[pair_id]
                if pair.has_position():
                    self.algorithm.Debug(f"[协整复查] {pair_id} 失去协整性但仍有持仓")

        # 第二步:重新分类所有配对
        self.reclassify_pairs(current_pair_ids)

        # 输出统计
        self.log_statistics()


    def reclassify_pairs(self, current_pair_ids: Set):
        """
        重新分类所有配对

        Args:
            current_pair_ids: 本轮建模成功的pair_id集合(已通过协整检验、质量筛选和贝叶斯建模)

        设计说明:
            - 使用 PairState.classify() 封装分类逻辑
            - 状态定义和分类规则内聚在 PairState 类中
            - 易于扩展: 将来添加新状态只需修改 PairState.classify()
        """
        # 清空分类
        self.cointegrated_ids.clear()
        self.legacy_ids.clear()
        self.archived_ids.clear()

        # 使用 PairState.classify() 重新分类所有配对
        for pair_id, pair in self.all_pairs.items():
            category = PairState.classify(pair_id, pair, current_pair_ids)

            if category == PairState.COINTEGRATED:
                self.cointegrated_ids.add(pair_id)
            elif category == PairState.LEGACY:
                self.legacy_ids.add(pair_id)
            else:
                self.archived_ids.add(pair_id)


    # ===== 3. 查询接口 =====

    def has_tradeable_pairs(self) -> bool:
        """检查是否有可交易的配对"""
        return len(self.tradeable_ids) > 0


    def get_tradeable_pairs(self) -> Dict:
        """
        获取所有可交易的配对字典

        Returns:
            Dict[tuple, Pairs]: {pair_id: Pairs对象} 字典

        包含范围:
            - COINTEGRATED: 本轮通过协整检验的配对
            - LEGACY: 历史配对但仍有持仓的配对
        """
        return {pair_id: self.all_pairs[pair_id] for pair_id in self.tradeable_ids}


    def get_pairs_with_position(self) -> Dict:
        """
        获取所有有持仓的可交易配对
        返回: {pair_id: Pairs对象} 字典

        优化: 直接遍历,避免构建中间字典
        """
        result = {}
        for pair_id in self.tradeable_ids:
            pair = self.all_pairs[pair_id]
            if pair.has_position():
                result[pair_id] = pair
        return result


    def get_pairs_without_position(self) -> Dict:
        """
        获取所有无持仓的COINTEGRATED配对(用于开仓逻辑)

        返回: {pair_id: Pairs对象} 字典

        v7.30.1优化:
        - 只遍历cointegrated_ids (本轮通过协整检验)
        - LEGACY配对定义保证has_position()==True,不会出现在此列表
        - 语义清晰: 只有本轮协整通过的配对才能开新仓
        """
        result = {}
        for pair_id in self.cointegrated_ids:
            pair = self.all_pairs[pair_id]
            if not pair.has_position():
                result[pair_id] = pair
        return result


    def get_pair_by_id(self, pair_id):
        """
        通过pair_id获取Pairs对象
        Args:
            pair_id: 配对ID元组 (symbol1, symbol2)
        Returns:
            Pairs对象 或 None
        """
        return self.all_pairs.get(pair_id)


    # ===== 4. 日志与统计 =====

    def get_statistics(self) -> Dict:
        """
        获取配对管理统计信息

        设计说明:
            - 数据结构化: 返回字典而非打印字符串
            - 接口隔离: 统计数据可被其他模块复用(如监控、分析、测试)
            - 可测试性: 便于单元测试验证统计值

        使用场景:
            - 日志输出: log_statistics() 调用此方法
            - 监控面板: 实时显示配对状态统计
            - 风控分析: 检查配对数量分布是否异常
            - 单元测试: 验证分类逻辑正确性
        """
        return {
            'update_count': self.update_count,
            'cointegrated_count': len(self.cointegrated_ids),
            'legacy_count': len(self.legacy_ids),
            'archived_count': len(self.archived_ids),
            'total_count': len(self.all_pairs),
            'last_update_time': self.last_update_time
        }

    def log_statistics(self):
        """输出统计信息 - 使用 get_statistics()"""
        pass


    # ===== 5. 行业情报站 (v7.40.0) =====

    def get_industry_stats(self):
        """
        行业情报站 - 聚合行业级统计数据 (v7.40.0)

        职责:
            1. 按行业分组聚合配对数据
            2. 计算7项核心指标 (持仓数, 净敞口, 漂移, 保证金, 浮盈, 累积收益, 胜率)
            3. 过滤无意义行业 (无持仓且无历史交易)
            4. 查询行业名称 (从config.constants['industry_names'])
            5. 创建 IndustryStats 对象并返回 IndustryStatsCollection

        使用场景:
            - 日志输出: 输出行业级监控数据
            - 风险监控: 检查行业集中度和对冲失衡
            - 行业配额调整: 基于行业收益率动态调整配额

        Returns:
            IndustryStatsCollection: 行业统计集合容器

        Example:
            >>> industry_stats = pairs_manager.get_industry_stats()
            >>> for industry_code, stats in industry_stats.items():
            >>>     self.Debug(stats.to_log_string())

            输出示例:
            [行业统计] 半导体(31130): 持仓3对 | 净敞口$-5,200 | 漂移-2.1% |
                       保证金$12,000 | 浮动盈亏$+850 | 累积收益+12.3% | 胜率66.7%
        """
        from src.industry import IndustryStats, IndustryStatsCollection
        from collections import defaultdict

        # === 步骤1: 按行业分组聚合数据 ===
        industry_data = defaultdict(lambda: {
            'position_count': 0,
            'net_exposure': 0.0,
            'gross_exposure': 0.0,
            'margin_used': 0.0,
            'unrealized_pnl': 0.0,
            'realized_pnl': 0.0,
            'realized_cost': 0.0,
            'total_trades': 0,
            'winning_trades': 0
        })

        # 遍历所有配对聚合数据
        for _, pair in self.all_pairs.items():
            industry_code = str(pair.industry_code)
            data = industry_data[industry_code]

            # 统计1-2: 持仓数和净敞口 (方案C: 只在内层检查)
            net_exp = pair.get_net_exposure() 
            if net_exp is not None:
                data['position_count'] += 1  # 从净敞口返回值推导持仓数
                data['net_exposure'] += net_exp

            # 统计3: 总敞口
            gross_exp = pair.get_gross_exposure()  
            if gross_exp is not None:
                data['gross_exposure'] += gross_exp

            # 统计4-5: 保证金和浮盈
            margin = pair.get_pair_cost()
            if margin is not None:
                data['margin_used'] += margin

            pnl = pair.get_pair_unrealized_pnl()
            if pnl is not None:
                data['unrealized_pnl'] += pnl

            # 统计6-7: 已实现盈亏和成本 (所有配对都累加历史数据)
            data['realized_pnl'] += pair.total_pnl_dollars
            data['realized_cost'] += pair.total_pair_cost

            # 统计8-9: 交易次数和盈利次数
            data['total_trades'] += pair.trade_count
            data['winning_trades'] += pair.win_count

        # === 步骤2: 过滤空行业 (无持仓且无历史交易) ===
        filtered_data = {
            industry_code: data
            for industry_code, data in industry_data.items()
            if data['position_count'] > 0 or data['total_trades'] > 0
        }

        # === 步骤3: 查询行业名称 (factory责任) ===
        industry_names = self.algorithm.config.constants['industry_names']

        # === 步骤4: 创建 IndustryStats 对象 ===
        stats_dict = {}
        for industry_code, data in filtered_data.items():
            # 查询行业名称 (如果映射缺失则显示"未知")
            industry_name = industry_names.get(int(industry_code), f'未知{industry_code}')

            # 创建 IndustryStats 对象
            stats_dict[industry_code] = IndustryStats(
                industry_code=industry_code,
                industry_name=industry_name,
                position_count=data['position_count'],
                net_exposure=data['net_exposure'],
                gross_exposure=data['gross_exposure'],
                margin_used=data['margin_used'],
                unrealized_pnl=data['unrealized_pnl'],
                realized_pnl=data['realized_pnl'],
                realized_cost=data['realized_cost'],
                total_trades=data['total_trades'],
                winning_trades=data['winning_trades']
            )

        # === 步骤5: 返回 IndustryStatsCollection ===
        return IndustryStatsCollection(stats_dict)