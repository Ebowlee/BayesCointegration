# region imports
from AlgorithmImports import *
from typing import List
# endregion

class BayesianCointegrationRiskManagementModel(RiskManagementModel):
    """
    协整对层面的精细化风险管理
    1. 选股日前全部清仓
    2. 协整对最大回撤控制(10%)
    3. Trailing Stop跟踪(5%)
    """
    
    def __init__(self, algorithm: QCAlgorithm):
        super().__init__()
        self.algorithm = algorithm
        self.is_selection_on_next_day = False
        
        # 跟踪每个股票的最高价
        self.high_water_marks = {}
        self.trailing_stop_percent = 0.05  # 5% trailing stop
        self.max_drawdown_percent = 0.10   # 10% 最大回撤

    def manage_risk(self, algorithm: QCAlgorithm, targets: List[PortfolioTarget]) -> List[PortfolioTarget]:
        """
        主要风险管理入口
        """
        # 1. 选股日前清仓
        if self.is_selection_on_next_day:
            self.is_selection_on_next_day = False
            self.algorithm.Debug("[RiskManagement] 选股日前清仓")
            return [PortfolioTarget(target.symbol, 0) for target in targets]
        
        # 2. 将targets按配对分组
        pairs = []
        for i in range(0, len(targets), 2):
            if i + 1 < len(targets):
                pair = (targets[i], targets[i + 1])
                pairs.append(pair)
        
        # 3. 对每个协整对进行风险管理
        final_targets = []
        for pair in pairs:
            target1, target2 = pair
            managed_pair = self._manage_pair_risk(algorithm, target1, target2)
            final_targets.extend(managed_pair)
        
        return final_targets

    def _manage_pair_risk(self, algorithm, target1, target2):
        """
        协整对层面的风险管理
        """
        # 更新最高价
        self._update_high_water_marks(algorithm, target1.symbol)
        self._update_high_water_marks(algorithm, target2.symbol)
        
        # 检查trailing stop
        if self._check_trailing_stop(algorithm, target1.symbol) or self._check_trailing_stop(algorithm, target2.symbol):
            self.algorithm.Debug(f"[RiskManagement] Trailing Stop触发清仓: {target1.symbol.Value} - {target2.symbol.Value}")
            return [PortfolioTarget(target1.symbol, 0), PortfolioTarget(target2.symbol, 0)]
        
        # 检查最大回撤
        drawdown1 = self._calculate_drawdown(algorithm.Portfolio[target1.symbol])
        drawdown2 = self._calculate_drawdown(algorithm.Portfolio[target2.symbol])
        
        if drawdown1 > self.max_drawdown_percent or drawdown2 > self.max_drawdown_percent:
            self.algorithm.Debug(f"[RiskManagement] 配对回撤触发清仓: {target1.symbol.Value}({drawdown1:.2%}) - {target2.symbol.Value}({drawdown2:.2%})")
            return [PortfolioTarget(target1.symbol, 0), PortfolioTarget(target2.symbol, 0)]
        
        return [target1, target2]



    def _update_high_water_marks(self, algorithm, symbol):
        """
        更新最高价
        """
        if symbol not in self.high_water_marks:
            self.high_water_marks[symbol] = algorithm.Securities[symbol].Price
        else:
            current_price = algorithm.Securities[symbol].Price
            self.high_water_marks[symbol] = max(self.high_water_marks[symbol], current_price)

    def _check_trailing_stop(self, algorithm, symbol):
        """
        检查是否触发trailing stop
        """
        if symbol not in self.high_water_marks:
            return False
        
        current_price = algorithm.Securities[symbol].Price
        high_water = self.high_water_marks[symbol]
        
        # 从最高价下跌超过trailing_stop_percent
        return (high_water - current_price) / high_water > self.trailing_stop_percent



    def _calculate_drawdown(self, holding):
        """
        计算持仓回撤
        """
        if holding.Quantity == 0:
            return 0
        
        # 使用平均成本计算回撤
        avg_cost = holding.AveragePrice
        current_price = holding.Price
        
        if avg_cost > current_price:
            return (avg_cost - current_price) / avg_cost
        else:
            return 0



    def IsSelectionOnNextDay(self):
        """
        外部调用的接口：设置选股日标志
        """
        self.is_selection_on_next_day = True
        self.algorithm.Debug("[RiskManagement] - IsSelectionOnNextDay: True") 
        