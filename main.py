# region imports
from AlgorithmImports import *
from src.UniverseSelection import MyUniverseSelectionModel
from System import Action
from src.AlphaModel import BayesianCointegrationAlphaModel
from src.PortfolioConstruction import BayesianCointegrationPortfolioConstructionModel
# from QuantConnect.Algorithm.Framework.Risk import MaximumDrawdownPercentPortfolio, MaximumSectorExposureRiskManagementModel
# from src.RiskManagement import BayesianCointegrationRiskManagementModel
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
            'max_beta_threshold': 3.0        # Beta最大阈值，过滤极大beta的协整对
        }
        
        # PortfolioConstruction 配置
        self.portfolio_construction = {
            'margin_rate': 0.5,
            'cooling_period_days': 7
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
        
        # 设置杠杆提高资金利用率
        self.UniverseSettings.Leverage = 2.0
        
        # 🔥 关键修复：设置自定义证券初始化器以正确配置保证金模型
        self.SetSecurityInitializer(self.CustomSecurityInitializer)
        
        # 调整最小订单限制，解决"minimum order size"警告
        self.Settings.MinimumOrderMarginPortfolioPercentage = 0.001  # 0.1%，从默认0.5%降低

        # 设置UniverseSelection模块
        self.universe_selector = MyUniverseSelectionModel(self, self.config.universe_selection)
        self.SetUniverseSelection(self.universe_selector)
        self.Schedule.On(self.DateRules.MonthStart(), self.TimeRules.At(9, 10), Action(self.universe_selector.TriggerSelection))

        # 设置Alpha模块
        self.SetAlpha(BayesianCointegrationAlphaModel(self, self.config.alpha_model))

        # 测试模式配置：用于验证Alpha信号逻辑
        test_mode = self.GetParameter("test_mode", "false").lower() == "true"
        if test_mode:
            # 使用NullPortfolioConstructionModel直接执行Alpha信号
            from QuantConnect.Algorithm.Framework.Portfolio import NullPortfolioConstructionModel
            self.SetPortfolioConstruction(NullPortfolioConstructionModel())
            self.Debug("[Initialize] 测试模式启用：Alpha信号将直接执行")
        else:
            # 正常模式：使用完整的框架模块
            self.SetPortfolioConstruction(BayesianCointegrationPortfolioConstructionModel(self, self.config.portfolio_construction))
            self.Debug("[Initialize] 正常模式启用：使用PortfolioConstruction模块")

        # # 设置风险管理模块
        # ## 组合层面分控
        # self.AddRiskManagement(MaximumDrawdownPercentPortfolio(0.1))  
        # self.AddRiskManagement(MaximumSectorExposureRiskManagementModel(0.3))
        # ## 资产层面分控
        # self.risk_manager = BayesianCointegrationRiskManagementModel(self)
        # self.AddRiskManagement(self.risk_manager)  
        # self.Schedule.On(self.DateRules.MonthStart(-1), self.TimeRules.At(16, 00), Action(self.risk_manager.IsSelectionOnNextDay))

        # # # # 设置Execution模块
        # # # self.SetExecution(MyExecutionModel(self))
        
        # # # 记录初始化完成
        # # self.Debug(f"[Initialize] 完成, 起始日期: {self.StartDate}")
    
    def CustomSecurityInitializer(self, security):
        """
        自定义证券初始化器，确保保证金机制正确配置
        这是修复保证金效率异常的关键配置
        """
        # 基础设置
        security.SetDataNormalizationMode(DataNormalizationMode.Adjusted)
        
        # 🔥 关键修复：为股票设置适当的保证金模型
        if security.Type == SecurityType.Equity:
            # 使用2倍杠杆的保证金模型（50%保证金要求）
            security.SetMarginModel(SecurityMarginModel(2.0))
            
            # 设置适当的结算模型（保证金账户立即结算）
            security.SetSettlementModel(ImmediateSettlementModel())
            
            self.Debug(f"[保证金配置] {security.Symbol}: 设置2倍杠杆保证金模型")
        
        # 设置手续费模型
        security.SetFeeModel(InteractiveBrokersFeeModel())
        
        # 设置填充模型
        security.SetFillModel(ImmediateFillModel())

       
