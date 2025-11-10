"""
黑名单管理器 (v7.7.0)

设计原则:
- 无状态: 不存储任何配对数据
- 面向对象: 从 Pairs 对象读取交易历史
- 配置化: 判断阈值从 config 读取
- 即时查询: 遍历 PairsManager 中的所有 Pairs 对象

职责:
1. 根据配置判断 Pairs 是否应该黑名单
2. 提供黑名单集合给 PairSelector
3. 提供单个配对的统计信息 (用于诊断日志)
"""

from typing import Set, Tuple, Optional, Dict


class BlacklistManager:
    """
    黑名单管理器

    核心方法:
    - get_blacklist(): 获取所有黑名单配对ID
    - is_blacklisted(): 检查单个配对是否黑名单
    - get_stats(): 获取配对统计信息 (用于诊断)

    工作流程:
    1. PairSelector 调用 get_blacklist()
    2. BlacklistManager 遍历 PairsManager.all_pairs
    3. 对每个 Pairs 对象判断: trade_count >= min_trades AND total_pnl_pct < threshold
    4. 返回满足条件的 pair_id 集合
    """

    def __init__(self, algorithm, config: dict):
        """
        初始化黑名单管理器

        Args:
            algorithm: QCAlgorithm 实例
            config: trade_analysis 配置字典
                - blacklist_min_trades: 最少交易次数 (默认3)
                - blacklist_pnl_threshold: 累计收益阈值 (默认0.0)
        """
        self.algorithm = algorithm
        self.min_trades = config['blacklist_min_trades']
        self.pnl_threshold = config['blacklist_pnl_threshold']

    def get_blacklist(self) -> Set[Tuple[str, str]]:
        """
        获取黑名单集合 (即时计算)

        遍历 PairsManager 中的所有 Pairs 对象,
        判断是否满足黑名单条件:
        - trade_count >= self.min_trades
        - total_pnl_pct < self.pnl_threshold

        Returns:
            Set[Tuple[str, str]]: 黑名单配对ID集合

        Example:
            blacklist = blacklist_manager.get_blacklist()
            # 返回: {('AAPL', 'MSFT'), ('GOOGL', 'META')}
        """
        blacklist = set()

        # 遍历所有配对 (active + legacy + dormant)
        all_pairs = self.algorithm.pairs_manager.all_pairs.values()

        for pair in all_pairs:
            if self._should_blacklist(pair):
                blacklist.add(pair.pair_id)

        return blacklist

    def is_blacklisted(self, pair_id: Tuple[str, str]) -> bool:
        """
        检查单个配对是否在黑名单

        Args:
            pair_id: 配对ID元组 (symbol1, symbol2)

        Returns:
            bool: True if 黑名单, False otherwise

        Note:
            性能优化: 直接查询 Pairs 对象,避免生成整个黑名单集合
        """
        pair = self.algorithm.pairs_manager.get_pair_by_id(pair_id)
        if not pair:
            return False

        return self._should_blacklist(pair)

    def get_stats(self, pair_id: Tuple[str, str]) -> Optional[Dict]:
        """
        获取配对统计信息 (用于诊断日志)

        Args:
            pair_id: 配对ID

        Returns:
            Dict: 包含 count, wins, total_pnl (v7.7.1: 加权平均收益率)
            None: 配对不存在

        Example:
            stats = blacklist_manager.get_stats(('AAPL', 'MSFT'))
            # 返回: {'count': 5, 'wins': 2, 'total_pnl': -8.5}

        v7.7.1 修正:
        - 旧: 返回 pair.total_pnl_pct (简单相加的错误结果)
        - 新: 计算加权平均收益率 (total_pnl_dollars / total_pair_cost)
        """
        pair = self.algorithm.pairs_manager.get_pair_by_id(pair_id)
        if not pair:
            return None

        # 计算加权平均累计收益率 (v7.7.1)
        if pair.total_pair_cost > 0:
            total_return_pct = (pair.total_pnl_dollars / pair.total_pair_cost) * 100
        else:
            total_return_pct = 0.0  # 无交易历史,返回0

        return {
            'count': pair.trade_count,
            'wins': pair.win_count,
            'total_pnl': total_return_pct
        }

    def _should_blacklist(self, pair) -> bool:
        """
        判断 Pairs 对象是否应该黑名单

        黑名单条件 (AND关系):
        1. 交易次数 >= min_trades (确保统计意义)
        2. 累计收益率 < pnl_threshold (累计亏损)

        Args:
            pair: Pairs 对象

        Returns:
            bool: True if 应该黑名单

        v7.7.1 修正:
        - 旧: 使用 pair.total_pnl_pct (简单相加的错误结果)
        - 新: 计算加权平均收益率 (total_pnl_dollars / total_pair_cost)
        """
        # 检查交易次数门槛
        if pair.trade_count < self.min_trades:
            return False

        # 计算加权平均累计收益率 (v7.7.1)
        if pair.total_pair_cost > 0:
            cumulative_return_pct = (pair.total_pnl_dollars / pair.total_pair_cost) * 100
        else:
            return False  # 无有效成本数据,不黑名单

        # 判断是否累计亏损
        return cumulative_return_pct < self.pnl_threshold
