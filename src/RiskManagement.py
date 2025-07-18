# region imports
from AlgorithmImports import *
from typing import List
# endregion

class BayesianCointegrationRiskManagementModel(RiskManagementModel):
    """
    个股持有时间限制, 在下一次选股前一天全部清仓
    """
    
    def __init__(self, algorithm: QCAlgorithm):
        super().__init__()
        self.algorithm = algorithm
        self.is_selection_on_next_day = False

    

    def manage_risk(self, algorithm: QCAlgorithm, targets: List[PortfolioTarget]) -> List[PortfolioTarget]:
        if self.is_selection_on_next_day:
            # 先打印一下targets包含那些symbol
            symbols = [target.symbol for target in targets]
            self.algorithm.Debug(f"[RiskManagement] - symbols: {[symbol.Value for symbol in symbols]}")

            # 将所有target清仓
            self.is_selection_on_next_day = False
            return [PortfolioTarget(target.symbol, 0) for target in targets]
        else:
            return targets
       

    
    def IsSelectionOnNextDay(self):
        self.is_selection_on_next_day = True
        self.algorithm.Debug("[RiskManagement] - IsSelectionOnNextDay: True")
        