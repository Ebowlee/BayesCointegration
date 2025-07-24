# region imports
from AlgorithmImports import *
from src.UniverseSelection import MyUniverseSelectionModel
from System import Action
from src.AlphaModel import BayesianCointegrationAlphaModel
from src.PortfolioConstruction import BayesianCointegrationPortfolioConstructionModel
from QuantConnect.Algorithm.Framework.Risk import MaximumDrawdownPercentPortfolio, MaximumSectorExposureRiskManagementModel
from src.RiskManagement import BayesianCointegrationRiskManagementModel
# endregion

class StrategyConfig:
    """
    策略参数统一配置类
    """
    def __init__(self):
        # UniverseSelection 配置
        self.universe_selection = {
            'num_candidates': 30,
            'min_price': 15,
            'min_volume': 2.5e8,
            'min_ipo_days': 1095,
            'max_pe': 50,
            'min_roe': 0.05,
            'max_debt_to_assets': 0.6,
            'max_leverage_ratio': 5
        }
        
        # AlphaModel 配置
        self.alpha_model = {
            'pvalue_threshold': 0.025,
            'correlation_threshold': 0.5,
            'max_symbol_repeats': 1,
            'max_pairs': 4,
            'lookback_period': 252,
            'mcmc_burn_in': 1000,
            'mcmc_draws': 1000,
            'mcmc_chains': 2,
            'entry_threshold': 1.65,
            'exit_threshold': 0.3,
            'upper_limit': 3.0,
            'lower_limit': -3.0,
            'max_volatility_3month': 0.45,
            'volatility_lookback_days': 63,
            'selection_interval_days': 30,  # 选股间隔天数，用于动态贝叶斯更新
            'dynamic_update_enabled': True,  # 是否启用动态贝叶斯更新
            'min_beta_threshold': 0.2,       # Beta最小阈值，过滤极小beta的协整对
            'max_beta_threshold': 3.0,       # Beta最大阈值，过滤极大beta的协整对
            'flat_signal_duration_days': 1,  # 平仓信号有效期（天）
            'entry_signal_duration_days': 2  # 建仓信号有效期（天）
        }
        
        # PortfolioConstruction 配置
        self.portfolio_construction = {
            'margin_rate': 1.0,
            'cooling_period_days': 7
        }
        
        # RiskManagement 配置
        self.risk_management = {
            'trailing_stop_percent': 0.05,
            'max_drawdown_percent': 0.10
        }



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
        # 创建统一配置
        self.config = StrategyConfig()
        
        # 设置回测时间段和初始资金
        self.SetStartDate(2024, 6, 20)
        self.SetEndDate(2024, 9, 20)
        self.SetCash(100000)
        
        # 设置分辨率和账户类型
        self.UniverseSettings.Resolution = Resolution.Daily
        self.SetBrokerageModel(BrokerageName.InteractiveBrokersBrokerage, AccountType.Margin)
        

        # 设置UniverseSelection模块
        self.universe_selector = MyUniverseSelectionModel(self, self.config.universe_selection)
        self.SetUniverseSelection(self.universe_selector)
        self.Schedule.On(self.DateRules.MonthStart(), self.TimeRules.At(9, 10), Action(self.universe_selector.TriggerSelection))

        # 设置Alpha模块
        self.SetAlpha(BayesianCointegrationAlphaModel(self, self.config.alpha_model))

        # 设置PortfolioConstruction模块
        self.SetPortfolioConstruction(BayesianCointegrationPortfolioConstructionModel(self, self.config.portfolio_construction))

        # 设置风险管理模块
        ## 组合层面风控
        self.AddRiskManagement(MaximumDrawdownPercentPortfolio(0.1))  
        self.AddRiskManagement(MaximumSectorExposureRiskManagementModel(0.3))
        ## 资产层面风控
        self.risk_manager = BayesianCointegrationRiskManagementModel(self, self.config.risk_management)
        self.AddRiskManagement(self.risk_manager)

        # # # # 设置Execution模块
        # # # self.SetExecution(MyExecutionModel(self))
        
        # # # 记录初始化完成
        # # self.Debug(f"[Initialize] 完成, 起始日期: {self.StartDate}")
    

       
