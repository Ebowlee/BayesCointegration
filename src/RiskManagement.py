# region imports
from AlgorithmImports import *
from typing import List
# endregion

class BayesianCointegrationRiskManagementModel(RiskManagementModel):
    """
    协整对层面的精细化风险管理
    1. 协整对最大回撤控制(10%)
    2. Trailing Stop跟踪(5%)
    """
    
    def __init__(self, algorithm: QCAlgorithm, config: dict = None):
        super().__init__()
        self.algorithm = algorithm
        
        # 从配置读取参数，如果没有配置则使用默认值
        if config is None:
            config = {}
        
        self.trailing_stop_percent = config.get('trailing_stop_percent', 0.05)  # 5% trailing stop
        self.max_drawdown_percent = config.get('max_drawdown_percent', 0.10)   # 10% 最大回撤
        
        # 跟踪每个股票的最高价
        self.high_water_marks = {}

    def manage_risk(self, algorithm: QCAlgorithm, targets: List[PortfolioTarget]) -> List[PortfolioTarget]:
        """
        主要风险管理入口
        """
        # 1. 从Insights中获取配对关系
        pairs_dict = {}  # {symbol: paired_symbol}
        
        # 遍历当前活跃的insights
        for kvp in algorithm.Insights:
            insight = kvp.Value
            if insight.Tag and "&" in insight.Tag:
                # 解析tag: "symbol1&symbol2|alpha|beta|zscore|num_pairs"
                parts = insight.Tag.split("|")
                if len(parts) >= 1:
                    symbol_pair = parts[0].split("&")
                    if len(symbol_pair) == 2:
                        # 使用algorithm.Symbol创建symbol对象
                        symbol1_str = symbol_pair[0]
                        symbol2_str = symbol_pair[1]
                        
                        # 在targets中查找对应的symbol对象
                        symbol1 = None
                        symbol2 = None
                        for target in targets:
                            if target.symbol.Value == symbol1_str:
                                symbol1 = target.symbol
                            elif target.symbol.Value == symbol2_str:
                                symbol2 = target.symbol
                        
                        if symbol1 and symbol2:
                            pairs_dict[symbol1] = symbol2
                            pairs_dict[symbol2] = symbol1
        
        # 2. 对每个target进行风险检查
        processed_symbols = set()
        final_targets = []
        
        for target in targets:
            if target.symbol in processed_symbols:
                continue
                
            # 检查是否有配对
            if target.symbol in pairs_dict:
                paired_symbol = pairs_dict[target.symbol]
                # 找到配对的target
                paired_target = next((t for t in targets if t.symbol == paired_symbol), None)
                
                if paired_target:
                    # 进行配对风险管理
                    managed_pair = self._manage_pair_risk(algorithm, target, paired_target)
                    final_targets.extend(managed_pair)
                    processed_symbols.add(target.symbol)
                    processed_symbols.add(paired_symbol)
                else:
                    # 配对的另一半不在targets中，单独处理
                    # 这种情况下，可能需要将两个都平仓
                    self.algorithm.Debug(f"[RiskManagement] 警告: {target.symbol.Value}的配对{paired_symbol.Value}不在targets中")
                    final_targets.append(target)
                    processed_symbols.add(target.symbol)
            else:
                # 没有配对信息，单独处理（可能是单边持仓或其他情况）
                final_targets.append(target)
                processed_symbols.add(target.symbol)
        
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


