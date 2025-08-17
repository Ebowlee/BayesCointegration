# region imports
from AlgorithmImports import *
# endregion


class BayesianCointegrationExecutionModel(ExecutionModel):
    """
    贝叶斯协整策略执行模型 - 极简权重执行
    
    该模型负责将RiskManagement输出的权重目标转换为实际订单。
    采用极简设计，专注于执行，不管理状态。
    
    核心功能:
    1. 接收PortfolioTarget.Percent格式的权重目标
    2. 使用SetHoldings自动处理权重到股数的转换
    3. 过滤微小调整，避免频繁交易
    
    工作流程:
    1. 接收targets（权重百分比）
    2. 计算当前权重与目标权重的差异
    3. 对显著差异执行调仓
    4. 记录执行日志
    
    注意事项:
    - target.Quantity是权重百分比（0.05 = 5%仓位）
    - 负值表示做空（-0.03 = -3%仓位）
    - SetHoldings会自动处理滑点和手续费
    """
    
    def __init__(self, algorithm):
        """
        初始化执行模型
        
        Args:
            algorithm: QuantConnect算法实例
        """
        super().__init__()
        self.algorithm = algorithm
        self.min_weight_change = 0.001  # 最小权重变化阈值（0.1%）
        self.algorithm.Debug("[Execution] 初始化完成")
    
    def Execute(self, algorithm: QCAlgorithm, targets: List[PortfolioTarget]):
        """
        执行权重调整
        
        该方法将权重目标转换为实际交易订单。
        使用SetHoldings确保精确的权重控制。
        
        Args:
            algorithm: 算法实例
            targets: 权重目标列表，每个target的Quantity是权重百分比
        """
        if len(targets) == 0:
            return
        
        self.algorithm.Debug(f"[Execution] 收到{len(targets)}个权重目标")
        
        # 获取当前组合总价值
        total_value = self.algorithm.Portfolio.TotalPortfolioValue
        if total_value <= 0:
            self.algorithm.Debug("[Execution] 组合价值为0，跳过执行")
            return
        
        # 处理每个target
        executed_count = 0
        for target in targets:
            if self._execute_target(target, total_value):
                executed_count += 1
        
        if executed_count > 0:
            self.algorithm.Debug(f"[Execution] 执行了{executed_count}个权重调整")
    
    def _execute_target(self, target: PortfolioTarget, total_value: float) -> bool:
        """
        执行单个权重目标
        
        Args:
            target: 权重目标
            total_value: 组合总价值
            
        Returns:
            bool: 是否执行了交易
        """
        symbol = target.Symbol
        target_weight = target.Quantity  # 目标权重（百分比）
        
        # 获取当前持仓权重
        holding = self.algorithm.Portfolio[symbol]
        current_value = holding.HoldingsValue
        current_weight = current_value / total_value if total_value > 0 else 0
        
        # 计算权重差异
        weight_change = target_weight - current_weight
        
        # 过滤微小调整
        if abs(weight_change) < self.min_weight_change:
            return False
        
        # 检查是否可交易
        security = self.algorithm.Securities[symbol]
        if not security.IsTradable:
            self.algorithm.Debug(f"[Execution] {symbol.Value} 不可交易，跳过")
            return False
        
        # 使用SetHoldings执行权重调整
        # SetHoldings会自动：
        # 1. 计算需要的股数
        # 2. 考虑当前持仓
        # 3. 下市价单
        # 4. 处理做空情况
        try:
            self.algorithm.SetHoldings(symbol, target_weight)
            
            # 记录执行日志
            action = "建仓" if not holding.Invested else ("平仓" if target_weight == 0 else "调仓")
            self.algorithm.Debug(
                f"[Execution] {action}: {symbol.Value} "
                f"权重 {current_weight:.2%} → {target_weight:.2%}"
            )
            
            return True
            
        except Exception as e:
            self.algorithm.Error(f"[Execution] 执行失败 {symbol.Value}: {str(e)}")
            return False