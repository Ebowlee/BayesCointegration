# region imports
from AlgorithmImports import *
from System import Action
from src.config import StrategyConfig
from src.UniverseSelection import SectorBasedUniverseSelection
from src.Pairs import Pairs
from src.TicketsManager import TicketsManager
from src.RiskManagement import RiskManager
from src.ExecutionManager import ExecutionManager
# endregion


class BayesianCointegrationStrategy(QCAlgorithm):
    """基于OnData的贝叶斯协整策略"""

    def Initialize(self):
        """初始化策略"""
        # === 加载配置 ===
        self.config = StrategyConfig()
        self.debug_mode = self.config.main['debug_mode']

        # === 设置基本参数 ===
        self.SetStartDate(*self.config.main['start_date'])
        self.SetEndDate(*self.config.main['end_date'])
        self.SetCash(self.config.main['cash'])
        self.UniverseSettings.Resolution = self.config.main['resolution']
        self.SetBrokerageModel(self.config.main['brokerage_name'], self.config.main['account_type'])

        # === 初始化选股模块 ===
        self.universe_selector = SectorBasedUniverseSelection(self)
        self.SetUniverseSelection(self.universe_selector)

        # 定期触发选股（保持原有调度）
        date_rule = getattr(self.DateRules, self.config.main['schedule_frequency'])()
        time_rule = self.TimeRules.At(*self.config.main['schedule_time'])
        self.Schedule.On(date_rule, time_rule, Action(self.universe_selector.trigger_selection))

        # === 初始化分析工具 ===
        from src.analysis.DataProcessor import DataProcessor
        from src.analysis.CointegrationAnalyzer import CointegrationAnalyzer
        from src.analysis.BayesianModeler import BayesianModeler
        from src.analysis.PairSelector import PairSelector
        from src.PairsManager import PairsManager

        self.data_processor = DataProcessor(self, self.config.analysis)
        self.cointegration_analyzer = CointegrationAnalyzer(self, self.config.analysis)
        self.bayesian_modeler = BayesianModeler(self, self.config.analysis)  # 现在内部管理历史后验
        self.pair_selector = PairSelector(self, self.config.analysis)

        # === 初始化配对管理器 ===
        self.pairs_manager = PairsManager(self, self.config.pairs_trading)

        # === 初始化核心数据结构 ===
        self.symbols = []  # 当前选中的股票列表

        # === 初始化状态管理 ===
        self.is_analyzing = False  # 是否正在分析
        self.last_analysis_time = None  # 上次分析时间

        # === 添加市场基准 ===
        self.market_benchmark = self.AddEquity("SPY", Resolution.Daily).Symbol

        # 设置SPY为基准（用于计算Alpha/Beta）
        self.SetBenchmark(self.market_benchmark)

        # === 添加VIX指数（用于市场条件检查）===
        self.vix_symbol = self.AddIndex("VIX", Resolution.Daily).Symbol

        # === 资金管理参数 ===
        self.initial_cash = self.config.main['cash']
        # 注意：cash_buffer现在是动态的，将在OnData中计算
        # 最小投资额是固定的
        self.min_investment = self.initial_cash * self.config.main['min_investment_ratio']

        # === 订单追踪管理器(替代旧的去重机制) ===
        self.tickets_manager = TicketsManager(self, self.pairs_manager)

        # === 初始化风控管理器 ===
        self.risk_manager = RiskManager(self, self.config)

        # === 初始化统一执行器 ===
        self.execution_manager = ExecutionManager(self, self.pairs_manager, self.tickets_manager)

        self.Debug("[Initialize] 策略初始化完成")


    def Debug(self, message: str):
        """统一的Debug输出方法"""
        if self.debug_mode:
            QCAlgorithm.Debug(self, message)


    def OnSecuritiesChanged(self, changes: SecurityChanges):
        """处理证券变更事件 - 触发配对分析"""

        # 添加新股票（过滤掉市场基准）
        for security in changes.AddedSecurities:
            # 过滤掉SPY
            if security.Symbol == self.market_benchmark:
                continue
            if security.Symbol not in self.symbols:
                self.symbols.append(security.Symbol)

        # 移除旧股票（过滤掉市场基准）
        removed_symbols = [s.Symbol for s in changes.RemovedSecurities
                          if s.Symbol != self.market_benchmark]
        self.symbols = [s for s in self.symbols if s not in removed_symbols]

        # === 触发配对分析 ===
        if len(self.symbols) >= 2:
            # 标记正在分析
            self.is_analyzing = True
            self.last_analysis_time = self.Time

            # 执行分析流程
            self._analyze_and_create_pairs()

            # 分析完成
            self.is_analyzing = False



    def _analyze_and_create_pairs(self):
        """执行配对分析流程（步骤1-5）"""

        # === 步骤1: 数据处理 ===
        data_result = self.data_processor.process(self.symbols)
        clean_data = data_result['clean_data']
        valid_symbols = data_result['valid_symbols']

        if len(valid_symbols) < 2:
            return

        # === 步骤2: 协整检验 ===
        cointegration_result = self.cointegration_analyzer.cointegration_procedure(
            valid_symbols,
            clean_data
        )
        raw_pairs = cointegration_result['raw_pairs']

        if not raw_pairs:
            return

        # === 步骤3&4: 质量评估和配对筛选 ===
        selected_pairs = self.pair_selector.selection_procedure(raw_pairs, clean_data)

        if not selected_pairs:
            return

        # === 步骤5: 贝叶斯建模 ===
        modeling_results = self.bayesian_modeler.modeling_procedure(selected_pairs, clean_data)

        if not modeling_results:
            return

        # === 步骤6: 直接创建Pairs对象 ===
        new_pairs_dict = {}
        for model_result in modeling_results:
            # 直接使用model_result创建Pairs对象
            pair = Pairs(self, model_result, self.config.pairs_trading)
            new_pairs_dict[pair.pair_id] = pair

        # === 步骤7: 交给PairsManager管理 ===
        self.pairs_manager.update_pairs(new_pairs_dict)
        self.Debug(f"[配对分析] 完成: 创建{len(new_pairs_dict)}个新配对, 共管理{len(self.pairs_manager.all_pairs)}个配对")
    


    def OnData(self, data: Slice):
        """处理实时数据 - OnData架构的核心"""

        # 如果正在分析，跳过
        if self.is_analyzing:
            return

        # === Portfolio层面风控检查（最优先） ===
        portfolio_action, triggered_rules = self.risk_manager.check_portfolio_risks()
        if portfolio_action:
            self.execution_manager.handle_portfolio_risk_action(portfolio_action, triggered_rules)
            return  # 触发任何风控后，完全停止所有交易

        # 检查是否有任何风控规则在冷却期（统一阻断）
        if self.risk_manager.has_any_rule_in_cooldown():
            return  # 冷却期内完全停止所有交易

        # 如果没有可交易配对，跳过
        if not self.pairs_manager.has_tradeable_pairs():
            return

        # 分类获取配对
        pairs_with_position = self.pairs_manager.get_pairs_with_position()
        pairs_without_position = self.pairs_manager.get_pairs_without_position()

        # === Pair层面风控检查 ===
        pair_risk_actions = self.risk_manager.check_all_pair_risks(pairs_with_position)
        if pair_risk_actions:
            self.execution_manager.handle_pair_risk_actions(pair_risk_actions)

        # === 处理正常平仓 ===
        self.execution_manager.handle_signal_closings(pairs_with_position, data)

        # === 处理正常开仓 ===
        if pairs_without_position:
            # 市场条件检查（高波动时阻止开仓，但允许平仓）
            if not self.risk_manager.is_safe_to_open_positions():
                return  # 市场高波动，跳过开仓逻辑

            self.execution_manager.handle_position_openings(pairs_without_position, data)


    def OnOrderEvent(self, event):
        """订单事件回调"""
        # 委托给TicketsManager处理
        self.tickets_manager.on_order_event(event)

        # 检查是否有异常配对需要处理
        anomaly_pairs = self.tickets_manager.get_anomaly_pairs()
        if anomaly_pairs:
            for pair_id in anomaly_pairs:
                self.Debug(f"[订单异常] {pair_id} 检测到单腿失败,已标记异常")
