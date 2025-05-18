# region imports
from AlgorithmImports import *
from typing import List # 虽然在当前骨架中未直接使用List的类型提示，但执行模型通常会处理列表，预先导入有益
# endregion

class MyExecutionModel(ExecutionModel):
    """
    【模块：ExecutionModel - 基础执行模型】

    职责：
    - 接收由风险管理模块（或投资组合构建模块，如果没有风险管理模块）传递过来的投资组合目标 (PortfolioTarget)。
    - 将这些投资组合目标转化为实际的订单请求。
    - 执行订单，例如市价单、限价单等。

    调用流程：
    - QuantConnect 框架在 RiskManagementModel (或 PortfolioConstructionModel) 处理完目标后，
      会调用本模块的 `Execute` 方法。
    """

    def __init__(self, algorithm: QCAlgorithm):
        """
        - 初始化执行模型。
        - 可以存储 QCAlgorithm 的引用，以便访问例如 `SetHoldings`, `MarketOrder`, `LimitOrder` 等方法。
        """
        self.algorithm = algorithm
        


    def Execute(self, algorithm: QCAlgorithm, targets: List[PortfolioTarget]):
        """
        - 此方法由 QuantConnect 框架自动调用，用于执行投资组合目标。
        - 遍历传入的 PortfolioTarget 列表，并为每个目标生成和执行相应的订单。
        """

        for target in targets:
            # 检查资产是否可交易，这是一个好习惯
            security = algorithm.Securities[target.Symbol]
            if not security.IsTradable:
                # algorithm.Log(f"[ExecutionModel] Symbol {target.Symbol.Value} is not tradable. Skipping target: {target.Quantity}")
                continue

            # 使用 SetHoldings 来达到目标持仓。
            # target.Quantity 在这里通常是由 PortfolioConstructionModel 设置的目标百分比或股数。
            # 如果是百分比，SetHoldings 会将其转换为具体的股数。
            # 如果已经是股数，SetHoldings 会直接使用它。
            # SetHoldings 会处理重复下单的问题（即如果当前持仓已满足目标，则不会下单）。
            algorithm.SetHoldings(target.Symbol, target.Quantity)
            # algorithm.Debug(f"[ExecutionModel] Executed SetHoldings for {target.Symbol.Value} to quantity/percentage {target.Quantity}")
