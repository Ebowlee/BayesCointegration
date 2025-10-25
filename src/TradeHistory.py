# region imports
from AlgorithmImports import *
from dataclasses import dataclass
from datetime import datetime
from typing import List, Dict, Optional
import pandas as pd
import numpy as np
# endregion


@dataclass(frozen=True)
class TradeSnapshot:
    """
    交易快照（值对象）- Value Object

    设计原则：
    1. 不可变：创建后不能修改（frozen=True）
    2. 自包含：包含重现交易的所有信息
    3. 轻量级：仅存储数据，不包含业务逻辑
    4. 职责分离：配对身份（隐式提取）vs 交易数据（显式传入）vs 衍生数据（懒计算）

    用途：
    - 记录每一笔配对交易的完整信息
    - 支持后续统计分析和绩效评估
    - 提供交易历史的可追溯性
    """

    # ===== 配对身份（隐式提取，不变的）=====
    pair_id: tuple                  # 配对ID (symbol1, symbol2)
    industry_group: str             # 子行业分组（IndustryGroupCode）
    beta: float                     # Beta系数（协整系数）
    quality_score: float            # 配对质量分数

    # ===== 交易数据（显式传入，会变化的）=====
    entry_price1: float             # symbol1 开仓价格
    entry_price2: float             # symbol2 开仓价格
    exit_price1: float              # symbol1 平仓价格
    exit_price2: float              # symbol2 平仓价格

    # 数量信息（保持原始符号：多头为正，空头为负）
    qty1: int                       # symbol1 持仓数量（带符号）
    qty2: int                       # symbol2 持仓数量（带符号）

    open_time: datetime             # 开仓时间
    close_time: datetime            # 平仓时间

    close_reason: str               # 平仓原因（'CLOSE', 'STOP_LOSS', 'TIMEOUT', 'RISK_TRIGGER'）
    direction: str                  # 交易方向（'LONG_SPREAD' 或 'SHORT_SPREAD'）

    pair_cost: float                # 配对保证金成本 (Regulation T margin: 0.5 for long, 1.5 for short)
    pair_pnl: float                 # 配对盈亏 (使用 exit 价格计算)


    # ===== 衍生数据（懒计算，从已有字段推导）=====

    @property
    def symbol1(self) -> str:
        """提取 symbol1"""
        return self.pair_id[0]

    @property
    def symbol2(self) -> str:
        """提取 symbol2"""
        return self.pair_id[1]

    @property
    def holding_days(self) -> int:
        """持仓天数"""
        return (self.close_time - self.open_time).days

    @property
    def return_pct(self) -> float:
        """收益率（%）= pair_pnl / pair_cost * 100"""
        if self.pair_cost <= 0:
            return 0.0
        return (self.pair_pnl / self.pair_cost) * 100


    # ===== 工厂方法（便捷创建） =====

    @classmethod
    def from_pair(cls, pair, close_reason: str,
                  entry_price1: float, entry_price2: float,
                  exit_price1: float, exit_price2: float,
                  qty1: int, qty2: int,
                  open_time, close_time,
                  pair_cost: float, pair_pnl: float):
        """
        从 Pairs 对象创建 TradeSnapshot（工厂方法）

        设计理念（职责分离）：
        - 交易数据（会变化的）→ 显式传入（11个字段：9个原始 + 2个计算）
        - 配对身份（不变的）→ 从 pair 对象隐式提取（4个字段）
        - 计算数据（显式传入）→ pair_cost, pair_pnl（Pairs已计算好）
        - 懒计算（从已有字段推导）→ symbol1, symbol2, holding_days, return_pct

        Args:
            pair: Pairs 对象（包含配对身份信息）
            close_reason: 平仓原因（'CLOSE', 'STOP_LOSS', 'TIMEOUT', 'RISK_TRIGGER'）
            entry_price1: symbol1 开仓价格
            entry_price2: symbol2 开仓价格
            exit_price1: symbol1 平仓价格
            exit_price2: symbol2 平仓价格
            qty1: symbol1 持仓数量（保持原始符号：多头为正，空头为负）
            qty2: symbol2 持仓数量（保持原始符号：多头为正，空头为负）
            open_time: 配对开仓时间
            close_time: 配对平仓时间
            pair_cost: 配对保证金成本（调用 pair.get_pair_cost() 计算）
            pair_pnl: 配对盈亏（使用 exit 价格计算）

        Returns:
            TradeSnapshot: 不可变的交易快照对象

        Example:
            # 在 Pairs.on_position_filled(CLOSE) 中调用
            pair_cost = self.get_pair_cost()
            exit_value = self.tracked_qty1 * exit_price1 + self.tracked_qty2 * exit_price2
            entry_value = self.tracked_qty1 * self.entry_price1 + self.tracked_qty2 * self.entry_price2
            pair_pnl = exit_value - entry_value

            snapshot = TradeSnapshot.from_pair(
                pair=self,
                close_reason=close_reason,
                entry_price1=self.entry_price1,
                entry_price2=self.entry_price2,
                exit_price1=exit_price1,
                exit_price2=exit_price2,
                qty1=self.tracked_qty1,  # 保持原始符号
                qty2=self.tracked_qty2,  # 保持原始符号
                open_time=self.pair_opened_time,
                close_time=self.pair_closed_time,
                pair_cost=pair_cost,
                pair_pnl=pair_pnl
            )
        """
        # 判断交易方向（基于 qty1 的符号）
        direction = 'LONG_SPREAD' if qty1 > 0 else 'SHORT_SPREAD'

        return cls(
            # 隐式提取的配对身份信息（4个字段）
            pair_id=pair.pair_id,
            industry_group=pair.industry_group,
            beta=pair.beta_mean,
            quality_score=pair.quality_score,

            # 显式传入的交易数据（12个字段：9个原始 + 3个计算）
            close_reason=close_reason,
            direction=direction,  # 交易方向（从qty1推导）
            entry_price1=entry_price1,
            entry_price2=entry_price2,
            exit_price1=exit_price1,
            exit_price2=exit_price2,
            qty1=qty1,        # 保持原始符号（多头为正，空头为负）
            qty2=qty2,        # 保持原始符号（多头为正，空头为负）
            open_time=open_time,
            close_time=close_time,
            pair_cost=pair_cost,  # 显式传入（Pairs已计算）
            pair_pnl=pair_pnl     # 显式传入（Pairs已计算）
        )


class TradeJournal:
    """
    交易日志（聚合根）- Aggregate Root

    设计原则：
    1. 单一入口：所有历史记录操作必须通过 TradeJournal
    2. 封装复杂性：外部不需要知道内部存储和索引细节
    3. 职责单一：只负责存储和查询，不负责分析

    存储结构：
    - _snapshots: 按时间顺序存储（支持时间范围查询）
    - _by_pair: 按配对索引存储（支持快速配对查询）

    用途：
    - 记录每笔交易的快照
    - 提供多维度查询接口
    - 导出数据供分析使用
    """

    def __init__(self, algorithm):
        """
        初始化交易日志

        Args:
            algorithm: QCAlgorithm 实例（用于 Debug 日志）
        """
        self.algorithm = algorithm

        # 时间序列存储（按平仓时间顺序）
        self._snapshots: List[TradeSnapshot] = []

        # 配对索引（快速查找某配对的所有交易）
        # 结构：{('AAPL', 'MSFT'): [snapshot1, snapshot2, ...]}
        self._by_pair: Dict[tuple, List[TradeSnapshot]] = {}


    def record(self, snapshot: TradeSnapshot):
        """
        记录一笔交易快照

        设计要点：
        - 同时更新时间序列和配对索引（保持同步）
        - 自动维护索引，外部无需关心存储细节

        Args:
            snapshot: TradeSnapshot 对象

        Example:
            # 在 main.py 平仓后调用
            snapshot = TradeSnapshot.from_pair(pair, 'CLOSE', price1, price2, pnl)
            self.trade_journal.record(snapshot)
        """
        # 添加到时间序列
        self._snapshots.append(snapshot)

        # 更新配对索引
        if snapshot.pair_id not in self._by_pair:
            self._by_pair[snapshot.pair_id] = []
        self._by_pair[snapshot.pair_id].append(snapshot)

        # Debug 日志
        self.algorithm.Debug(
            f"[交易记录] {snapshot.pair_id} {snapshot.direction} "
            f"PnL=${snapshot.pair_pnl:.2f} ({snapshot.return_pct:.2f}%) "
            f"原因={snapshot.close_reason}"
        )


    def get_all(self) -> List[TradeSnapshot]:
        """
        获取所有交易记录（按时间顺序）

        返回副本的原因：
        - 防止外部修改内部列表结构
        - 保护封装性

        Returns:
            List[TradeSnapshot]: 所有交易快照的副本（按平仓时间顺序）

        复杂度：O(n) - n 为交易总数
        """
        return self._snapshots.copy()


    def get_by_pair(self, pair_id: tuple) -> List[TradeSnapshot]:
        """
        获取某配对的所有交易记录

        Args:
            pair_id: 配对ID，例如 ('AAPL', 'MSFT')

        Returns:
            List[TradeSnapshot]: 该配对的所有交易快照（按时间顺序）
            如果配对不存在，返回空列表

        复杂度：O(1) 查找 + O(m) 复制 - m 为该配对的交易数

        Example:
            aapl_msft_trades = trade_journal.get_by_pair(('AAPL', 'MSFT'))
            for trade in aapl_msft_trades:
                print(f"PnL: {trade.pnl}, Return: {trade.return_pct}%")
        """
        return self._by_pair.get(pair_id, []).copy()


    def count(self) -> int:
        """
        获取交易总数

        Returns:
            int: 历史交易总笔数
        """
        return len(self._snapshots)


    def count_by_pair(self, pair_id: tuple) -> int:
        """
        获取某配对的交易次数

        Args:
            pair_id: 配对ID

        Returns:
            int: 该配对的历史交易次数
        """
        return len(self._by_pair.get(pair_id, []))


    def get_by_time_range(self, start_time: datetime, end_time: datetime) -> List[TradeSnapshot]:
        """
        获取指定时间范围内的交易记录

        设计要点：
        - 利用 _snapshots 时间序列特性
        - 按 close_time（平仓时间）过滤

        Args:
            start_time: 起始时间（包含）
            end_time: 结束时间（包含）

        Returns:
            List[TradeSnapshot]: 时间范围内的所有交易快照

        复杂度：O(n) - n 为交易总数

        Example:
            # 查询 2024 年 Q1 的所有交易
            q1_trades = trade_journal.get_by_time_range(
                datetime(2024, 1, 1),
                datetime(2024, 3, 31)
            )
        """
        return [
            snapshot for snapshot in self._snapshots
            if start_time <= snapshot.close_time <= end_time
        ]


    def query(self,
              pair_id: Optional[tuple] = None,
              industry_group: Optional[str] = None,
              direction: Optional[str] = None,
              close_reason: Optional[str] = None,
              min_pnl: Optional[float] = None,
              max_pnl: Optional[float] = None,
              min_return_pct: Optional[float] = None,
              max_return_pct: Optional[float] = None,
              min_holding_days: Optional[int] = None,
              max_holding_days: Optional[int] = None) -> List[TradeSnapshot]:
        """
        灵活的多条件查询

        设计要点：
        - 所有条件都是可选的（默认 None = 不过滤）
        - 多个条件之间是 AND 关系（必须同时满足）
        - 只遍历一次数据，高效

        Args:
            pair_id: 配对ID过滤
            industry_group: 行业过滤
            direction: 交易方向过滤（'LONG_SPREAD' 或 'SHORT_SPREAD'）
            close_reason: 平仓原因过滤
            min_pnl: 最小盈亏
            max_pnl: 最大盈亏
            min_return_pct: 最小收益率（%）
            max_return_pct: 最大收益率（%）
            min_holding_days: 最小持仓天数
            max_holding_days: 最大持仓天数

        Returns:
            List[TradeSnapshot]: 满足所有条件的交易快照

        复杂度：O(n) - n 为交易总数

        Example:
            # 查找所有止损平仓的亏损交易
            stop_loss_trades = trade_journal.query(
                close_reason='STOP_LOSS',
                max_pnl=0
            )

            # 查找科技行业持仓超过 20 天的盈利交易
            tech_long_winners = trade_journal.query(
                industry_group='Technology',
                min_holding_days=20,
                min_pnl=0
            )
        """
        results = []

        for snapshot in self._snapshots:
            # 逐条检查所有条件
            if pair_id is not None and snapshot.pair_id != pair_id:
                continue
            if industry_group is not None and snapshot.industry_group != industry_group:
                continue
            if direction is not None and snapshot.direction != direction:
                continue
            if close_reason is not None and snapshot.close_reason != close_reason:
                continue
            if min_pnl is not None and snapshot.pair_pnl < min_pnl:
                continue
            if max_pnl is not None and snapshot.pair_pnl > max_pnl:
                continue
            if min_return_pct is not None and snapshot.return_pct < min_return_pct:
                continue
            if max_return_pct is not None and snapshot.return_pct > max_return_pct:
                continue
            if min_holding_days is not None and snapshot.holding_days < min_holding_days:
                continue
            if max_holding_days is not None and snapshot.holding_days > max_holding_days:
                continue

            # 通过所有条件，加入结果
            results.append(snapshot)

        return results


    def to_dataframe(self):
        """
        导出所有交易记录为 pandas DataFrame

        设计要点：
        - 每个 TradeSnapshot 字段成为 DataFrame 的一列
        - 包含计算型属性（symbol1, symbol2, entry_cost, pnl, holding_days, return_pct）
        - 数量字段提供双重视图：qty_signed（原始符号）和 qty_abs（绝对值）
        - 支持后续数据分析和可视化

        Returns:
            pd.DataFrame: 包含所有交易记录的数据框
            核心列：
            - 配对身份：pair_id, symbol1, symbol2, industry_group, beta, quality_score
            - 时间信息：open_time, close_time, holding_days
            - 交易方向：direction, close_reason
            - 价格信息：entry_price1, entry_price2, exit_price1, exit_price2
            - 数量信息（双重视图）：
              * qty1_signed, qty2_signed（原始符号：多头为正，空头为负）
              * qty1_abs, qty2_abs（绝对值：便于可读性）
            - 衍生数据：pair_cost（显式字段）, pair_pnl（显式字段）, return_pct（懒计算）

        复杂度：O(n) - n 为交易总数

        Example:
            # 导出数据并分析
            df = trade_journal.to_dataframe()

            # 使用带符号数量计算 PnL（技术分析）
            df['pnl_check'] = df['qty1_signed'] * (df['exit_price1'] - df['entry_price1']) + \
                              df['qty2_signed'] * (df['exit_price2'] - df['entry_price2'])

            # 使用绝对值数量统计交易规模（业务可读性）
            df['total_shares'] = df['qty1_abs'] + df['qty2_abs']

            # 按行业分组统计
            industry_stats = df.groupby('industry_group')['pnl'].agg(['sum', 'mean', 'count'])

            # 保存为 CSV
            df.to_csv('trade_history.csv', index=False)
        """
        if not self._snapshots:
            # 空日志，返回空 DataFrame
            return pd.DataFrame()

        # 将所有快照转为字典列表
        records = []
        for snapshot in self._snapshots:
            records.append({
                # 配对身份
                'pair_id': snapshot.pair_id,
                'symbol1': snapshot.symbol1,
                'symbol2': snapshot.symbol2,
                'industry_group': snapshot.industry_group,
                'beta': snapshot.beta,
                'quality_score': snapshot.quality_score,

                # 时间信息
                'open_time': snapshot.open_time,
                'close_time': snapshot.close_time,
                'holding_days': snapshot.holding_days,

                # 交易方向
                'direction': snapshot.direction,
                'close_reason': snapshot.close_reason,

                # 价格信息
                'entry_price1': snapshot.entry_price1,
                'entry_price2': snapshot.entry_price2,
                'exit_price1': snapshot.exit_price1,
                'exit_price2': snapshot.exit_price2,

                # 数量信息（双重视图）
                'qty1_signed': snapshot.qty1,      # 原始符号（技术分析）
                'qty2_signed': snapshot.qty2,
                'qty1_abs': abs(snapshot.qty1),    # 绝对值（业务可读性）
                'qty2_abs': abs(snapshot.qty2),

                # 衍生数据（显式字段 + 懒计算）
                'pair_cost': snapshot.pair_cost,    # 显式字段
                'pair_pnl': snapshot.pair_pnl,      # 显式字段
                'return_pct': snapshot.return_pct   # 懒计算
            })

        return pd.DataFrame(records)


class TradeAnalyzer:
    """
    交易分析器（领域服务）- Domain Service

    设计原则：
    1. 无状态：所有方法都是静态方法，不依赖实例变量
    2. 纯函数：相同输入 → 相同输出，无副作用
    3. 专注配对维度：只计算 QC 不提供的配对交易特有统计

    职责：
    - 配对维度分析（每个配对的详细统计）
    - 行业维度分析（各行业表现对比）
    - 平仓原因维度分析（诊断退出策略）
    - 交易级 SPY 对比（验证策略 alpha）

    不重复 QC 已有指标：
    - ✗ 整体夏普比率、信息比率（QC 已提供）
    - ✗ 整体 Alpha、Beta（QC 已提供）
    - ✗ 整体胜率、总交易次数（QC 已提供）
    """

    @staticmethod
    def analyze_pair(snapshots: List[TradeSnapshot]) -> Dict:
        """
        单配对统计分析

        Args:
            snapshots: 某个配对的所有交易快照列表

        Returns:
            Dict: 配对统计指标
            {
                'total_trades': int,        # 交易次数
                'win_rate': float,          # 胜率（0-1）
                'avg_return_pct': float,    # 平均收益率（%）
                'std_return_pct': float,    # 收益率标准差（%）
                'max_win': float,           # 最大单笔盈利（$）
                'max_loss': float,          # 最大单笔亏损（$）
                'avg_holding_days': float,  # 平均持仓天数
                'total_pnl': float          # 总盈亏（$）
            }

        Example:
            aapl_msft_trades = trade_journal.get_by_pair(('AAPL', 'MSFT'))
            stats = TradeAnalyzer.analyze_pair(aapl_msft_trades)
            print(f"胜率: {stats['win_rate']:.1%}")
        """
        if not snapshots:
            return {
                'total_trades': 0,
                'win_rate': 0,
                'avg_return_pct': 0,
                'std_return_pct': 0,
                'max_win': 0,
                'max_loss': 0,
                'avg_holding_days': 0,
                'total_pnl': 0
            }

        total_trades = len(snapshots)
        wins = [s for s in snapshots if s.pair_pnl > 0]
        win_rate = len(wins) / total_trades

        returns = [s.return_pct for s in snapshots]
        avg_return_pct = np.mean(returns)
        std_return_pct = np.std(returns, ddof=1) if total_trades > 1 else 0

        pnls = [s.pair_pnl for s in snapshots]
        max_win = max(pnls)
        max_loss = min(pnls)

        holding_days = [s.holding_days for s in snapshots]
        avg_holding_days = np.mean(holding_days)

        total_pnl = sum(pnls)

        return {
            'total_trades': total_trades,
            'win_rate': win_rate,
            'avg_return_pct': avg_return_pct,
            'std_return_pct': std_return_pct,
            'max_win': max_win,
            'max_loss': max_loss,
            'avg_holding_days': avg_holding_days,
            'total_pnl': total_pnl
        }


    @staticmethod
    def analyze_global(snapshots: List[TradeSnapshot], spy_prices: Optional[Dict] = None) -> Dict:
        """
        整体统计分析（多维度）

        提供 QC 不提供的配对交易特有统计：
        - 配对维度：每个配对的详细表现
        - 行业维度：各行业的表现对比
        - 平仓原因维度：不同退出方式的效果
        - 交易级 SPY 对比：验证策略是否创造 alpha

        Args:
            snapshots: 所有交易快照列表
            spy_prices: 预加载的 SPY 价格字典 {date: close_price}
                       如果提供，计算交易级 SPY 对比指标
                       如果为 None，跳过 SPY 对比

        Returns:
            Dict: 多维度统计结果
            {
                'summary': {...},              # 基础摘要
                'by_pair': {...},              # 配对维度
                'top_pairs': [...],            # 最佳配对（按总盈亏）
                'worst_pairs': [...],          # 最差配对（按总盈亏）
                'by_industry': {...},          # 行业维度
                'by_close_reason': {...},      # 平仓原因维度
                'benchmark_comparison': {...}  # SPY 对比（如果提供 spy_prices）
            }

        Example:
            # 在 OnEndOfAlgorithm 中
            spy_history = self.History(self.market_benchmark, self.StartDate, self.EndDate, Resolution.Daily)
            if isinstance(spy_history.index, pd.MultiIndex):
                spy_history = spy_history.droplevel(0)
            spy_prices = {index.date(): row['close'] for index, row in spy_history.iterrows()}

            stats = TradeAnalyzer.analyze_global(
                self.trade_journal.get_all(),
                spy_prices=spy_prices
            )

            self.Debug(f"最佳配对: {stats['top_pairs'][0]}")
            self.Debug(f"科技行业胜率: {stats['by_industry']['Technology']['win_rate']:.1%}")
        """
        if not snapshots:
            return {
                'summary': {'total_trades': 0},
                'by_pair': {},
                'top_pairs': [],
                'worst_pairs': [],
                'by_industry': {},
                'by_close_reason': {},
                'benchmark_comparison': None
            }

        # === 基础摘要 ===
        summary = {
            'total_trades': len(snapshots),
            'total_pnl': sum(s.pair_pnl for s in snapshots),
            'avg_holding_days': np.mean([s.holding_days for s in snapshots])
        }

        # === 配对维度分析 ===
        by_pair = {}
        for snapshot in snapshots:
            pair_id = snapshot.pair_id
            if pair_id not in by_pair:
                by_pair[pair_id] = []
            by_pair[pair_id].append(snapshot)

        pair_stats = {}
        for pair_id, pair_snapshots in by_pair.items():
            pair_stats[pair_id] = TradeAnalyzer.analyze_pair(pair_snapshots)

        # 最佳/最差配对排名（按总盈亏）
        sorted_pairs = sorted(pair_stats.items(), key=lambda x: x[1]['total_pnl'], reverse=True)
        top_pairs = [(pair_id, stats['total_pnl']) for pair_id, stats in sorted_pairs[:10]]
        worst_pairs = [(pair_id, stats['total_pnl']) for pair_id, stats in sorted_pairs[-10:]]

        # === 行业维度分析 ===
        by_industry = {}
        for snapshot in snapshots:
            industry = snapshot.industry_group
            if industry not in by_industry:
                by_industry[industry] = []
            by_industry[industry].append(snapshot)

        industry_stats = {}
        for industry, industry_snapshots in by_industry.items():
            stats = TradeAnalyzer.analyze_pair(industry_snapshots)
            industry_stats[industry] = stats

        # === 平仓原因维度分析 ===
        by_close_reason = {}
        for snapshot in snapshots:
            reason = snapshot.close_reason
            if reason not in by_close_reason:
                by_close_reason[reason] = []
            by_close_reason[reason].append(snapshot)

        close_reason_stats = {}
        for reason, reason_snapshots in by_close_reason.items():
            stats = TradeAnalyzer.analyze_pair(reason_snapshots)

            # 如果提供了 SPY 价格，计算该平仓原因的 SPY 对比
            if spy_prices is not None:
                spy_comparison = TradeAnalyzer._analyze_spy_comparison(reason_snapshots, spy_prices)
                stats.update(spy_comparison)

            close_reason_stats[reason] = stats

        # === 交易级 SPY 对比（整体）===
        benchmark_comparison = None
        if spy_prices is not None:
            benchmark_comparison = TradeAnalyzer._analyze_spy_comparison(snapshots, spy_prices)

        return {
            'summary': summary,
            'by_pair': pair_stats,
            'top_pairs': top_pairs,
            'worst_pairs': worst_pairs,
            'by_industry': industry_stats,
            'by_close_reason': close_reason_stats,
            'benchmark_comparison': benchmark_comparison
        }


    @staticmethod
    def _analyze_spy_comparison(snapshots: List[TradeSnapshot], spy_prices: Dict) -> Dict:
        """
        计算交易级 SPY 对比指标（内部方法）

        Args:
            snapshots: 交易快照列表
            spy_prices: SPY 价格字典 {date: close_price}

        Returns:
            Dict: SPY 对比统计
            {
                'beat_spy_count': int,   # 跑赢 SPY 的交易数
                'beat_spy_rate': float,  # 跑赢比例（0-1）
                'avg_alpha': float,      # 平均超额收益（%）
                'alpha_std': float       # 超额收益标准差（%）
            }
        """
        alphas = []
        beat_spy_count = 0

        for snapshot in snapshots:
            # 计算 SPY 同期收益率
            spy_return = TradeAnalyzer._get_spy_return(
                snapshot.open_time,
                snapshot.close_time,
                spy_prices
            )

            # 如果 SPY 数据缺失，跳过该笔交易
            if spy_return is None:
                continue

            # 计算超额收益（alpha）
            alpha = snapshot.return_pct - spy_return
            alphas.append(alpha)

            if alpha > 0:
                beat_spy_count += 1

        # 如果没有有效数据，返回空统计
        if not alphas:
            return {
                'beat_spy_count': 0,
                'beat_spy_rate': 0,
                'avg_alpha': 0,
                'alpha_std': 0
            }

        beat_spy_rate = beat_spy_count / len(alphas)
        avg_alpha = np.mean(alphas)
        alpha_std = np.std(alphas, ddof=1) if len(alphas) > 1 else 0

        return {
            'beat_spy_count': beat_spy_count,
            'beat_spy_rate': beat_spy_rate,
            'avg_alpha': avg_alpha,
            'alpha_std': alpha_std
        }


    @staticmethod
    def _get_spy_return(open_date: datetime, close_date: datetime, spy_prices: Dict) -> Optional[float]:
        """
        计算 SPY 在指定时间段的收益率

        处理周末/节假日：向前/向后查找最近的交易日

        Args:
            open_date: 开仓日期（datetime）
            close_date: 平仓日期（datetime）
            spy_prices: SPY 价格字典 {date: close_price}

        Returns:
            float: SPY 收益率（%），数据缺失时返回 None

        Example:
            spy_return = TradeAnalyzer._get_spy_return(
                datetime(2024, 1, 15),
                datetime(2024, 1, 25),
                spy_prices
            )
            # 返回：2.3 (表示 2.3%)
        """
        # 转为日期（去掉时间部分）
        open_date = open_date.date() if isinstance(open_date, datetime) else open_date
        close_date = close_date.date() if isinstance(close_date, datetime) else close_date

        # 查找最近的交易日价格
        def find_nearest_price(target_date):
            # 向前最多查找 5 天（处理长周末）
            for offset in range(5):
                date = target_date - pd.Timedelta(days=offset)
                if date in spy_prices:
                    return spy_prices[date]

            # 向后查找 5 天
            for offset in range(1, 6):
                date = target_date + pd.Timedelta(days=offset)
                if date in spy_prices:
                    return spy_prices[date]

            return None  # 找不到

        spy_open_price = find_nearest_price(open_date)
        spy_close_price = find_nearest_price(close_date)

        # 数据缺失，返回 None
        if spy_open_price is None or spy_close_price is None:
            return None

        # 计算收益率（%）
        spy_return = ((spy_close_price / spy_open_price) - 1) * 100

        return spy_return
