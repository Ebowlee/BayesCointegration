# region imports
from AlgorithmImports import *
from typing import List  # 通常用于类型提示，当前结构中是良好习惯
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
        self.algorithm.Debug(f"[Execution] 本轮传入 PortfolioTargets: {len(targets)}")

        # 1. 将新目标加入目标集合中（覆盖同 symbol 的旧目标）
        self.targets_collection.add_range(targets)

        # 2. 成对处理（默认 PortfolioConstruction 输出顺序是成对的）
        if len(targets) != 0:
            pair_targets = []
            for target in targets:
                symbol = target.Symbol
                security = self.algorithm.Securities[symbol]
                if not security.IsTradable or target.Quantity is None:
                    continue

                pair_targets.append(target)
                if len(pair_targets) == 2:
                    t1, t2 = pair_targets
                    pair_targets = []

                    for t in (t1, t2):
                        symbol = t.Symbol
                        self.algorithm.set_holdings(symbol, t.Quantity)
                    self.algorithm.Debug(f"[Execution] 成对下单: [{t1.Symbol}, {t2.Symbol}]")
               
        # 3. 清理已完成目标
        self.targets_collection.clear_fulfilled(algorithm)



