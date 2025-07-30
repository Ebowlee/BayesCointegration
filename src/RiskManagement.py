# region imports
from AlgorithmImports import *
from typing import List, Tuple, Set, Optional
# endregion

class BayesianCointegrationRiskManagementModel(RiskManagementModel):
    """
    协整对层面的精细化风险管理
    1. 配对完整性检查 - 防止单边暴露
    2. 协整关系失效检查 - 清理失效的跨周期持仓
    3. 协整对最大回撤控制(10%)
    4. 持仓时间限制(60天)
    """
    
    # 日志消息常量
    LOG_NO_LEDGER = "[RiskManagement] 警告: pair_ledger未初始化，跳过风控检查"
    LOG_MISSING_PAIR = "[RiskManagement] 配对缺失: {0}的配对{1}不在targets中，执行保护性平仓"
    LOG_NO_PAIR_INFO = "[RiskManagement] 发现无配对信息的持仓: {0}，可能是过期协整对，执行平仓"
    LOG_IGNORE_SIGNAL = "[RiskManagement] 忽略无配对信息的信号: {0}"
    LOG_HOLDING_EXPIRED = "[RiskManagement] 持仓超期清仓: {0}-{1} (已持有{2}天)"
    LOG_DRAWDOWN_TRIGGERED = "[RiskManagement] 配对回撤触发清仓: {0}({1:.2%}) - {2}({3:.2%})"
    
    def __init__(self, algorithm: QCAlgorithm, config: dict = None, pair_ledger = None):
        super().__init__()
        self.algorithm = algorithm
        self.pair_ledger = pair_ledger
        
        # 从配置读取参数，如果没有配置则使用默认值
        config = config or {}
        self.max_drawdown_percent = config.get('max_drawdown_percent', 0.10)   # 10% 最大回撤
        self.max_holding_days = config.get('max_holding_days', 60)              # 60天最大持仓时间

    def manage_risk(self, algorithm: QCAlgorithm, targets: List[PortfolioTarget]) -> List[PortfolioTarget]:
        """
        主要风险管理入口：基于配对记账簿进行风控
        """
        # 早期返回：边界检查
        if not self.pair_ledger:
            self.algorithm.Debug(self.LOG_NO_LEDGER)
            return targets
        
        # 创建目标字典以快速查找
        target_dict = {t.symbol: t for t in targets}
        
        # 处理所有目标
        final_targets = []
        processed_symbols = set()
        
        for target in targets:
            if target.symbol in processed_symbols:
                continue
            
            # 处理配对风控
            pair_targets, pair_symbols = self._process_pair(algorithm, target, target_dict)
            final_targets.extend(pair_targets)
            processed_symbols.update(pair_symbols)
        
        return final_targets

    def _process_pair(self, algorithm: QCAlgorithm, target: PortfolioTarget, 
                     target_dict: dict) -> Tuple[List[PortfolioTarget], Set]:
        """
        处理单个目标及其配对的风控逻辑
        
        Returns:
            tuple: (处理后的目标列表, 已处理的符号集合)
        """
        paired_symbol = self.pair_ledger.get_paired_symbol(target.symbol)
        
        # 情况1：有配对信息
        if paired_symbol:
            return self._handle_paired_target(algorithm, target, paired_symbol, target_dict)
        
        # 情况2：无配对信息
        return self._handle_unpaired_target(algorithm, target)
    
    def _handle_paired_target(self, algorithm: QCAlgorithm, target: PortfolioTarget, 
                             paired_symbol: Symbol, target_dict: dict) -> Tuple[List[PortfolioTarget], Set]:
        """处理有配对信息的目标"""
        paired_target = target_dict.get(paired_symbol)
        
        if paired_target:
            # 配对完整，进行风险检查
            managed_targets = self._check_pair_risks(algorithm, target, paired_target)
            return managed_targets, {target.symbol, paired_symbol}
        else:
            # 配对缺失一条腿，执行保护性平仓
            self.algorithm.Debug(self.LOG_MISSING_PAIR.format(target.symbol.Value, paired_symbol.Value))
            return self._create_liquidation_targets(target.symbol), {target.symbol}
    
    def _handle_unpaired_target(self, algorithm: QCAlgorithm, target: PortfolioTarget) -> Tuple[List[PortfolioTarget], Set]:
        """处理无配对信息的目标"""
        if algorithm.Portfolio[target.symbol].Invested:
            # 持仓但无配对信息，可能是过期协整对
            self.algorithm.Debug(self.LOG_NO_PAIR_INFO.format(target.symbol.Value))
            return self._create_liquidation_targets(target.symbol), {target.symbol}
        else:
            # 非持仓的单边信号，忽略
            self.algorithm.Debug(self.LOG_IGNORE_SIGNAL.format(target.symbol.Value))
            return [], {target.symbol}

    def _check_pair_risks(self, algorithm: QCAlgorithm, target1: PortfolioTarget, 
                         target2: PortfolioTarget) -> List[PortfolioTarget]:
        """
        检查配对的所有风险因素
        """
        # 检查持仓时间
        if self._is_holding_expired(algorithm, target1.symbol, target2.symbol):
            return self._create_liquidation_targets(target1.symbol, target2.symbol)
        
        # 检查回撤
        if self._is_drawdown_exceeded(algorithm, target1.symbol, target2.symbol):
            return self._create_liquidation_targets(target1.symbol, target2.symbol)
        
        # 所有检查通过
        return [target1, target2]
    
    def _is_holding_expired(self, algorithm: QCAlgorithm, symbol1: Symbol, symbol2: Symbol) -> bool:
        """检查持仓是否超期"""
        pair_info = self.pair_ledger.get_pair_info(symbol1, symbol2)
        if pair_info and pair_info.entry_time:
            holding_days = (algorithm.Time - pair_info.entry_time).days
            if holding_days > self.max_holding_days:
                self.algorithm.Debug(self.LOG_HOLDING_EXPIRED.format(
                    symbol1.Value, symbol2.Value, holding_days))
                return True
        return False
    
    def _is_drawdown_exceeded(self, algorithm: QCAlgorithm, symbol1: Symbol, symbol2: Symbol) -> bool:
        """检查是否超过最大回撤"""
        drawdown1 = self._calculate_drawdown(algorithm.Portfolio[symbol1])
        drawdown2 = self._calculate_drawdown(algorithm.Portfolio[symbol2])
        
        if drawdown1 > self.max_drawdown_percent or drawdown2 > self.max_drawdown_percent:
            self.algorithm.Debug(self.LOG_DRAWDOWN_TRIGGERED.format(
                symbol1.Value, drawdown1, symbol2.Value, drawdown2))
            return True
        return False

    def _calculate_drawdown(self, holding) -> float:
        """
        计算持仓回撤（支持做多和做空头寸）
        """
        if holding.Quantity == 0 or holding.AveragePrice <= 0 or holding.Price <= 0:
            return 0
        
        # 计算回撤：多头看跌幅，空头看涨幅
        price_ratio = holding.Price / holding.AveragePrice
        if holding.Quantity > 0:
            return max(0, 1 - price_ratio)
        else:
            return max(0, price_ratio - 1)
    
    def _create_liquidation_targets(self, *symbols: Symbol) -> List[PortfolioTarget]:
        """创建平仓目标"""
        return [PortfolioTarget(symbol, 0) for symbol in symbols]