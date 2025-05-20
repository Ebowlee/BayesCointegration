# region imports
from AlgorithmImports import *
from typing import List
# endregion

class BayesianCointegrationRiskManagementModel(RiskManagementModel):
    """
    【模块: RiskManagement - 贝叶斯协整风险管理模型】

    职责：
    - 监控和管理由投资组合构建模块生成的投资组合目标的风险。
    - 根据预设的组合最大回撤参数调整或否决目标。
    - 根据预设的最大持仓天数对超期持仓进行平仓。
    - 确保策略在可接受的风险范围内运行。

    调用流程：
    - QuantConnect 框架在 PortfolioConstructionModel 生成目标后调用 `ManageRisk` 方法。
    - `ManageRisk` 内部协调调用处理最大回撤和最大持仓时间的风险逻辑。
    """

    def __init__(self,algorithm: QCAlgorithm):
        """
        Attributes:
            self.algorithm (QCAlgorithm): QCAlgorithm 的引用。
            self.max_drawdown_pct (float): 最大回撤阈值。
            self.portfolio_peak_value (float): 用于计算回撤的投资组合历史峰值。
            self.current_drawdown (float): 当前计算的组合回撤百分比。
        """
        super().__init__()
        self.algorithm = algorithm
        self.max_drawdown_pct = 0.3
        self.portfolio_peak_value: float = 0.0 
        self.current_drawdown: float = 0.0
        self.algorithm.Debug("[RiskManagement] 初始化完成")



    def ManageRisk(self, algorithm: QCAlgorithm, targets: List[PortfolioTarget]) -> List[PortfolioTarget]:
        """
        - 此方法由 QuantConnect 框架自动调用，用于评估和调整投资组合目标。
        - 协调最大回撤风险检查和最大持仓时间风险检查逻辑。
        """

        # 1. 若触发最大回撤，立即清仓全部持仓
        self._force_flat_if_portfolio_drawdown_exceeds_max_drawdown(algorithm)

        # 2. 若存在没有 active insight 的持仓，也清仓
        self._force_flat_if_asset_has_no_active_insight(algorithm)

        # 3. 检查协整对中是否有崩盘资产（此处直接调用 Liquidate，不需要返回 target）
        targets = self._force_liquidate_pair_if_crash(algorithm, targets)

        return targets  


    def _force_flat_if_portfolio_drawdown_exceeds_max_drawdown(self, algorithm: QCAlgorithm) -> List[PortfolioTarget]:
        """
        若组合当前回撤超过最大允许阈值，触发全仓强制平仓，并清除相关 insights。
        返回：用于清仓的 PortfolioTarget 列表（用于框架内统一处理）
        """
        current_value = algorithm.Portfolio.TotalPortfolioValue

        if current_value > self.portfolio_peak_value:
            self.portfolio_peak_value = current_value

        if self.portfolio_peak_value > 0:
            self.current_drawdown = (self.portfolio_peak_value - current_value) / self.portfolio_peak_value
        else:
            self.current_drawdown = 0.0

        algorithm.Debug(f"[RiskManagement] -- [max_drawdown] Peak: {self.portfolio_peak_value:.0f}, Value: {current_value:.0f}, Drawdown: {self.current_drawdown:.1%}")

        if self.current_drawdown > self.max_drawdown_pct:
            algorithm.Error(f"[RiskManagement] -- [max_drawdown] 触发最大回撤: {self.current_drawdown:.1%} > {self.max_drawdown_pct:.1%}. 全仓清仓！！！")
            for holding in algorithm.Portfolio.Values:
                if holding.Invested:
                    algorithm.insights.cancel([holding.Symbol])
                    algorithm.Liquidate(holding.Symbol, tag="PortfolioDrawdown>MaxDrawdown")
            return []

        return []



    def _force_flat_if_asset_has_no_active_insight(self, algorithm: QCAlgorithm) -> List[PortfolioTarget]:
        """
        风控兜底机制：
        若某个持仓资产已经没有 active insight, 则视为“信号残留未清理”, 发送平仓指令并清除其 insight。
        """
        for symbol, holding in algorithm.Portfolio.items():
            if not holding.Invested:
                continue

            if not algorithm.insights.has_active_insights(symbol, algorithm.utc_time):
                algorithm.Debug(f"[RiskManagement] -- [AssetHasNoActiveInsight] {symbol.Value} 触发清仓！！！")
                algorithm.insights.cancel([symbol])
                algorithm.Liquidate(symbol, tag="AssetHasNoActiveInsight")

        return []



    def _force_liquidate_pair_if_crash(self, algorithm: QCAlgorithm, targets: List[PortfolioTarget]) -> None:
        """
        若协整对中任一资产价格较其平均建仓成本下跌超过 50%，则强制平仓整个协整对。
        该函数假设每轮 targets 仍以协整对为单位（两个 symbol 成对出现）。
        """
        # 临时缓冲协整对
        temp_pair = []
        force_liquidate_targets = []

        for target in targets:
            if target.Quantity == 0:
                continue
            temp_pair.append(target)

            # 每两项构成一个协整对
            if len(temp_pair) == 2:
                t1, t2 = temp_pair
                temp_pair = []  # 清空，为下一个 pair 做准备

                for t in (t1, t2):
                    symbol = t.Symbol
                    holding = algorithm.Portfolio[symbol]
                    if not holding.Invested:
                        continue  # 不处理未持仓资产

                    avg_price = holding.AveragePrice
                    if avg_price == 0:
                        continue  # 避免除零

                    current_price = algorithm.Securities[symbol].Price
                    drawdown_pct = (avg_price - current_price) / avg_price

                    if drawdown_pct >= 0.5:
                        algorithm.Debug(f"[RiskManagement] -- [pair_crash] 协整对中 {symbol.Value} 下跌 {drawdown_pct:.1%} 超过 50%，强平整个协整对！！！")
                        algorithm.insights.cancel([t1.Symbol, t2.Symbol])
                        algorithm.Liquidate(t1.Symbol, tag="PairCrashDrawdown>50%")
                        force_liquidate_targets.extend([t1, t2])
                        break  # 一旦触发就退出该 pair 的检查

        # 从 targets 中移除 force_liquidate_targets  
        targets = [t for t in targets if t not in force_liquidate_targets]

        return targets
