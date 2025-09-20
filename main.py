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
    """贝叶斯协整配对交易策略"""

    def Initialize(self):
        """初始化策略"""
        # 加载配置
        self.config = StrategyConfig()

        # 调试级别: 0=不输出, 1=输出详细统计
        self.debug_level = self.config.main['debug_level']

        # 设置基本参数
        self._setup_basic_parameters()

        # 设置框架模块
        self._setup_framework_modules()

    def _setup_basic_parameters(self):
        """设置基本参数"""
        # 时间范围
        self.SetStartDate(*self.config.main['start_date'])
        self.SetEndDate(*self.config.main['end_date'])
        self.SetCash(self.config.main['cash'])

        # 分辨率和账户
        self.UniverseSettings.Resolution = self.config.main['resolution']
        self.SetBrokerageModel(self.config.main['brokerage_name'], self.config.main['account_type'])

    def _setup_framework_modules(self):
        """设置框架模块"""
        # 选股模块
        self.universe_selector = MyUniverseSelectionModel(
            self,
            self.config.universe_selection,
            self.config.sector_code_to_name,
            self.config.sector_name_to_code
        )
        self.SetUniverseSelection(self.universe_selector)

        # 定期触发选股
        self._setup_schedule()

        # ========== 仅测试选股模块，其他模块暂时注释 ==========
        # # Alpha模块
        # self.SetAlpha(BayesianCointegrationAlphaModel(
        #     self,
        #     self.config.alpha_model,
        #     self.config.sector_code_to_name
        # ))

        # # 仓位构建模块
        # self.SetPortfolioConstruction(BayesianCointegrationPortfolioConstructionModel(
        #     self,
        #     self.config.portfolio_construction
        # ))

        # # 风险管理模块
        # self.risk_manager = BayesianCointegrationRiskManagementModel(
        #     self,
        #     self.config.risk_management
        # )
        # self.SetRiskManagement(self.risk_manager)

        # # 执行模块
        # self.SetExecution(BayesianCointegrationExecutionModel(self))

    def _setup_schedule(self):
        """设置调度任务"""
        date_rule = getattr(self.DateRules, self.config.main['schedule_frequency'])()
        time_rule = self.TimeRules.At(*self.config.main['schedule_time'])
        self.Schedule.On(date_rule, time_rule, Action(self.universe_selector.TriggerSelection))