# region imports
from AlgorithmImports import *
from src.UniverseSelection import MyUniverseSelectionModel
from System import Action
# from src.CentralPairManager import CentralPairManager  # 暂时注释，后续重构时启用
from src.config import StrategyConfig
# endregion

# ==============================================================================================
#                                    主策略类
# ==============================================================================================

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
    
    # ----------------------------------------------------------------------------------------------
    #                                    初始化方法
    # ----------------------------------------------------------------------------------------------
    
    def Initialize(self):
        """
        初始化算法、设置参数、注册事件处理程序
        """
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
    
    # ----------------------------------------------------------------------------------------------
    #                                    配置方法
    # ----------------------------------------------------------------------------------------------
    
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
        
        # CentralPairManager - 核心组件（待重构）
        # self.central_pair_manager = CentralPairManager(self, self.config.central_pair_manager)
        self.central_pair_manager = None  # 暂时设为None，后续重构
        
        # 设置Null模块以验证选股功能
        # 后续会逐步重构并启用真实模块
        
        # AlphaModel - 使用重构后的贝叶斯协整Alpha模型
        from src.alpha import BayesianCointegrationAlphaModel
        self.SetAlpha(BayesianCointegrationAlphaModel(
            self, 
            self.config.alpha_model, 
            self.config.sector_code_to_name
        ))
        
        # PortfolioConstruction - 暂时使用Null（待重构）
        from QuantConnect.Algorithm.Framework.Portfolio import NullPortfolioConstructionModel
        self.SetPortfolioConstruction(NullPortfolioConstructionModel())
        
        # RiskManagement - 暂时使用Null（待重构）
        from QuantConnect.Algorithm.Framework.Risk import NullRiskManagementModel
        self.SetRiskManagement(NullRiskManagementModel())
        
        # TODO: 框架模块重构计划
        # 阶段4: PortfolioConstruction - 完全基于CPM
        # 阶段5: RiskManagement - 移除OrderTracker依赖
        # 阶段6: OnOrderEvent增强 - 基于CPM的订单处理
    
    # ----------------------------------------------------------------------------------------------
    #                                    调度设置
    # ----------------------------------------------------------------------------------------------
    
    def _setup_schedule(self):
        """设置调度"""
        date_rule = getattr(self.DateRules, self.config.main['schedule_frequency'])()
        time_rule = self.TimeRules.At(*self.config.main['schedule_time'])
        self.Schedule.On(date_rule, time_rule, Action(self.universe_selector.TriggerSelection))
    
    # ----------------------------------------------------------------------------------------------
    #                                    事件处理
    # ----------------------------------------------------------------------------------------------
    
    def OnOrderEvent(self, orderEvent: OrderEvent):
        """
        处理订单事件 - 简化版
        
        第一阶段：仅记录订单成交事件
        后续阶段：根据CPM增强需求逐步完善
        """
        if orderEvent.Status == OrderStatus.Filled:
            order = self.Transactions.GetOrderById(orderEvent.OrderId)
            if order:
                self.Debug(f"[Order] {order.Symbol.Value} filled: {orderEvent.FillQuantity} @ {orderEvent.FillPrice}")
                
                # TODO: 阶段6实现
                # 1. 从CPM获取配对关系
                # 2. 检查配对双边成交状态
                # 3. 更新CPM状态（register_entry/exit）