# region imports
from AlgorithmImports import *
from typing import List
# endregion

class BayesianCointegrationRiskManagementModel(RiskManagementModel):
    """
    【模块：RiskManagement - 贝叶斯协整风险管理模型】

    职责：
    - 监控和管理由投资组合构建模块生成的投资组合目标的风险。
    - 根据预设的组合最大回撤参数调整或否决目标。
    - 根据预设的最大持仓天数对超期持仓进行平仓。
    - 确保策略在可接受的风险范围内运行。

    调用流程：
    - QuantConnect 框架在 PortfolioConstructionModel 生成目标后调用 `ManageRisk` 方法。
    - `ManageRisk` 内部协调调用处理最大回撤和最大持仓时间的风险逻辑。
    """

    def __init__(self,
                 algorithm: QCAlgorithm,
                 max_drawdown_pct: float = 0.3,
                 max_holding_days: int = 5):                          # 新增参数：最大持仓天数
        """

        目的：
        - 设置风险管理模型的参数阈值。
        - 初始化用于跟踪风险指标的内部变量。

        Args:
            algorithm (QCAlgorithm): QCAlgorithm 实例，用于访问算法级别的属性和方法（如 Portfolio）。
            max_drawdown_pct (float): 组合最大允许回撤百分比。
            max_holding_days (int): 单个协整对（持仓）的最大允许持有天数。

        Attributes:
            self.algorithm (QCAlgorithm): QCAlgorithm 的引用。
            self.max_drawdown_pct (float): 最大回撤阈值。
            self.max_holding_days (int): 最大持仓天数阈值。
            self.portfolio_peak_value (float): 用于计算回撤的投资组合历史峰值。
            self.current_drawdown (float): 当前计算的组合回撤百分比。
        """
        super().__init__()
        self.algorithm = algorithm
        self.max_drawdown_pct = max_drawdown_pct
        self.max_holding_days = max_holding_days                       # 存储最大持仓天数

        self.portfolio_peak_value: float = 0.0 
        self.current_drawdown: float = 0.0

        self.holding_start_times = {}



    def ManageRisk(self, algorithm: QCAlgorithm, targets: List[PortfolioTarget]) -> List[PortfolioTarget]:
        """
        - 此方法由 QuantConnect 框架自动调用，用于评估和调整投资组合目标。
        - 协调最大回撤风险检查和最大持仓时间风险检查逻辑。
        """
        # 步骤 1: 应用最大回撤风险
        targets_after_drawdown = self._evaluate_and_apply_max_drawdown_risk(algorithm, targets)
            
        # 如果最大回撤已触发全局清仓 (通过 self.current_drawdown 状态判断，该状态在 _evaluate_and_apply_max_drawdown_risk 中更新)
        if self.current_drawdown > self.max_drawdown_pct:
            algorithm.Debug(f"[RiskManagement] Max drawdown ({self.current_drawdown:.2%}) triggered liquidation. Skipping max holding time check.")
            risk_revised_portfolio_targets = targets_after_drawdown # 命名调整
            return risk_revised_portfolio_targets # 直接返回清仓所有头寸的目标
        
        # 步骤 2: 如果未因最大回撤清仓，则应用最大持仓时间风险
        # targets_after_drawdown 在未触发回撤时等于原始 targets
        targets_after_holding_time = self._apply_max_holding_time_risk(algorithm, targets_after_drawdown)
        risk_revised_portfolio_targets = targets_after_holding_time # 命名调整
        return risk_revised_portfolio_targets



    def _evaluate_and_apply_max_drawdown_risk(self, algorithm: QCAlgorithm, targets: List[PortfolioTarget]) -> List[PortfolioTarget]:
        """
        - 更新组合的当前回撤。
        - 检查当前组合回撤是否超过 `self.max_drawdown_pct`。
        - 如果触发最大回撤，则生成清仓目标；否则返回原始目标。
        """
        # 步骤 1: 更新组合级别指标
        current_portfolio_value = algorithm.Portfolio.TotalPortfolioValue

        if current_portfolio_value > self.portfolio_peak_value:
            self.portfolio_peak_value = current_portfolio_value
        
        if self.portfolio_peak_value > 0:
            self.current_drawdown = (self.portfolio_peak_value - current_portfolio_value) / self.portfolio_peak_value
        else:
            self.current_drawdown = 0.0
        
        algorithm.Debug(f"[RiskManagement] Peak: {self.portfolio_peak_value:.2f}, Value: {current_portfolio_value:.2f}, Drawdown: {self.current_drawdown:.2%}")

        # 步骤 2: 检查最大回撤风险并应用
        if self.current_drawdown > self.max_drawdown_pct:
            algorithm.Error(f"[RiskManagement] MAX DRAWDOWN EXCEEDED: Current Drawdown {self.current_drawdown:.2%} > Threshold {self.max_drawdown_pct:.2%}. Liquidating all positions.")
            
            liquidate_targets = []
            for security_holding in algorithm.Portfolio.Values: # Iterate over SecurityHolding objects
                if security_holding.Invested:
                    liquidate_targets.append(PortfolioTarget(security_holding.Symbol, 0))
            return liquidate_targets
        
        return targets



    def _apply_max_holding_time_risk(self, algorithm: QCAlgorithm, targets: List[PortfolioTarget]) -> List[PortfolioTarget]:
        """
        应用最大持仓时间风险控制（按每个 symbol 单独追踪持仓起始时间）
        """
        adjusted_targets = []
        cleared_symbols = set()

        # 更新持仓开始时间 or 清除计时器
        for target in targets:
            symbol = target.Symbol
            quantity = target.Quantity

            if quantity != 0:
                if symbol not in self.holding_start_times:
                    self.holding_start_times[symbol] = algorithm.Time
                    algorithm.Debug(f"[HoldingTime] 记录 {symbol.Value} 的持仓开始时间为 {algorithm.Time}")
            else:
                if symbol in self.holding_start_times:
                    algorithm.Debug(f"[HoldingTime] 清除 {symbol.Value} 的持仓开始时间（因生成平仓信号）")
                    del self.holding_start_times[symbol]

        # 检查每个已投资的 symbol 是否超期
        for symbol, security in algorithm.Portfolio.items():
            if not security.Invested or symbol not in self.holding_start_times:
                continue

            holding_days = (algorithm.Time.date() - self.holding_start_times[symbol].date()).days
            if holding_days > self.max_holding_days:
                algorithm.Debug(f"[RiskManagement] {symbol.Value} 超过最大持仓天数 {self.max_holding_days} 天，持有了 {holding_days} 天，平仓")
                adjusted_targets.append(PortfolioTarget(symbol, 0))
                cleared_symbols.add(symbol)
                del self.holding_start_times[symbol]

        # 将原始 targets 中未被“超期强平”的 symbol 加回去
        filtered_targets = [t for t in targets if t.Symbol not in cleared_symbols]
        
        return adjusted_targets + filtered_targets


