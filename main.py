# region imports
from AlgorithmImports import *
from System import Action
from src.config import StrategyConfig
from src.UniverseSelection import SectorBasedUniverseSelection
from src.Pairs import Pairs, TradingSignal, OrderAction
from src.TicketsManager import TicketsManager
from src.RiskManagement import RiskManager
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

        # === 资金管理参数 ===
        self.initial_cash = self.config.main['cash']
        # 注意：cash_buffer现在是动态的，将在OnData中计算
        # 最小投资额是固定的
        self.min_investment = self.initial_cash * self.config.main['min_investment_ratio']

        # === 订单追踪管理器(替代旧的去重机制) ===
        self.tickets_manager = TicketsManager(self, self.pairs_manager)

        # === 初始化风控管理器 ===
        self.risk_manager = RiskManager(self, self.config)

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
            clean_data,
            self.config.sector_code_to_name
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
            self._handle_portfolio_risk_action(portfolio_action, triggered_rules)
            return  # 触发风控后，阻断所有后续交易逻辑

        # 检查是否有规则在冷却期（即使没有新触发，也要阻止交易）
        if self.risk_manager.has_any_rule_in_cooldown():
            return  # 冷却期内阻断所有交易

        # 如果没有可交易配对，跳过
        if not self.pairs_manager.has_tradeable_pairs():
            return

        # 分类获取配对
        pairs_with_position = self.pairs_manager.get_pairs_with_position()
        pairs_without_position = self.pairs_manager.get_pairs_without_position()

        # === 处理有持仓配对（平仓）===
        for pair in pairs_with_position.values():
            # 订单锁定检查
            if self.tickets_manager.is_pair_locked(pair.pair_id):
                continue

            # 获取交易信号
            signal = pair.get_signal(data)

            # 处理平仓信号
            if signal == TradingSignal.CLOSE:
                self.Debug(f"[平仓] {pair.pair_id} Z-score回归")
                tickets = pair.close_position()
                if tickets:
                    self.tickets_manager.register_tickets(pair.pair_id, tickets, OrderAction.CLOSE)

            elif signal == TradingSignal.STOP_LOSS:
                self.Debug(f"[止损] {pair.pair_id} Z-score超限")
                tickets = pair.close_position()
                if tickets:
                    self.tickets_manager.register_tickets(pair.pair_id, tickets, OrderAction.CLOSE)

        # === 处理无持仓配对（开仓）===
        if pairs_without_position:
            # 获取所有开仓候选（已按质量降序）
            entry_candidates = self.pairs_manager.get_sequenced_entry_candidates(data)

            if entry_candidates:
                # 使用95%的MarginRemaining,保留5%作为动态缓冲
                initial_margin = self.Portfolio.MarginRemaining * self.config.pairs_trading['margin_usage_ratio']
                buffer = self.Portfolio.MarginRemaining * (1 - self.config.pairs_trading['margin_usage_ratio'])

                # 检查是否低于最小阈值
                if initial_margin >= self.min_investment:
                    # 提取候选信息 (entry_candidates已按质量降序)
                    planned_allocations = {}  # {pair_id: pct}
                    opening_signals = {}      # {pair_id: signal}

                    for pair, signal, _, pct in entry_candidates:
                        planned_allocations[pair.pair_id] = pct
                        opening_signals[pair.pair_id] = signal

                    # === 逐个开仓 (动态缩放保持公平) ===
                    actual_opened = 0
                    for pair_id, pct in planned_allocations.items():
                        pair = self.pairs_manager.get_pair_by_id(pair_id)
                        signal = opening_signals[pair_id]

                        # 动态缩放计算
                        current_margin = (self.Portfolio.MarginRemaining - buffer) * pct
                        if current_margin <= 0:
                            break

                        scale_factor = initial_margin / (self.Portfolio.MarginRemaining - buffer)
                        margin_allocated = current_margin * scale_factor  # = initial_margin * pct

                        # 检查1: 订单锁定检查
                        if self.tickets_manager.is_pair_locked(pair_id):
                            continue

                        # 检查2: 最小投资额
                        if margin_allocated < self.min_investment:
                            continue

                        # 检查3: 保证金是否仍足够
                        available_for_check = self.Portfolio.MarginRemaining - buffer
                        if margin_allocated > available_for_check:
                            self.Debug(f"[开仓失败] {pair_id} 保证金不足: 需要${margin_allocated:,.0f}, 可用${available_for_check:,.0f}")
                            continue

                        # 执行开仓并注册订单追踪
                        tickets = pair.open_position(signal, margin_allocated, data)
                        if tickets:
                            self.tickets_manager.register_tickets(pair_id, tickets, OrderAction.OPEN)
                            actual_opened += 1

                    # 执行结果
                    if actual_opened > 0:
                        self.Debug(f"[开仓] 成功开仓{actual_opened}/{len(entry_candidates)}个配对")


    def OnOrderEvent(self, event):
        """订单事件回调"""
        # 委托给TicketsManager处理
        self.tickets_manager.on_order_event(event)

        # 检查是否有异常配对需要处理
        anomaly_pairs = self.tickets_manager.get_anomaly_pairs()
        if anomaly_pairs:
            for pair_id in anomaly_pairs:
                self.Debug(f"[订单异常] {pair_id} 检测到单腿失败,已标记异常")


    def _handle_portfolio_risk_action(self, action: str, triggered_rules: list):
        """
        处理Portfolio层面风控动作

        Args:
            action: 风控动作 ('portfolio_liquidate_all'等)
            triggered_rules: 触发的规则列表 [(rule, description), ...]

        执行流程:
        1. 记录所有触发规则的详细日志
        2. 根据action字符串分发到具体处理方法
        3. 激活所有触发规则的冷却期

        注意:
        - 当前只实现'portfolio_liquidate_all'（全部清仓）
        - 其他action作为占位，未来实现
        """
        # 记录所有触发的规则
        for rule, description in triggered_rules:
            self.Debug(f"[风控触发] {rule.__class__.__name__}: {description}")

        # 根据action执行相应操作
        if action == 'portfolio_liquidate_all':
            self._liquidate_all_positions()

        elif action == 'portfolio_stop_new_entries':
            # 未来实现：设置标志位，禁止开新仓
            self.Debug(f"[风控] 停止开新仓模式（暂未实现）")

        elif action == 'portfolio_reduce_exposure_50':
            # 未来实现：减仓50%
            self.Debug(f"[风控] 减仓50%模式（暂未实现）")

        elif action == 'portfolio_rebalance_sectors':
            # 未来实现：行业再平衡
            self.Debug(f"[风控] 行业再平衡模式（暂未实现）")

        else:
            self.Debug(f"[风控] 未知动作: {action}")

        # 激活所有触发规则的冷却期
        for rule, _ in triggered_rules:
            rule.activate_cooldown()
            self.Debug(f"[风控] {rule.__class__.__name__} 冷却至 {rule.cooldown_until}")


    def _liquidate_all_positions(self):
        """
        清空所有持仓（仅通过pairs_manager，不使用Liquidate）

        执行流程:
        1. 获取所有有持仓的配对
        2. 检查订单锁定状态（is_pair_locked）
        3. 通过pair.close_position()平仓（保持订单追踪）
        4. 注册订单到tickets_manager
        5. 记录详细日志

        设计决策:
        - 不使用QC的Liquidate()方法，原因：
          1. 绕过pair.close_position()
          2. 绕过tickets_manager.register_tickets()
          3. 破坏订单追踪体系
          4. 可能导致重复下单
        - 只通过pairs_manager管理的配对进行平仓
        - 保持TicketsManager的订单追踪完整性
        """
        self.Debug(f"[风控清仓] 开始清空所有持仓...")

        # 获取所有有持仓的配对
        pairs_with_position = self.pairs_manager.get_pairs_with_position()

        if not pairs_with_position:
            self.Debug(f"[风控清仓] 无持仓，跳过")
            return

        closed_count = 0
        for pair in pairs_with_position.values():
            # 订单锁定检查（防止重复下单）
            if self.tickets_manager.is_pair_locked(pair.pair_id):
                self.Debug(f"[风控清仓] {pair.pair_id} 订单处理中,跳过")
                continue

            # 通过pair平仓（保持订单追踪）
            tickets = pair.close_position()
            if tickets:
                self.tickets_manager.register_tickets(pair.pair_id, tickets, OrderAction.CLOSE)
                closed_count += 1
                self.Debug(f"[风控清仓] {pair.pair_id} 已提交平仓订单")

        self.Debug(f"[风控清仓] 完成: 平仓{closed_count}/{len(pairs_with_position)}个配对")
