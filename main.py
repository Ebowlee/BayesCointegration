# region imports
from AlgorithmImports import *
from src.UniverseSelection import MyUniverseSelectionModel
from System import Action
from QuantConnect.Data.Fundamental import MorningstarSectorCode
from src.AlphaModel import BayesianCointegrationAlphaModel
from src.PairLedger import PairLedger
from src.PortfolioConstruction import BayesianCointegrationPortfolioConstructionModel
from src.CustomRiskManager import CustomRiskManager
# from QuantConnect.Algorithm.Framework.Risk import MaximumDrawdownPercentPortfolio, MaximumSectorExposureRiskManagementModel
# from src.RiskManagement import BayesianCointegrationRiskManagementModel
# endregion
    


class StrategyConfig:
    """
    策略参数统一配置类
    """
    def __init__(self):
        # Main 配置 - main.py中的硬编码参数
        self.main = {
            'start_date': (2024, 6, 20),
            'end_date': (2024, 9, 20),
            'cash': 100000,
            'resolution': Resolution.Daily,
            'brokerage_name': BrokerageName.InteractiveBrokersBrokerage,
            'account_type': AccountType.Margin,
            'schedule_frequency': 'MonthStart',
            'schedule_time': (9, 10),
            'portfolio_max_drawdown': 0.1,
            'portfolio_max_sector_exposure': 0.3
        }
        
        # UniverseSelection 配置
        self.universe_selection = {
            'max_stocks_per_sector': 20,
            'min_price': 10,                                # 降低至10美元，支持中小盘
            'min_volume': 5e6,                              # 改为成交量：1000万股
            'min_days_since_ipo': 1095,                     # 保持3年
            'max_pe': 80,                                   # 已放宽至50，容纳成长股
            'min_roe': 0,                                   # 放宽至0，容纳转型期公司
            'max_debt_ratio': 0.8,                          # 放宽至80%
            'max_leverage_ratio': 8,                        # 放宽至8倍
            'max_volatility': 0.6,                          # 新增：最大年化波动率60%
            'volatility_lookback_days': 252,                # 新增：波动率计算天数
            'volatility_data_completeness_ratio': 0.98      # 新增：波动率计算时要求的最低数据完整性(98%)
        }
        
        # AlphaModel 配置
        self.alpha_model = {
            'pvalue_threshold': 0.05,
            'correlation_threshold': 0.7,
            'max_symbol_repeats': 2,
            'max_pairs': 5,
            'lookback_period': 252,
            'mcmc_warmup_samples': 1000,
            'mcmc_posterior_samples': 1000,
            'mcmc_chains': 2,
            'entry_threshold': 1.2,  
            'exit_threshold': 0.3,  
            'upper_limit': 3.0,  
            'lower_limit': -3.0,
            'flat_signal_duration_days': 1,                 # 平仓信号有效期（天）
            'entry_signal_duration_days': 2,                # 建仓信号有效期（天）
            'min_data_completeness_ratio': 0.98,            # 数据完整性最低要求(98%)
        }
        
        # PortfolioConstruction 配置
        self.portfolio_construction = {
            'margin_rate': 1.0,
            'pair_reentry_cooldown_days': 14,               # 冷却期从7天改为14天
            'max_pairs': 8,                                 # 最大持仓配对数
            'cash_buffer': 0.05                             # 5%现金缓冲
        }
        
        # RiskManagement 配置
        self.risk_management = {
            'max_drawdown_percent': 0.10,
            'max_holding_days': 60  # 最大持仓天数
        }
        
        # 行业映射配置
        self.sector_code_to_name = {
            MorningstarSectorCode.Technology: "Technology",
            MorningstarSectorCode.Healthcare: "Healthcare",
            MorningstarSectorCode.Energy: "Energy",
            MorningstarSectorCode.ConsumerDefensive: "ConsumerDefensive",
            MorningstarSectorCode.ConsumerCyclical: "ConsumerCyclical",
            MorningstarSectorCode.CommunicationServices: "CommunicationServices",
            MorningstarSectorCode.Industrials: "Industrials",
            MorningstarSectorCode.Utilities: "Utilities"
        }
        self.sector_name_to_code = {v: k for k, v in self.sector_code_to_name.items()}



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
        # 初始化配置和记账簿
        self.config = StrategyConfig()
        self.pair_ledger = PairLedger(self)
        
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
        
        # Alpha模块
        self.SetAlpha(BayesianCointegrationAlphaModel(
            self, self.config.alpha_model, self.pair_ledger, self.config.sector_code_to_name
        ))
        
        # PortfolioConstruction模块
        self.SetPortfolioConstruction(BayesianCointegrationPortfolioConstructionModel(
            self, self.config.portfolio_construction, self.pair_ledger
        ))
        
        # 创建自定义风控管理器
        self.custom_risk_manager = CustomRiskManager(
            self, 
            self.config.risk_management, 
            self.pair_ledger
        )
        
        # # 设置RiskManagement模块
        # self.AddRiskManagement(MaximumDrawdownPercentPortfolio(self.config.main['portfolio_max_drawdown']))
        # self.AddRiskManagement(MaximumSectorExposureRiskManagementModel(self.config.main['portfolio_max_sector_exposure']))
        # self.risk_manager = BayesianCointegrationRiskManagementModel(self, self.config.risk_management, self.pair_ledger)
        # self.AddRiskManagement(self.risk_manager)

        # # # # 设置Execution模块
        # # # self.SetExecution(MyExecutionModel(self))
    
    def _setup_schedule(self):
        """设置调度"""
        date_rule = getattr(self.DateRules, self.config.main['schedule_frequency'])()
        time_rule = self.TimeRules.At(*self.config.main['schedule_time'])
        self.Schedule.On(date_rule, time_rule, Action(self.universe_selector.TriggerSelection))
    
    def OnData(self, data):
        """
        每日数据更新时的处理
        
        主要用于转发给自定义风控进行每日检查
        """
        # 转发给自定义风控管理器
        if hasattr(self, 'custom_risk_manager'):
            self.custom_risk_manager.on_data(data)
    
    def OnOrderEvent(self, order_event):
        """
        订单事件处理
        
        主要用于更新PairLedger的持仓状态
        """
        # 转发给自定义风控管理器
        if hasattr(self, 'custom_risk_manager'):
            self.custom_risk_manager.on_order_event(order_event)