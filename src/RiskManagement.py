# region imports
from AlgorithmImports import *
from typing import List, Tuple, Set, Optional, Dict
# endregion

class BayesianCointegrationRiskManagementModel(RiskManagementModel):
    """
    协整对层面的精细化风险管理
    
    主要功能:
    1. 每个Resolution步（Daily）自动检查所有持仓
    2. 持仓时间限制(60天)
    3. 止损控制(10%)
    4. 可选止盈控制
    
    重要：ManageRisk方法会在每个时间步被框架调用，
    即使targets为空也会调用，从而实现持续风控监控。
    """
    
    # 日志消息常量
    LOG_NO_LEDGER = "[RiskManagement] 警告: pair_ledger未初始化，跳过风控检查"
    LOG_HOLDING_EXPIRED = "[RiskManagement] TIMEOUT: {0}-{1} 持仓{2}天，超过{3}天限制"
    LOG_STOP_LOSS = "[RiskManagement] STOP_LOSS: {0}-{1} 亏损{2:.2%}，触发止损线{3:.0%}"
    LOG_TAKE_PROFIT = "[RiskManagement] TAKE_PROFIT: {0}-{1} 盈利{2:.2%}，触发止盈线{3:.0%}"
    
    def __init__(self, algorithm: QCAlgorithm, config: dict = None, pair_ledger = None):
        super().__init__()
        self.algorithm = algorithm
        self.pair_ledger = pair_ledger
        
        # 从配置读取参数，如果没有配置则使用默认值
        config = config or {}
        self.max_holding_days = config.get('max_holding_days', 60)              # 60天最大持仓时间
        self.max_loss_percent = config.get('max_loss_percent', 0.10)           # 10% 最大亏损
        self.max_profit_percent = config.get('max_profit_percent', 0.30)        # 30% 最大盈利
        self.enable_take_profit = config.get('enable_take_profit', False)      # 是否启用止盈
        
        # 统计信息
        self.risk_triggers = {
            'TIMEOUT': 0,
            'STOP_LOSS': 0,
            'TAKE_PROFIT': 0
        }

    def ManageRisk(self, algorithm: QCAlgorithm, targets: List[PortfolioTarget]) -> List[PortfolioTarget]:
        """
        主要风险管理入口 - 每个Resolution步都会被调用
        
        重要：即使targets为空，这个方法也会被框架调用。
        这使得我们可以持续监控所有持仓的风险状态。
        
        Args:
            algorithm: 主算法实例
            targets: 当前的PortfolioTarget列表（可能为空）
            
        Returns:
            调整后的targets列表
        """
        # 早期返回：边界检查
        if not self.pair_ledger:
            self.algorithm.Debug(self.LOG_NO_LEDGER)
            return targets
        
        # 步骤1: 获取所有持仓配对的风控数据
        risk_data = self.pair_ledger.get_risk_control_data()
        
        # 步骤2: 检查每个持仓配对的风控条件
        for data in risk_data:
            pair_info = data['pair_info']
            
            # 检查风控条件
            should_close = False
            risk_type = None
            
            # 持仓超时
            if data['holding_days'] > self.max_holding_days:
                should_close = True
                risk_type = 'TIMEOUT'
                self.algorithm.Debug(self.LOG_HOLDING_EXPIRED.format(
                    pair_info.symbol1.Value, pair_info.symbol2.Value,
                    data['holding_days'], self.max_holding_days
                ))
                self.risk_triggers['TIMEOUT'] += 1
            
            # 止损
            elif data['total_pnl_percent'] < -self.max_loss_percent:
                should_close = True
                risk_type = 'STOP_LOSS'
                self.algorithm.Debug(self.LOG_STOP_LOSS.format(
                    pair_info.symbol1.Value, pair_info.symbol2.Value,
                    data['total_pnl_percent'], -self.max_loss_percent
                ))
                self.risk_triggers['STOP_LOSS'] += 1
            
            # 止盈（可选）
            elif self.enable_take_profit and data['total_pnl_percent'] > self.max_profit_percent:
                should_close = True
                risk_type = 'TAKE_PROFIT'
                self.algorithm.Debug(self.LOG_TAKE_PROFIT.format(
                    pair_info.symbol1.Value, pair_info.symbol2.Value,
                    data['total_pnl_percent'], self.max_profit_percent
                ))
                self.risk_triggers['TAKE_PROFIT'] += 1
            
            # 生成平仓target
            if should_close:
                # 添加平仓targets
                symbol1, symbol2 = data['pair']
                targets.append(PortfolioTarget(symbol1, 0))
                targets.append(PortfolioTarget(symbol2, 0))
        
        return targets

    def get_statistics(self) -> Dict:
        """
        获取风控统计信息
        
        Returns:
            Dict: 各类风控触发次数统计
        """
        return self.risk_triggers.copy()