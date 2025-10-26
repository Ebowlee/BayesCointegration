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
                if old_pair.update_params(new_pair):
                    # 更新成功:输出确认日志(含beta值)
                    self.algorithm.Debug(
                        f"[PairsManager] 更新配对 {pair_id} "
                        f"(beta: {old_pair.beta_mean:.3f})"
                    )
                # 更新失败(有持仓):Pairs内部已输出冻结日志,这里不重复
            else:
                # 新配对:直接添加
                self.all_pairs[pair_id] = new_pair
                self.algorithm.Debug(f"[PairsManager] 添加新配对 {pair_id}")

        # 第一点五步:协整复查预警（方案A - 监控失去协整性但仍有持仓的配对）
        for pair_id in self.cointegrated_ids:  # 上一轮是cointegrated
            if pair_id not in current_pair_ids:  # 本轮未通过协整检验
                pair = self.all_pairs[pair_id]
                if pair.has_position():
                    holding_days = pair.get_pair_holding_days()
                    self.algorithm.Debug(
                        f"[协整复查] {pair_id} 失去协整性但仍有持仓 "
                        f"(持仓{holding_days}天,进入增强监控)"
                    )

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
        获取所有无持仓的可交易配对(用于开仓逻辑)
        返回: {pair_id: Pairs对象} 字典

        优化: 直接遍历,避免构建中间字典
        """
        result = {}
        for pair_id in self.tradeable_ids:
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
        stats = self.get_statistics()
        self.algorithm.Debug(
            f"[PairsManager] 第{stats['update_count']}轮更新完成: "
            f"协整={stats['cointegrated_count']}, "
            f"遗留={stats['legacy_count']}, "
            f"归档={stats['archived_count']}, "
            f"总计={stats['total_count']}"
        )