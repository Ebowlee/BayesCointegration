"""
交易快照 - Value Object

职责:
1. 不可变交易数据对象
2. 用于未来扩展(例如: 导出到自定义格式)

设计原则:
- Immutable (frozen=True)
- 无业务逻辑
- 纯数据容器

当前状态 (v7.2.0):
- 保留此类以便未来扩展
- TradeAnalyzer暂时不使用(通过JSON Lines输出到logs.txt)
"""

from dataclasses import dataclass
from typing import Tuple, Optional, TYPE_CHECKING
from datetime import datetime

if TYPE_CHECKING:
    from ..Pairs import Pairs


@dataclass(frozen=True)
class TradeSnapshot:
    """
    交易快照 (不可变Value Object)

    当前版本 (v7.2.0): 预留未来使用
    - TradeAnalyzer通过JSON Lines输出到logs.txt
    - 如需导出自定义格式,可使用此类
    """
    pair_id: Tuple[str, str]
    signal: str
    entry_time: datetime
    exit_time: Optional[datetime]
    pnl: float
    pnl_pct: float
    reason: str
    holding_days: int
    entry_zscore: float
    exit_zscore: float

    @classmethod
    def from_pair(cls, pair: 'Pairs', reason: str) -> 'TradeSnapshot':
        """
        工厂方法: 从Pairs对象创建快照

        当前版本 (v7.2.0): 未使用
        保留以便未来扩展(例如: 导出到自定义格式)
        """
        return cls(
            pair_id=pair.pair_id,
            signal=pair.signal,
            entry_time=pair.entry_time,
            exit_time=pair.algorithm.Time,
            pnl=pair.get_pair_pnl(mode='final') * pair.algorithm.Portfolio.TotalPortfolioValue / 100,
            pnl_pct=pair.get_pair_pnl(mode='final'),
            reason=reason,
            holding_days=pair.get_pair_holding_days(),
            entry_zscore=pair.entry_zscore,
            exit_zscore=pair.get_zscore(),
        )
