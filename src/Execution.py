# region imports
from AlgorithmImports import *
from typing import List # 虽然在当前骨架中未直接使用List的类型提示，但执行模型通常会处理列表，预先导入有益
from AlgorithmImports import PortfolioTargetCollection
# endregion

class MyExecutionModel(ExecutionModel):
    """
    工作流程：
        1. 添加新的目标进集合；
        2. 遍历所有未完成的目标；
        3. 计算是否还有未执行的数量；
        4. 若有未执行，则下单；
        5. 清理已完成目标。
    """

    def __init__(self, algorithm: QCAlgorithm):
        self.algorithm = algorithm
        self.targets_collection = PortfolioTargetCollection()
        self.algorithm.Debug("[Execution] 初始化完成")
        


    def Execute(self, algorithm: QCAlgorithm, targets: List[PortfolioTarget]):
        # 1. 将新目标加入目标集合中（覆盖同 symbol 的旧目标）
        self.targets_collection.add_range(targets)

        # 2. 如果集合不为空，逐个处理目标（两两成对）
        if not self.targets_collection.is_empty:
            pair_buffer = []

            for target in self.targets_collection.order_by_margin_impact(algorithm):
                symbol = target.Symbol
                security = algorithm.Securities[symbol]

                if not security.IsTradable or target.Quantity is None:
                    continue

                pair_buffer.append(target)

                # 3. 每两个目标作为一组执行（假设目标输出顺序由 PortfolioConstruction 保证是成对的）
                if len(pair_buffer) == 2:
                    t1, t2 = pair_buffer
                    pair_buffer = []

                    for t in (t1, t2):
                        symbol = t.Symbol
                        security = algorithm.Securities[symbol]

                        unordered_quantity = OrderSizing.get_unordered_quantity(algorithm, t)

                        if unordered_quantity != 0:
                            algorithm.MarketOrder(symbol, unordered_quantity)
                            action = "平仓" if target.Quantity == 0 else "建/调仓"
                            self.algorithm.Debug(f"[Execution] {action}: {symbol.Value}, 未下单量: {unordered_quantity}, 目标量: {target.Quantity}")

            # 4. 移除已完成的目标（无论是建仓还是平仓）
            self.targets_collection.clear_fulfilled(algorithm)


