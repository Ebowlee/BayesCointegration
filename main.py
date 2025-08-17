# region imports
from AlgorithmImports import *
from System import Action
from src.config import StrategyConfig
from src.UniverseSelection import MyUniverseSelectionModel
from src.alpha import BayesianCointegrationAlphaModel
from src.PortfolioConstruction import BayesianCointegrationPortfolioConstructionModel
from src.RiskManagement import BayesianCointegrationRiskManagementModel
from src.Execution import BayesianCointegrationExecutionModel
# endregion


class BayesianCointegrationStrategy(QCAlgorithm):
    """
    贝叶斯动态协整交易策略  
    
    该策略通过以下步骤运作：
    1. 选股：基于协整关系筛选资产对
    2. 信号生成：利用贝叶斯方法动态更新模型并生成交易信号
    3. 组合构建：根据信号构建投资组合
    4. 风险管理：动态调整持仓和风险暴露
    5. 交易执行：优化订单执行
    """
    
    def Initialize(self):
        """初始化算法、设置参数、注册事件处理程序"""
        # 初始化配置
        self.config = StrategyConfig()
        
        # 设置调试级别说明
        # Level 0: 关闭所有日志输出
        # Level 1: 仅输出关键决策日志（交易执行、风控触发、重要决策）
        # Level 2: 输出详细流程日志（包含筛选过程、评分细节）
        # Level 3: 输出全部调试信息（包含数据处理、计算细节）
        self.debug_level = self.config.main['debug_level']
        
        # 设置基本参数
        self._setup_basic_parameters()
        
        # 设置框架模块
        self._setup_framework_modules()
    
    def _setup_basic_parameters(self):
        """设置基本参数"""
        # 时间和资金
        self.SetStartDate(*self.config.main['start_date'])
        self.SetEndDate(*self.config.main['end_date'])
        self.SetCash(self.config.main['cash'])
        
        # 分辨率和账户
        self.UniverseSettings.Resolution = self.config.main['resolution']
        self.SetBrokerageModel(self.config.main['brokerage_name'], self.config.main['account_type'])
    
    def _setup_framework_modules(self):
        """设置算法框架模块"""
        # UniverseSelection
        self.universe_selector = MyUniverseSelectionModel(
            self, self.config.universe_selection, 
            self.config.sector_code_to_name, self.config.sector_name_to_code
        )
        self.SetUniverseSelection(self.universe_selector)
        
        # 调度
        self._setup_schedule()
        
        # CentralPairManager - 信息中心
        from src.CentralPairManager import CentralPairManager
        self.central_pair_manager = CentralPairManager(self)
        
        # AlphaModel
        self.SetAlpha(BayesianCointegrationAlphaModel(
            self, 
            self.config.alpha_model, 
            self.config.sector_code_to_name,
            self.central_pair_manager  # 传递CPM
        ))
        
        # PortfolioConstruction
        self.SetPortfolioConstruction(BayesianCointegrationPortfolioConstructionModel(
            self,
            self.config.portfolio_construction,
            self.central_pair_manager  # 传递CPM给PC
        ))
        
        # RiskManagement
        self.risk_manager = BayesianCointegrationRiskManagementModel(
            self,
            self.config.risk_management,
            self.central_pair_manager  # 传递CPM给RiskManagement
        )
        self.SetRiskManagement(self.risk_manager)
        
        # Execution
        self.SetExecution(BayesianCointegrationExecutionModel(self))
    
    def _setup_schedule(self):
        """设置调度"""
        date_rule = getattr(self.DateRules, self.config.main['schedule_frequency'])()
        time_rule = self.TimeRules.At(*self.config.main['schedule_time'])
        self.Schedule.On(date_rule, time_rule, Action(self.universe_selector.TriggerSelection))
    
    def OnOrderEvent(self, orderEvent: OrderEvent):
        """
        处理订单事件 - 智能检测配对完成状态
        
        该方法通过分析持仓变化来判断配对的入场/出场是否完成。
        核心逻辑：
        1. 对每个活跃配对，检查两腿的持仓状态
        2. 入场完成：两腿都有持仓且之前未标记entry_time
        3. 出场完成：两腿都无持仓且之前有entry_time
        """
        if orderEvent.Status != OrderStatus.Filled:
            return
        
        # 获取订单信息
        order = self.Transactions.GetOrderById(orderEvent.OrderId)
        if not order:
            return
        
        filled_symbol = order.Symbol
        self.Debug(f"[OOE] {filled_symbol.Value} filled: {orderEvent.FillQuantity} @ {orderEvent.FillPrice}")
        
        # 获取所有活跃配对
        active_pairs = self.central_pair_manager.get_all_active_pairs()
        if not active_pairs:
            return
        
        # 检查每个活跃配对的状态
        for pair_key in active_pairs:
            # pair_key格式: (symbol1, symbol2)
            symbol1_str, symbol2_str = pair_key
            
            # 检查刚成交的订单是否属于这个配对
            if filled_symbol.Value not in [symbol1_str, symbol2_str]:
                continue
            
            # 获取两腿的Symbol对象
            symbol1 = self.Symbol(symbol1_str) if hasattr(self, 'Symbol') else None
            symbol2 = self.Symbol(symbol2_str) if hasattr(self, 'Symbol') else None
            
            # 如果无法获取Symbol对象，尝试从Portfolio直接访问
            holdings1 = None
            holdings2 = None
            
            for kvp in self.Portfolio:
                if kvp.Key.Value == symbol1_str:
                    holdings1 = kvp.Value
                elif kvp.Key.Value == symbol2_str:
                    holdings2 = kvp.Value
            
            if holdings1 is None or holdings2 is None:
                # 无法找到持仓信息，跳过
                continue
            
            # 判断配对状态
            both_have_position = holdings1.Invested and holdings2.Invested
            both_no_position = not holdings1.Invested and not holdings2.Invested
            
            # 获取当前配对的实例信息
            if pair_key in self.central_pair_manager.open_instances:
                instance = self.central_pair_manager.open_instances[pair_key]
                has_entry_time = instance.get('entry_time') is not None
                
                if both_have_position and not has_entry_time:
                    # 入场完成：两腿都有持仓且尚未记录entry_time
                    self.central_pair_manager.on_pair_entry_complete(pair_key, self.Time)
                    self.Debug(f"[OOE] 检测到配对入场完成: {pair_key}")
                    
                elif both_no_position and has_entry_time:
                    # 出场完成：两腿都无持仓且已有entry_time
                    self.central_pair_manager.on_pair_exit_complete(pair_key, self.Time)
                    self.Debug(f"[OOE] 检测到配对出场完成: {pair_key}")