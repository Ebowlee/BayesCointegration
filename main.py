# region imports
from AlgorithmImports import *
from System import Action
from src.config import StrategyConfig
from src.UniverseSelection import SectorBasedUniverseSelection
from src.analysis.DataProcessor import DataProcessor
from src.analysis.CointegrationAnalyzer import CointegrationAnalyzer
from src.analysis.BayesianModeler import BayesianModeler
from src.analysis.PairSelector import PairSelector
from src.Pairs import Pairs
from src.PairsManager import PairsManager
from src.TicketsManager import TicketsManager
from src.risk import RiskManager
from src.execution import ExecutionManager, OrderExecutor, MarginAllocator
from src.trade import TradeAnalyzer

# endregion


class BayesianCointegrationStrategy(QCAlgorithm):
    """基于OnData的贝叶斯协整策略"""

    def Initialize(self):
        """初始化策略"""
        # === 加载参数配置 ===
        self.config = StrategyConfig()
        self.debug_mode = self.config.main['debug_mode']
        self.SetStartDate(*self.config.main['start_date'])
        self.SetEndDate(*self.config.main['end_date'])
        self.SetCash(self.config.main['cash'])
        self.UniverseSettings.Resolution = self.config.main['resolution']
        self.SetBrokerageModel(self.config.main['brokerage_name'], self.config.main['account_type'])

        # === Benchmark symbols列表(需过滤，不参与选股) ===
        self.benchmark_symbols = []
        self.market_benchmark = self.AddEquity("SPY", self.config.main['resolution']).Symbol
        self.benchmark_symbols.append(self.market_benchmark)
        self.SetBenchmark(self.market_benchmark)


        # === 初始化选股模块 ===
        # 选股模块（按26个子行业分组）
        self.universe_selector = SectorBasedUniverseSelection(self)             # 在此处做插拔替换
        self.SetUniverseSelection(self.universe_selector)
        self.symbols = []

        # 选股触发调度器
        date_rule = getattr(self.DateRules, self.config.main['schedule_frequency'])()
        time_rule = self.TimeRules.At(*self.config.main['schedule_time'])
        self.Schedule.On(date_rule, time_rule, Action(self.universe_selector.trigger_selection))

        # === 初始化分析工具 ===
        self.data_processor = DataProcessor(self, self.config.analysis_shared, self.config.data_processor)
        self.cointegration_analyzer = CointegrationAnalyzer(self, self.config.cointegration_analyzer)
        self.pair_selector = PairSelector(self, self.config.analysis_shared,self.config.pair_selector)
        self.bayesian_modeler = BayesianModeler(self, self.config.analysis_shared, self.config.bayesian_modeler)
        self.pairs_manager = PairsManager(self, self.config.pairs_trading)


        # === 初始化状态管理 ===
        self.is_analyzing = False  # 是否正在分析
        self.last_analysis_time = None  # 上次分析时间

        # === 添加VIX指数（用于市场条件检查）===
        vix_config = self.config.risk_management['market_condition']
        self.vix_symbol = self.AddIndex(vix_config['vix_symbol'], vix_config['vix_resolution']).Symbol
        self.benchmark_symbols.append(self.vix_symbol)  # VIX也需过滤

        # === 初始化辅助工具 ===
        self.tickets_manager = TicketsManager(self, self.pairs_manager)
        self.risk_manager = RiskManager(self, self.config, self.pairs_manager)
        self.order_executor = OrderExecutor(self, self.tickets_manager)
        self.margin_allocator = MarginAllocator(self, self.config)
        self.trade_analyzer = TradeAnalyzer(self)
        self.execution_manager = ExecutionManager(self, self.pairs_manager, self.risk_manager, self.tickets_manager, self.order_executor, self.margin_allocator, self.trade_analyzer)

        self.Debug("[Initialize] 策略初始化完成")


    def Debug(self, message: str):
        """统一的Debug输出方法（自动过滤SecurityChanges噪音）"""
        if self.debug_mode:
            # 过滤QC框架自动生成的SecurityChanges日志
            # 特征: "SecurityChanges: Added:" 或 "SecurityChanges: Removed:"
            if "SecurityChanges:" in message:
                return  # 完全过滤，不打印

            QCAlgorithm.Debug(self, message)


    def OnSecuritiesChanged(self, changes: SecurityChanges):
        """处理证券变更事件 - 触发配对分析"""

        # 添加新股票（过滤掉所有benchmark: SPY, VIX等）
        added_count = 0
        for security in changes.AddedSecurities:
            # 过滤掉benchmark symbols
            if security.Symbol in self.benchmark_symbols:
                continue
            if security.Symbol not in self.symbols:
                self.symbols.append(security.Symbol)
                added_count += 1

        # 简化日志: 只打印数量,不打印ticker列表
        if added_count > 0:
            self.Debug(f"[证券变更] 新增{added_count}只股票,触发配对分析")

        # 移除旧股票（过滤掉所有benchmark）
        removed_symbols = [s.Symbol for s in changes.RemovedSecurities
                          if s.Symbol not in self.benchmark_symbols]
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
        cointegration_result = self.cointegration_analyzer.cointegration_procedure(valid_symbols, clean_data)
        raw_pairs = cointegration_result['raw_pairs']

        if not raw_pairs:
            return

        # === 步骤3: 构建PairData字典 ===
        from src.analysis.PairData import PairData
        pair_data_dict = {}
        for pair_info in raw_pairs:
            pair_key = (pair_info['symbol1'], pair_info['symbol2'])
            pair_data_dict[pair_key] = PairData.from_clean_data(pair_info, clean_data)

        # === 步骤4: 质量评估和配对筛选 ===
        selected_pairs = self.pair_selector.selection_procedure(raw_pairs, pair_data_dict, clean_data)

        if not selected_pairs:
            return

        # === 步骤5: 贝叶斯建模（复用PairData字典） ===
        modeling_results = self.bayesian_modeler.modeling_procedure(selected_pairs, pair_data_dict)

        if not modeling_results:
            return

        # === 步骤6: 通过类方法工厂创建Pairs对象 ===
        new_pairs_dict = {}
        for model_result in modeling_results:
            # 使用类方法工厂创建Pairs对象（与PairData.from_clean_data()一致）
            pair = Pairs.from_model_result(self, model_result, self.config.pairs_trading)
            new_pairs_dict[pair.pair_id] = pair

        # === 步骤7: 交给PairsManager管理 ===
        self.pairs_manager.update_pairs(new_pairs_dict)
        self.Debug(f"[配对分析] 完成: 创建{len(new_pairs_dict)}个新配对, 共管理{len(self.pairs_manager.all_pairs)}个配对")
    


    def OnData(self, data: Slice):
        """处理实时数据 - OnData架构的核心"""

        # 如果正在分析，跳过
        if self.is_analyzing:
            return

        # === Portfolio规则cooldown检查（第一道防线） ===
        # Portfolio规则排他性 - 任何规则在cooldown，阻止所有交易
        if self.risk_manager.is_portfolio_in_risk_cooldown():
            # 检查并清理残留持仓(Portfolio风控触发后可能有部分配对平仓失败)
            self.execution_manager.cleanup_remaining_positions()
            return  # 冷却期内完全停止所有交易

        # === Portfolio层面风控检查（最优先） ===
        # Intent Pattern - 返回List[CloseIntent]和触发的规则
        portfolio_intents, triggered_rule = self.risk_manager.check_portfolio_risks()
        if portfolio_intents and triggered_rule:
            # 传递triggered_rule用于激活cooldown
            self.execution_manager.handle_portfolio_risk_intents(
                portfolio_intents, triggered_rule, self.risk_manager
            )
            return  # 触发任何风控后，完全停止所有交易

        # 如果没有可交易配对，跳过
        if not self.pairs_manager.has_tradeable_pairs():
            return

        # 分类获取配对
        pairs_with_position = self.pairs_manager.get_pairs_with_position()
        pairs_without_position = self.pairs_manager.get_pairs_without_position()

        # === Pair层面风控检查 ===
        # 直接循环检查每个配对
        pair_intents = []
        for pair in pairs_with_position.values():
            intent = self.risk_manager.check_pair_risks(pair)
            if intent:
                pair_intents.append(intent)

        if pair_intents:
            # 传递risk_manager用于激活cooldown和清理HWM
            self.execution_manager.handle_pair_risk_intents(pair_intents, self.risk_manager)

        # === 处理正常平仓 ===
        self.execution_manager.handle_normal_close_intents(pairs_with_position, data)

        # === 处理正常开仓 ===
        if pairs_without_position:
            # 市场条件检查（高波动时阻止开仓，但允许平仓）
            if not self.risk_manager.is_safe_to_open_positions():
                return  # 市场高波动，跳过开仓逻辑

            self.execution_manager.handle_normal_open_intents(pairs_without_position, data)


    def OnOrderEvent(self, event):
        """订单事件回调"""
        # 委托给TicketsManager处理
        self.tickets_manager.on_order_event(event)

        # 检查是否有异常配对需要处理
        anomaly_pairs = self.tickets_manager.get_anomaly_pairs()
        if anomaly_pairs:
            for pair_id in anomaly_pairs:
                self.Debug(f"[订单异常] {pair_id} 检测到单腿失败,已标记异常")


    def OnEndOfAlgorithm(self):
        """回测结束时的统计汇总"""
        # 输出所有统计维度的汇总信息（JSON Lines格式）
        self.trade_analyzer.log_summary()
