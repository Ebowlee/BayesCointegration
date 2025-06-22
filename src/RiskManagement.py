# region imports
from AlgorithmImports import *
from typing import List
from QuantConnect.Algorithm.Framework.Risk import MaximumDrawdownPercentPerSecurity, TrailingStopRiskManagementModel, MaximumUnrealizedProfitPercentPerSecurity
# endregion

class BayesianCointegrationRiskManagementModel(RiskManagementModel):
    """
    贝叶斯协整风险管理模型：
    - 使用 MaximumDrawdownPercentPerSecurity 限制单个资产的最大回撤。
    - 使用 TrailingStopRiskManagementModel 限制单个资产的最大回撤。
    - 使用 MaximumUnrealizedProfitPercentPerSecurity 限制单个资产的最大未实现利润。
    """
    def __init__(self, algorithm: QCAlgorithm):
        super().__init__()
        self.algorithm = algorithm
        self.base_models = [
            MaximumDrawdownPercentPerSecurity(0.15),
            TrailingStopRiskManagementModel(0.10),
            MaximumUnrealizedProfitPercentPerSecurity(0.25)
        ]



    def manage_risk(self, algorithm: QCAlgorithm, targets: List[PortfolioTarget]) -> List[PortfolioTarget]:
        """
        管理风险：
        - 检查目标数量是否为偶数，如果不是则返回空列表。
        - 构建 symbol → pair 映射。
        - 执行所有基础风控模型，收集所有被触发的 symbol。
        - 恢复协整对，并生成清仓列表。
        - 取消 insight 并生成清仓 PortfolioTargets。
        """ 
        if len(targets) % 2 != 0:
            self.algorithm.Debug("RiskModel Warning: Odd number of targets, cannot resolve pairs safely.")
            return []

        # Step 1: 构造 symbol → pair 映射
        symbol_to_pair = {}
        for i in range(0, len(targets), 2):
            s1, s2 = targets[i].symbol, targets[i + 1].symbol
            pair = (s1, s2)
            symbol_to_pair[s1] = pair
            symbol_to_pair[s2] = pair

        # Step 2: 执行所有基础风控模型，收集所有被触发的 symbol
        triggered_symbols = set()
        for model in self.base_models:
            base_targets = model.manage_risk(algorithm, targets)
            for t in base_targets:
                triggered_symbols.add(t.symbol)

        # Step 3: 恢复协整对，并生成清仓列表
        to_clear = set()
        for symbol in triggered_symbols:
            pair = symbol_to_pair.get(symbol)
            if pair:
                to_clear.update(pair)

        # Step 4: 取消 insight 并生成清仓 PortfolioTargets
        fine_targets = []
        for symbol in to_clear:
            self.algorithm.Insights.Cancel(symbol)
            fine_targets.append(PortfolioTarget(symbol, 0))

        return fine_targets





