"""
MarginAllocator - Level 1 全局资金分配器 (v7.30.8: 双模式分配)

职责:
- 计算可用保证金(MarginRemaining - fixed_buffer)
- 为同批次entry_candidates按质量分数分配保证金
- 根据账户状态切换分配模式(放大 vs 保护)

设计原则:
- Fixed Buffer: 整个回测周期固定不变(初始资金×2%)
- 双模式分配 (v7.30.8):
  * 放大模式 (current >= initial): 盈利后使用current × planned_pct, 上限2倍杠杆
  * 保护模式 (current < initial): 亏损时使用min(current, initial × planned_pct)
- 无状态: 每次allocate_margin()独立计算,使用调用时的快照

不负责:
- ❌ Level 2配对内部分配(symbol1 vs symbol2) - 由Pairs.calculate_leg_values负责
- ❌ 订单执行 - 由OrderExecutor负责
- ❌ 信号生成 - 由Pairs.get_signal负责
"""

from AlgorithmImports import *
from typing import Dict, List, Tuple


class MarginAllocator:
    """
    Level 1 全局资金分配器 (v7.30.8: 双模式分配)

    核心算法:
    1. Fixed Buffer: 初始资金×2%(整个回测周期固定)
    2. Available Margin: MarginRemaining - fixed_buffer
    3. 双模式分配 (v7.30.8):
       - 放大模式 (current >= initial):
         * 计划分配 = min(current × planned_pct, initial × 2.0 × planned_pct)
         * 实现复利增长,上限2倍杠杆保护
       - 保护模式 (current < initial):
         * 计划分配 = min(current, initial × planned_pct)
         * 维持baseline约束,防止过度投入
    4. 过滤门槛: 最小投资额(初始资金 × min_investment_ratio = 5% = $5,000)
    5. 顺序分配直到资金不足或候选耗尽

    使用示例:
    ```python
    # Initialize()中
    self.margin_allocator = MarginAllocator(self, self.config)

    # ExecutionManager中
    allocations = margin_allocator.allocate_margin(entry_candidates)
    # 返回: {('AAPL','MSFT'): 25000.0, ('GOOG','GOOGL'): 20000.0}

    # 盈利场景 (current=150k, initial=100k):
    # quality_score=0.8 → planned_pct=13% → allocation=19.5k (放大1.5倍)

    # 亏损场景 (current=70k, initial=100k):
    # quality_score=0.8 → planned_pct=13% → allocation=13k (baseline约束)
    ```
    """

    def __init__(self, algorithm, config):
        """
        初始化资金分配器

        Args:
            algorithm: QuantConnect算法实例
            config: StrategyConfig配置对象
        """
        self.algorithm = algorithm
        self.config = config

        # 从config提取关键参数
        pairs_config = config.pairs_trading
        self.margin_usage_ratio = pairs_config.margin_usage_ratio  # 0.98
        self.max_leverage_cap = pairs_config.max_leverage_cap      # 2.0 (v7.30.11)

        # 记录初始保证金(分配基准,整个回测周期固定)
        self.initial_available_fund = algorithm.Portfolio.MarginRemaining

        # 计算固定buffer(整个回测周期不变)
        self.fixed_buffer = self.initial_available_fund * (1 - self.margin_usage_ratio)

        # 最小投资额(从config直接计算: initial_cash × min_investment_ratio)
        self.min_investment_amount = (config.main.cash * config.pairs_trading.min_investment_ratio)



    def get_available_margin(self) -> float:
        """
        获取当前可用保证金

        公式:
            available = Portfolio.MarginRemaining - fixed_buffer

        设计要点:
        - fixed_buffer是全局固定值(初始资金×5%)
        - 不会随着MarginRemaining变化而变化
        - 用于预留交易手续费,防止margin call

        Returns:
            可用保证金(美元),最小为0

        示例:
            初始: MarginRemaining=$100k, fixed_buffer=$5k
            → available = $100k - $5k = $95k

            开仓$50k后: MarginRemaining=$50k, fixed_buffer=$5k(不变!)
            → available = $50k - $5k = $45k

            接近耗尽: MarginRemaining=$3k, fixed_buffer=$5k
            → available = max(0, $3k - $5k) = $0 (防止负数)
        """
        available = self.algorithm.Portfolio.MarginRemaining - self.fixed_buffer
        return max(0, available)


    def allocate_margin(self, entry_candidates: List[Tuple]) -> Dict[Tuple, float]:
        """
        为同批次entry_candidates公平分配保证金 (v7.30.8: 双模式分配)

        Args:
            entry_candidates: [(pair, signal, quality_score, planned_pct), ...]
                - pair: Pairs对象
                - signal: TradingSignal (LONG_SPREAD/SHORT_SPREAD)
                - quality_score: 配对质量分数(0-1)
                - planned_pct: 计划分配比例(已由quality_score计算)

        Returns:
            Dict[pair_id, allocated_amount]
            {
                ('AAPL', 'MSFT'): 25000.0,
                ('GOOG', 'GOOGL'): 20000.0,
                ...
            }

        核心算法 (v7.30.8更新):
        1. 获取当前可用保证金
        2. 判断分配模式:
           - 放大模式 (current >= initial): 盈利后使用current × planned_pct, 上限2倍杠杆
           - 保护模式 (current < initial): 亏损时使用min(current, initial × planned_pct)
        3. 遍历candidates（按质量分数降序）:
           a. 根据模式计算分配额
           b. 如果 >= 最小投资额: 执行分配并扣减可用资金
           c. 如果 < 最小投资额: 跳过该配对
        4. 返回所有成功分配的配对及其金额
        """
        allocations = {}

        # === Step 1: 批次开始时快照(局部initial_margin) ===
        current_available = self.get_available_margin()

        # 检查是否低于最小阈值
        if current_available < self.min_investment_amount:
            self.algorithm.Debug(
                f"[MarginAllocator] 可用保证金不足: "
                f"${current_available:,.0f} < 最小投资${self.min_investment_amount:,.0f}"
            )
            return {}

        # === Step 2: v7.30.8 判断分配模式 ===
        if current_available >= self.initial_available_fund:
            # 放大模式: 盈利后放大规模
            allocation_mode = "GROWTH"
            max_cap = self.initial_available_fund * self.max_leverage_cap  # v7.30.11: 从config读取
        else:
            # 保护模式: 亏损时收缩规模
            allocation_mode = "PROTECT"
            max_cap = self.initial_available_fund

        # === Step 3: 遍历candidates进行分配 ===
        for idx, (pair, signal, quality_score, planned_pct) in enumerate(entry_candidates, 1):
            if allocation_mode == "GROWTH":
                # 放大模式: 基于当前资金,但有上限保护
                planned_allocated = min(
                    current_available * planned_pct,
                    max_cap * planned_pct
                )
            else:
                # 保护模式: 保持原有逻辑
                planned_allocated = min(
                    current_available,
                    self.initial_available_fund * planned_pct
                )

            # 判断是否满足最小投资门槛
            if planned_allocated >= self.min_investment_amount:
                # 满足条件: 执行分配流程
                actual_allocated = planned_allocated

                # 记录分配
                allocations[pair.pair_id] = actual_allocated

                # 扣减剩余资金
                current_available = current_available - actual_allocated
            else:
                # 不满足条件: 跳过此配对,继续尝试下一个
                continue  # 继续尝试剩余配对

        # === Step 4: 返回分配结果 ===
        return allocations
