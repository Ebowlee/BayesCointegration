# region imports
from AlgorithmImports import *
from datetime import timedelta
from src.UniverseSelection import MyUniverseSelectionModel
from src.AlphaModel import BayesianCointegrationAlphaModel
from src.PortfolioConstruction import BayesianCointegrationPortfolioConstructionModel
from src.RiskManagement import BayesianCointegrationRiskManagementModel
from src.Execution import MyExecutionModel
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
        """
        初始化算法、设置参数、注册事件处理程序
        """
        # 设置回测时间段和初始资金
        self.SetStartDate(2024, 6, 20)
        self.SetEndDate(2024, 6, 30)
        self.SetCash(100000)
        
        # 添加基准ETF和设置分辨率
        self.AddEquity("SPY", Resolution.Daily)
        self.UniverseSettings.Resolution = Resolution.Daily

        self.SetBrokerageModel(BrokerageName.InteractiveBrokersBrokerage, AccountType.Margin)
        self.UniverseSettings.ExtendedMarketHours = False
        
        # 设置UniverseSelection模块
        self.universeSelectionModel = MyUniverseSelectionModel(self)
        self.SetUniverseSelection(self.universeSelectionModel)

        # 在主函数中添加 Schedule.On 控制选股频率
        self.Schedule.On(self.DateRules.MonthStart(), self.TimeRules.AfterMarketOpen('SPY', 30), self.universeSelectionModel.RebalanceUniverse)

        # 设置Alpha模块
        self.alphaModel = BayesianCointegrationAlphaModel(self)
        self.SetAlpha(self.alphaModel)

        # 设置投资组合构建模块
        self.SetPortfolioConstruction(BayesianCointegrationPortfolioConstructionModel(self))

        # 设置风险管理模块
        self.SetRiskManagement(BayesianCointegrationRiskManagementModel(self))

        # 设置Execution模块
        self.SetExecution(MyExecutionModel(self))
        
        # 记录初始化完成
        self.Debug(f"[Initialize] 完成, 起始日期: {self.StartDate}")

       
