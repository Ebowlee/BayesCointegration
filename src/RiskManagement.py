# region imports
from AlgorithmImports import *
from typing import List
# endregion

class BayesianCointegrationRiskManagementModel(RiskManagementModel):
    """
    协整对层面的精细化风险管理
    1. 配对完整性检查 - 防止单边暴露
    2. 协整关系失效检查 - 清理失效的跨周期持仓
    3. 协整对最大回撤控制(10%)
    """
    
    def __init__(self, algorithm: QCAlgorithm, config: dict = None, pair_ledger = None):
        super().__init__()
        self.algorithm = algorithm
        self.pair_ledger = pair_ledger
        
        # 从配置读取参数，如果没有配置则使用默认值
        if config is None:
            config = {}
        
        self.max_drawdown_percent = config.get('max_drawdown_percent', 0.10)   # 10% 最大回撤

    def manage_risk(self, algorithm: QCAlgorithm, targets: List[PortfolioTarget]) -> List[PortfolioTarget]:
        """
        主要风险管理入口：基于配对记账簿进行风控
        """
        # 边界检查
        if not self.pair_ledger:
            self.algorithm.Debug("[RiskManagement] 警告: pair_ledger未初始化，跳过风控检查")
            return targets
            
        final_targets = []
        processed_symbols = set()
        
        for target in targets:
            if target.symbol in processed_symbols:
                continue
                
            # 从配对记账簿获取配对信息
            paired_symbol = self.pair_ledger.get_paired_symbol(target.symbol)
            
            # 处理单个目标的风控逻辑
            processed_targets, processed_syms = self._process_single_target(
                algorithm, target, paired_symbol, targets
            )
            final_targets.extend(processed_targets)
            processed_symbols.update(processed_syms)
        
        return final_targets

    def _process_single_target(self, algorithm, target, paired_symbol, targets):
        """
        处理单个目标的风控逻辑
        
        Returns:
            tuple: (processed_targets, processed_symbols)
        """
        if paired_symbol:
            # 有配对信息
            paired_target = next((t for t in targets if t.symbol == paired_symbol), None)
            
            if paired_target:
                # 配对完整，进行回撤检查
                managed_pair = self._manage_pair_risk(algorithm, target, paired_target)
                return managed_pair, {target.symbol, paired_symbol}
            else:
                # 配对缺失一条腿，执行保护性平仓
                self.algorithm.Debug(f"[RiskManagement] 配对缺失: {target.symbol.Value}的配对{paired_symbol.Value}不在targets中，执行保护性平仓")
                return [PortfolioTarget(target.symbol, 0)], {target.symbol}
        else:
            # 没有配对信息（可能是过期的协整对）
            if algorithm.Portfolio[target.symbol].Invested:
                self.algorithm.Debug(f"[RiskManagement] 发现无配对信息的持仓: {target.symbol.Value}，可能是过期协整对，执行平仓")
                return [PortfolioTarget(target.symbol, 0)], {target.symbol}
            else:
                # 非持仓的单边信号，直接忽略
                self.algorithm.Debug(f"[RiskManagement] 忽略无配对信息的信号: {target.symbol.Value}")
                return [], {target.symbol}

    def _manage_pair_risk(self, algorithm, target1, target2):
        """
        协整对层面的风险管理：只检查最大回撤
        """
        # 检查最大回撤
        drawdown1 = self._calculate_drawdown(algorithm.Portfolio[target1.symbol])
        drawdown2 = self._calculate_drawdown(algorithm.Portfolio[target2.symbol])
        
        if drawdown1 > self.max_drawdown_percent or drawdown2 > self.max_drawdown_percent:
            self.algorithm.Debug(f"[RiskManagement] 配对回撤触发清仓: {target1.symbol.Value}({drawdown1:.2%}) - {target2.symbol.Value}({drawdown2:.2%})")
            return [PortfolioTarget(target1.symbol, 0), PortfolioTarget(target2.symbol, 0)]
        
        return [target1, target2]


    def _calculate_drawdown(self, holding):
        """
        计算持仓回撤（支持做多和做空头寸）
        """
        if holding.Quantity == 0:
            return 0
        
        # 获取平均成本和当前价格
        avg_cost = holding.AveragePrice
        current_price = holding.Price
        
        # 边界条件检查
        if avg_cost <= 0 or current_price <= 0:
            return 0
        
        # 做多头寸：价格下跌为回撤
        if holding.Quantity > 0:
            return max(0, (avg_cost - current_price) / avg_cost)
        # 做空头寸：价格上涨为回撤  
        else:
            return max(0, (current_price - avg_cost) / avg_cost)


