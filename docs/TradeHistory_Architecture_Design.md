# TradeHistory架构设计文档

## 概述

TradeHistory是一个优雅的交易历史追踪系统，基于领域驱动设计（DDD）原则，解决Pairs类平仓后历史数据丢失的问题。

---

## 设计理念

### 核心原则

1. **最小侵入**：不修改Pairs类，仅在平仓时捕获快照
2. **职责分离**：数据（Snapshot）、存储（Journal）、分析（Analyzer）三层分离
3. **不可变性**：历史记录一旦创建不可修改
4. **优雅接口**：简洁的API，3行代码完成记录

### 架构分层

```
TradeSnapshot（值对象）
    ↓ 不可变数据
TradeJournal（聚合根）
    ↓ 存储+索引
TradeAnalyzer（领域服务）
    ↓ 统计分析
```

---

## 核心组件

### 1. TradeSnapshot（交易快照 - 值对象）

#### 设计特点

- **不可变性**：使用`@dataclass(frozen=True)`确保创建后不可修改
- **自包含性**：包含重现交易的所有必要信息
- **工厂方法**：`from_pair()`自动从Pairs对象提取数据

#### 完整定义

```python
from dataclasses import dataclass
from datetime import datetime

@dataclass(frozen=True)
class TradeSnapshot:
    """
    交易快照（值对象）

    设计原则：
    - 不可变：创建后不能修改
    - 自包含：包含重现交易的所有信息
    - 最小侵入：直接从Pairs对象创建
    """
    # === 配对标识 ===
    pair_id: tuple  # (symbol1_str, symbol2_str)
    industry_group: str

    # === 时间轴 ===
    open_time: datetime
    close_time: datetime

    # === 方向与原因 ===
    direction: str  # 'LONG_SPREAD' 或 'SHORT_SPREAD'
    close_reason: str  # 'signal', 'stop_loss', 'timeout', 'risk'

    # === 价格与数量 ===
    entry_price1: float
    entry_price2: float
    exit_price1: float
    exit_price2: float
    qty1: int
    qty2: int

    # === 财务指标 ===
    entry_cost: float  # 开仓总成本
    pnl: float  # 已实现盈亏

    # === 统计参数（用于后续分析） ===
    beta: float  # 协整系数
    quality_score: float  # 开仓时的质量评分

    # === 衍生属性（懒计算） ===

    @property
    def holding_days(self) -> int:
        """持仓天数"""
        return (self.close_time - self.open_time).days

    @property
    def return_pct(self) -> float:
        """收益率"""
        return self.pnl / self.entry_cost if self.entry_cost > 0 else 0

    @property
    def symbol1(self) -> str:
        """提取symbol1字符串"""
        return self.pair_id[0]

    @property
    def symbol2(self) -> str:
        """提取symbol2字符串"""
        return self.pair_id[1]

    @classmethod
    def from_pair(cls, pair, close_reason: str, exit_price1: float,
                  exit_price2: float, pnl: float):
        """
        工厂方法：从Pairs对象创建快照

        优雅之处：
        - 所有信息从pair对象自动提取
        - 调用者只需提供平仓特定信息
        - 无需手动构造大量参数

        Args:
            pair: Pairs对象
            close_reason: 平仓原因
            exit_price1: symbol1平仓价格
            exit_price2: symbol2平仓价格
            pnl: 已实现盈亏
        """
        # 推断开仓方向
        direction = 'LONG_SPREAD' if pair.tracked_qty1 > 0 else 'SHORT_SPREAD'

        return cls(
            # 标识信息
            pair_id=pair.pair_id,
            industry_group=pair.industry_group,

            # 时间
            open_time=pair.position_opened_time,
            close_time=pair.algorithm.Time,

            # 方向与原因
            direction=direction,
            close_reason=close_reason,

            # 价格
            entry_price1=pair.entry_price1,
            entry_price2=pair.entry_price2,
            exit_price1=exit_price1,
            exit_price2=exit_price2,

            # 数量
            qty1=pair.tracked_qty1,
            qty2=pair.tracked_qty2,

            # 财务
            entry_cost=pair.entry_cost,
            pnl=pnl,

            # 统计
            beta=pair.beta_mean,
            quality_score=pair.quality_score
        )
```

#### 知识点：`@dataclass(frozen=True)`

**作用**：
1. 自动生成`__init__`、`__repr__`、`__eq__`等方法
2. `frozen=True`使对象不可变（创建后不能修改属性）
3. 不可变对象可以作为字典key、确保历史数据完整性

**对比**：
```python
# 不使用dataclass（繁琐）
class TradeSnapshot:
    def __init__(self, pair_id, pnl, ...):
        self.pair_id = pair_id
        self.pnl = pnl
        # ... 重复20个字段

    def __repr__(self):
        return f"TradeSnapshot(pair_id={self.pair_id}, ...)"

    def __eq__(self, other):
        return self.pair_id == other.pair_id and ...

# 使用dataclass（简洁）
@dataclass(frozen=True)
class TradeSnapshot:
    pair_id: tuple
    pnl: float
    # ... 只需声明类型
```

#### 知识点：`@classmethod`

**作用**：创建对象的工厂方法

**对比三种方法**：

| 方法类型 | 第一个参数 | 用途 | 示例 |
|---------|-----------|------|------|
| 实例方法 | `self` | 操作实例数据 | `pair.get_signal(data)` |
| 类方法 | `cls` | 工厂模式、多种构造方式 | `TradeSnapshot.from_pair(pair, ...)` |
| 静态方法 | 无 | 工具函数、不需要实例 | `Pairs.validate_threshold(2.0)` |

**为什么用classmethod**：
```python
# 方式1：直接构造（需要手动提取20个字段）
snapshot = TradeSnapshot(
    pair_id=pair.pair_id,
    industry_group=pair.industry_group,
    open_time=pair.position_opened_time,
    # ... 再写15个字段
)

# 方式2：工厂方法（自动提取）⭐
snapshot = TradeSnapshot.from_pair(pair, 'signal', 150.0, 100.0, 100.0)
```

---

### 2. TradeJournal（交易日志 - 聚合根）

#### 设计特点

- **单一职责**：只管存储和查询，不管分析
- **索引优化**：按配对分组，优化查询性能
- **返回副本**：保护内部状态不被外部修改

#### 完整定义

```python
from typing import Dict, List, Optional
import pandas as pd

class TradeJournal:
    """
    交易日志（聚合根）

    职责：
    - 存储所有交易快照
    - 提供基础查询接口
    - 维护内部索引（优化查询）

    不做：
    - 统计计算（委托给TradeAnalyzer）
    - 业务逻辑判断
    """

    def __init__(self, algorithm):
        self.algorithm = algorithm

        # === 主存储：时间序列 ===
        self._snapshots: List[TradeSnapshot] = []

        # === 索引：按配对分组（优化查询） ===
        self._by_pair: Dict[tuple, List[TradeSnapshot]] = {}

    def record(self, snapshot: TradeSnapshot):
        """
        记录一笔交易快照

        优雅之处：
        - 参数只需一个snapshot对象
        - 内部自动维护索引
        - 单一职责：纯存储操作
        """
        # 添加到主存储
        self._snapshots.append(snapshot)

        # 更新索引
        if snapshot.pair_id not in self._by_pair:
            self._by_pair[snapshot.pair_id] = []
        self._by_pair[snapshot.pair_id].append(snapshot)

        # 日志输出
        self.algorithm.Debug(
            f"[TradeJournal] 记录: {snapshot.pair_id} "
            f"{snapshot.direction} {snapshot.holding_days}天 "
            f"PnL=${snapshot.pnl:.2f} ({snapshot.return_pct:.2%})"
        )

    # === 基础查询 ===

    def get_all(self) -> List[TradeSnapshot]:
        """获取所有交易（按时间顺序）"""
        return self._snapshots.copy()  # 返回副本，保护内部状态

    def get_by_pair(self, pair_id: tuple) -> List[TradeSnapshot]:
        """获取指定配对的所有交易"""
        return self._by_pair.get(pair_id, []).copy()

    def get_count(self) -> int:
        """获取总交易次数"""
        return len(self._snapshots)

    def get_pair_count(self) -> int:
        """获取参与交易的配对数量"""
        return len(self._by_pair)

    # === 高级查询 ===

    def get_by_time_range(self, start_time: datetime, end_time: datetime) -> List[TradeSnapshot]:
        """
        按时间段查询

        Args:
            start_time: 开始时间（包含）
            end_time: 结束时间（包含）

        Example:
            # 查询2020年1月到2024年5月的所有交易
            trades = journal.get_by_time_range(
                datetime(2020, 1, 1),
                datetime(2024, 5, 31)
            )
        """
        return [
            snap for snap in self._snapshots
            if start_time <= snap.close_time <= end_time
        ]

    def query(self,
              pair_id: Optional[tuple] = None,
              start_time: Optional[datetime] = None,
              end_time: Optional[datetime] = None,
              close_reason: Optional[str] = None,
              min_return: Optional[float] = None) -> List[TradeSnapshot]:
        """
        灵活查询（支持多条件组合）

        Examples:
            # 查询AAPL-MSFT在2023年所有因信号平仓的交易
            trades = journal.query(
                pair_id=('AAPL', 'MSFT'),
                start_time=datetime(2023, 1, 1),
                end_time=datetime(2023, 12, 31),
                close_reason='signal'
            )

            # 查询所有收益率>5%的交易
            trades = journal.query(min_return=0.05)
        """
        results = self._snapshots

        # 逐个应用过滤条件
        if pair_id is not None:
            results = [s for s in results if s.pair_id == pair_id]

        if start_time is not None:
            results = [s for s in results if s.close_time >= start_time]

        if end_time is not None:
            results = [s for s in results if s.close_time <= end_time]

        if close_reason is not None:
            results = [s for s in results if s.close_reason == close_reason]

        if min_return is not None:
            results = [s for s in results if s.return_pct >= min_return]

        return results

    # === 导出功能 ===

    def to_dataframe(self) -> Optional[pd.DataFrame]:
        """
        导出为DataFrame（便于后续分析）

        优雅之处：
        - 利用dataclass自动序列化
        - 懒加载：只在需要时构造
        """
        if not self._snapshots:
            return None

        records = []
        for snap in self._snapshots:
            records.append({
                'pair_id': str(snap.pair_id),
                'symbol1': snap.symbol1,
                'symbol2': snap.symbol2,
                'industry_group': snap.industry_group,
                'open_time': snap.open_time,
                'close_time': snap.close_time,
                'holding_days': snap.holding_days,
                'direction': snap.direction,
                'close_reason': snap.close_reason,
                'entry_cost': snap.entry_cost,
                'pnl': snap.pnl,
                'return_pct': snap.return_pct,
                'beta': snap.beta,
                'quality_score': snap.quality_score
            })

        return pd.DataFrame(records)
```

#### 查询性能优化

**索引策略**：
- ✅ 高频查询建索引：`_by_pair`（按配对查询很常见）
- ❌ 低频查询不建索引：时间查询（直接遍历即可）

**时间查询为什么不建索引**：
1. `_snapshots`已按时间排序（append保证）
2. 几百上千笔交易，线性遍历足够快
3. 如需优化，可用二分查找（bisect）

---

### 3. TradeAnalyzer（分析器 - 领域服务）

#### 设计特点

- **无状态**：纯函数式计算，无副作用
- **可插拔**：不同分析需求独立方法
- **易测试**：与存储层解耦

#### 完整定义

```python
import numpy as np

class TradeAnalyzer:
    """
    交易分析器（领域服务）

    职责：
    - 对TradeJournal中的数据进行统计分析
    - 生成各种维度的报告

    设计特点：
    - 无状态：纯函数式计算
    - 可插拔：不同分析需求独立方法
    - 易测试：与存储层解耦
    """

    @staticmethod
    def analyze_pair(snapshots: List[TradeSnapshot]) -> Optional[Dict]:
        """
        分析单个配对的历史表现

        Args:
            snapshots: 该配对的所有交易快照

        Returns:
            统计字典 或 None（无数据）
        """
        if not snapshots:
            return None

        # 基础统计
        total_trades = len(snapshots)
        wins = [s for s in snapshots if s.pnl > 0]
        win_rate = len(wins) / total_trades

        # 财务指标
        total_pnl = sum(s.pnl for s in snapshots)
        returns = [s.return_pct for s in snapshots]
        avg_return = np.mean(returns)

        # 时间指标
        avg_holding_days = np.mean([s.holding_days for s in snapshots])

        # Sharpe Ratio（年化）
        if len(returns) > 1 and avg_holding_days > 0:
            sharpe = (avg_return / np.std(returns, ddof=1)) * np.sqrt(252 / avg_holding_days)
        else:
            sharpe = 0

        # 极值
        max_win = max(s.pnl for s in snapshots)
        max_loss = min(s.pnl for s in snapshots)

        # 平仓原因分布
        close_reasons = {}
        for s in snapshots:
            reason = s.close_reason
            if reason not in close_reasons:
                close_reasons[reason] = 0
            close_reasons[reason] += 1

        return {
            'total_trades': total_trades,
            'win_rate': win_rate,
            'total_pnl': total_pnl,
            'avg_return': avg_return,
            'avg_holding_days': avg_holding_days,
            'sharpe_ratio': sharpe,
            'max_win': max_win,
            'max_loss': max_loss,
            'close_reasons': close_reasons
        }

    @staticmethod
    def analyze_global(snapshots: List[TradeSnapshot]) -> Optional[Dict]:
        """
        分析整个策略的表现

        Args:
            snapshots: 所有交易快照
        """
        if not snapshots:
            return None

        # 复用pair analysis逻辑
        pair_stats = TradeAnalyzer.analyze_pair(snapshots)

        # 添加全局特有统计
        unique_pairs = len(set(s.pair_id for s in snapshots))

        # 按行业分组
        by_industry = {}
        for s in snapshots:
            if s.industry_group not in by_industry:
                by_industry[s.industry_group] = []
            by_industry[s.industry_group].append(s)

        industry_stats = {
            industry: TradeAnalyzer.analyze_pair(trades)
            for industry, trades in by_industry.items()
        }

        return {
            **pair_stats,
            'unique_pairs': unique_pairs,
            'industry_breakdown': industry_stats
        }
```

#### 知识点：`@staticmethod`

**作用**：定义在类里的普通函数（不需要self或cls）

**对比三种方法**：

```python
class MyClass:
    def __init__(self):
        self.data = "实例数据"

    # 实例方法：需要访问self
    def instance_method(self):
        return self.data

    # 类方法：工厂模式、访问类属性
    @classmethod
    def create(cls):
        return cls()

    # 静态方法：工具函数、不需要self或cls
    @staticmethod
    def utility_function(x, y):
        return x + y

# 调用
obj = MyClass()
obj.instance_method()  # 需要实例
MyClass.create()  # 通过类调用，返回新实例
MyClass.utility_function(1, 2)  # 通过类调用，纯函数
```

**为什么TradeAnalyzer用静态方法**：
1. 分析是纯函数（输入快照列表 → 输出统计结果）
2. 不需要保存状态（无self）
3. 组织相关功能（避免全局函数污染命名空间）

---

## 集成方式

### main.py修改

#### 1. 初始化（1行）

```python
class BayesianCointegrationStrategy(QCAlgorithm):
    def Initialize(self):
        # ... 现有初始化 ...

        # ⭐ 添加TradeJournal
        self.trade_journal = TradeJournal(self)
```

#### 2. 平仓时记录（3行）

```python
def handle_position_close(self, pair, close_reason):
    # 平仓前获取必要信息
    pnl = pair.get_pair_pnl()
    exit_price1 = self.Portfolio[pair.symbol1].Price
    exit_price2 = self.Portfolio[pair.symbol2].Price

    # 执行平仓
    tickets = pair.close_position()
    if tickets:
        self.tickets_manager.register_tickets(pair.pair_id, tickets)

        # ⭐ 记录历史（3行代码）
        snapshot = TradeSnapshot.from_pair(
            pair, close_reason, exit_price1, exit_price2, pnl
        )
        self.trade_journal.record(snapshot)
```

#### 3. 回测结束分析

```python
def OnEndOfAlgorithm(self):
    # 全局统计
    global_stats = TradeAnalyzer.analyze_global(
        self.trade_journal.get_all()
    )

    if global_stats:
        self.Debug(f"[回测总结] 总交易次数: {global_stats['total_trades']}")
        self.Debug(f"[回测总结] 胜率: {global_stats['win_rate']:.2%}")
        self.Debug(f"[回测总结] 平均收益率: {global_stats['avg_return']:.2%}")
        self.Debug(f"[回测总结] Sharpe Ratio: {global_stats['sharpe_ratio']:.2f}")
        self.Debug(f"[回测总结] 参与配对数: {global_stats['unique_pairs']}")

    # 导出详细数据
    df = self.trade_journal.to_dataframe()
    if df is not None:
        # 可以保存到文件或进一步分析
        pass
```

---

## 使用示例

### 查询示例

```python
# 1. 获取所有交易
all_trades = self.trade_journal.get_all()
print(f"总共{len(all_trades)}笔交易")

# 2. 查询特定配对
aapl_msft = self.trade_journal.get_by_pair(('AAPL', 'MSFT'))
print(f"AAPL-MSFT交易{len(aapl_msft)}次")

# 3. 时间段查询
trades_2023 = self.trade_journal.get_by_time_range(
    datetime(2023, 1, 1),
    datetime(2023, 12, 31)
)
print(f"2023年交易{len(trades_2023)}次")

# 4. 组合查询
stop_losses = self.trade_journal.query(
    start_time=datetime(2023, 1, 1),
    end_time=datetime(2023, 12, 31),
    close_reason='stop_loss'
)
print(f"2023年止损{len(stop_losses)}次")

# 5. 高收益交易
high_return = self.trade_journal.query(min_return=0.10)
for trade in high_return:
    print(f"{trade.pair_id}: {trade.return_pct:.2%}")
```

### 分析示例

```python
# 1. 配对级分析
pair_stats = TradeAnalyzer.analyze_pair(
    self.trade_journal.get_by_pair(('AAPL', 'MSFT'))
)
print(f"胜率: {pair_stats['win_rate']:.2%}")
print(f"Sharpe: {pair_stats['sharpe_ratio']:.2f}")

# 2. 全局分析
global_stats = TradeAnalyzer.analyze_global(
    self.trade_journal.get_all()
)
print(f"整体胜率: {global_stats['win_rate']:.2%}")
print(f"参与配对: {global_stats['unique_pairs']}个")

# 3. 行业分析
for industry, stats in global_stats['industry_breakdown'].items():
    print(f"{industry}: 胜率{stats['win_rate']:.2%}")

# 4. DataFrame分析
df = self.trade_journal.to_dataframe()
if df is not None:
    # Pandas强大的分析功能
    print(df.groupby('close_reason')['pnl'].sum())
    print(df[df['return_pct'] > 0.05].describe())
```

---

## 设计优势

### 面向对象原则

1. **单一职责原则（SRP）**
   - TradeSnapshot：只负责数据封装
   - TradeJournal：只负责存储和查询
   - TradeAnalyzer：只负责统计分析

2. **开闭原则（OCP）**
   - 扩展分析方法：添加TradeAnalyzer方法
   - 扩展查询方式：添加TradeJournal方法
   - 无需修改现有代码

3. **依赖倒置原则（DIP）**
   - TradeAnalyzer依赖抽象（List[TradeSnapshot]）
   - 不依赖具体存储实现

### 函数式编程美学

1. **不可变性**
   - TradeSnapshot使用`frozen=True`
   - 历史记录不可篡改

2. **纯函数**
   - TradeAnalyzer所有方法无副作用
   - 相同输入保证相同输出

3. **数据与行为分离**
   - Snapshot是纯数据
   - Analyzer是纯行为

### 实用性

1. **最小侵入**
   - Pairs类无需修改
   - 3行代码完成记录

2. **易于测试**
   - 每个类独立可测
   - Snapshot是值对象，直接比较
   - Analyzer是纯函数，易断言

3. **可扩展性**
   - 新增字段：修改TradeSnapshot
   - 新增统计：扩展TradeAnalyzer
   - 新增查询：扩展TradeJournal

---

## 未来扩展方向

### 1. 持久化

```python
class TradeJournal:
    def save_to_file(self, filepath):
        """保存到文件"""
        df = self.to_dataframe()
        df.to_csv(filepath, index=False)

    @classmethod
    def load_from_file(cls, algorithm, filepath):
        """从文件加载"""
        df = pd.read_csv(filepath)
        journal = cls(algorithm)
        for _, row in df.iterrows():
            snapshot = TradeSnapshot(...)  # 从row构造
            journal.record(snapshot)
        return journal
```

### 2. 高级分析

```python
class TradeAnalyzer:
    @staticmethod
    def calculate_max_drawdown(snapshots):
        """计算最大回撤"""
        pass

    @staticmethod
    def analyze_holding_period_returns(snapshots):
        """按持仓期分组分析收益"""
        pass

    @staticmethod
    def compare_quality_score_performance(snapshots):
        """分析质量评分与实际表现的相关性"""
        pass
```

### 3. 可视化

```python
class TradeVisualizer:
    @staticmethod
    def plot_cumulative_pnl(snapshots):
        """绘制累计盈亏曲线"""
        pass

    @staticmethod
    def plot_win_rate_by_industry(snapshots):
        """按行业绘制胜率柱状图"""
        pass
```

---

## 总结

TradeHistory架构遵循领域驱动设计（DDD）原则，实现了：

✅ **优雅的数据模型**（不可变快照）
✅ **清晰的职责分离**（存储、查询、分析）
✅ **最小的代码侵入**（3行代码集成）
✅ **强大的扩展能力**（易添加新功能）
✅ **完整的历史追踪**（解决数据丢失问题）

这是一个教科书级的面向对象设计案例，值得深入学习！
