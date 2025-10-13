# region imports
from AlgorithmImports import *
from System import Action
from src.config import StrategyConfig
from src.UniverseSelection import SectorBasedUniverseSelection
from src.Pairs import Pairs, TradingSignal, OrderAction
from src.TicketsManager import TicketsManager
# endregion


class BayesianCointegrationStrategy(QCAlgorithm):
    """基于OnData的贝叶斯协整策略"""

    def Initialize(self):
        """初始化策略"""
        # === 加载配置 ===
        self.config = StrategyConfig()
        self.debug_level = self.config.main['debug_level']

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

        self.Debug("[Initialize] 策略初始化完成", 1)


    def Debug(self, message: str, level: int = 2):
        """
        统一的Debug输出方法，支持分级控制
        Args:
            message: 要输出的消息
            level: 消息级别
                0 - 静默（不会输出）
                1 - 关键信息（交易执行、风控触发、重要错误）
                2 - 详细信息（分析过程、统计信息等）
        """
        if level <= self.debug_level:
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
            self.Debug(f"[OnSecuritiesChanged] 当前股票池{len(self.symbols)}只，开始配对分析", 2)

            # 标记正在分析
            self.is_analyzing = True
            self.last_analysis_time = self.Time

            # 执行分析流程
            self._analyze_and_create_pairs()

            # 分析完成
            self.is_analyzing = False
        else:
            self.Debug(f"[OnSecuritiesChanged] 股票数量不足({len(self.symbols)}只)，跳过分析", 2)



    def _analyze_and_create_pairs(self):
        """执行配对分析流程（步骤1-5）"""

        # === 步骤1: 数据处理 ===
        data_result = self.data_processor.process(self.symbols)
        clean_data = data_result['clean_data']
        valid_symbols = data_result['valid_symbols']

        if len(valid_symbols) < 2:
            self.Debug(f"[配对分析] 有效股票不足({len(valid_symbols)}只)，结束分析", 2)
            return

        self.Debug(f"[配对分析] 步骤1完成: {len(valid_symbols)}只股票数据有效", 2)

        # === 步骤2: 协整检验 ===
        cointegration_result = self.cointegration_analyzer.cointegration_procedure(
            valid_symbols,
            clean_data,
            self.config.sector_code_to_name
        )
        raw_pairs = cointegration_result['raw_pairs']

        if not raw_pairs:
            self.Debug("[配对分析] 未发现协整配对，结束分析", 2)
            return

        self.Debug(f"[配对分析] 步骤2完成: 发现{len(raw_pairs)}个协整配对", 2)

        # === 步骤3&4: 质量评估和配对筛选 ===
        selected_pairs = self.pair_selector.selection_procedure(raw_pairs, clean_data)
        self.Debug(f"[配对分析] 步骤3&4完成: 评估并筛选出{len(selected_pairs)}个最佳配对", 2)

        if not selected_pairs:
            self.Debug("[配对分析] 筛选后无合格配对，结束分析", 2)
            return

        # 显示前3个配对
        for pair in selected_pairs[:3]:
            self.Debug(f"  - {pair['symbol1'].Value}&{pair['symbol2'].Value}: "f"质量分数{pair['quality_score']:.3f}", 2)

        # === 步骤5: 贝叶斯建模 ===
        modeling_results = self.bayesian_modeler.modeling_procedure(selected_pairs, clean_data)

        if not modeling_results:
            self.Debug("[配对分析] 建模失败，结束分析", 2)
            return

        self.Debug(f"[配对分析] 步骤5完成: {len(modeling_results)}个配对建模成功", 2)

        # === 步骤6: 直接创建Pairs对象 ===
        new_pairs_dict = {}
        for model_result in modeling_results:
            # 直接使用model_result创建Pairs对象
            pair = Pairs(self, model_result, self.config.pairs_trading)
            new_pairs_dict[pair.pair_id] = pair

            self.Debug(f"  创建配对: {pair.pair_id}, 质量分数: {pair.quality_score:.3f}", 2)

        # === 步骤7: 交给PairsManager管理 ===
        self.pairs_manager.update_pairs(new_pairs_dict)
        self.Debug(f"[配对分析] 完成: PairsManager管理{len(self.pairs_manager.all_pairs)}个配对", 2)
    


    def OnData(self, data: Slice):
        """处理实时数据 - OnData架构的核心"""

        # === DEBUG: OnData入口Portfolio状态监控 ===
        self.Debug(f"\n{'='*80}", 2)
        self.Debug(f"[OnData入口] 时间: {self.Time}", 2)
        self.Debug(f"[OnData入口] Portfolio.MarginRemaining: ${self.Portfolio.MarginRemaining:,.2f}", 2)
        self.Debug(f"[OnData入口] Portfolio.TotalPortfolioValue: ${self.Portfolio.TotalPortfolioValue:,.2f}", 2)
        self.Debug(f"[OnData入口] Portfolio.TotalMarginUsed: ${self.Portfolio.TotalMarginUsed:,.2f}", 2)
        self.Debug(f"[OnData入口] Portfolio.Cash: ${self.Portfolio.Cash:,.2f}", 2)
        self.Debug(f"{'='*80}\n", 2)

        # 如果正在分析，跳过
        if self.is_analyzing:
            return

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
                self.Debug(f"[处理跳过] {pair.pair_id} 订单处理中,跳过", 2)
                continue

            # 获取交易信号
            signal = pair.get_signal(data)

            # 处理平仓信号
            if signal == TradingSignal.CLOSE:
                # === DEBUG: 平仓前Portfolio状态 ===
                margin_before = self.Portfolio.MarginRemaining
                position_value = pair.get_position_value()
                self.Debug(f"[平仓前] {pair.pair_id} MarginRemaining: ${margin_before:,.2f}, 持仓市值: ${position_value:,.2f}", 2)

                self.Debug(f"[OnData] {pair.pair_id} 收到平仓信号(Z-score回归)", 1)

                # 执行平仓并注册订单追踪
                tickets = pair.close_position()
                if tickets:
                    self.tickets_manager.register_tickets(pair.pair_id, tickets, OrderAction.CLOSE)

                # === DEBUG: 平仓后Portfolio状态 ===
                margin_after = self.Portfolio.MarginRemaining
                margin_released = margin_after - margin_before
                self.Debug(f"[平仓后] {pair.pair_id} MarginRemaining: ${margin_after:,.2f}, 释放保证金: ${margin_released:,.2f}", 2)

            elif signal == TradingSignal.STOP_LOSS:
                # === DEBUG: 止损前Portfolio状态 ===
                margin_before = self.Portfolio.MarginRemaining
                position_value = pair.get_position_value()
                self.Debug(f"[止损前] {pair.pair_id} MarginRemaining: ${margin_before:,.2f}, 持仓市值: ${position_value:,.2f}", 2)

                self.Debug(f"[OnData] {pair.pair_id} 收到止损信号(Z-score超限)", 1)

                # 执行平仓并注册订单追踪
                tickets = pair.close_position()
                if tickets:
                    self.tickets_manager.register_tickets(pair.pair_id, tickets, OrderAction.CLOSE)

                # === DEBUG: 止损后Portfolio状态 ===
                margin_after = self.Portfolio.MarginRemaining
                margin_released = margin_after - margin_before
                self.Debug(f"[止损后] {pair.pair_id} MarginRemaining: ${margin_after:,.2f}, 释放保证金: ${margin_released:,.2f}", 2)

        # === 处理无持仓配对（开仓）===
        if pairs_without_position: 

            # 获取所有开仓候选（已按质量降序）
            entry_candidates = self.pairs_manager.get_sequenced_entry_candidates(data)

            if entry_candidates:
                # === 记录初始保证金 ===
                # 使用95%的MarginRemaining,保留5%作为动态缓冲
                initial_margin = self.Portfolio.MarginRemaining * self.config.pairs_trading['margin_usage_ratio']
                buffer = self.Portfolio.MarginRemaining * (1 - self.config.pairs_trading['margin_usage_ratio'])

                # === DEBUG: 开仓初始状态 ===
                self.Debug(f"\n[开仓初始状态]", 2)
                self.Debug(f"  Portfolio.MarginRemaining: ${self.Portfolio.MarginRemaining:,.2f}", 2)
                self.Debug(f"  initial_margin (95%): ${initial_margin:,.2f}", 2)
                self.Debug(f"  buffer (5%): ${buffer:,.2f}", 2)
                self.Debug(f"  候选配对数量: {len(entry_candidates)}", 2)

                # 检查是否低于最小阈值
                if initial_margin >= self.min_investment:
                    # 保证金充足,继续开仓流程

                    # 提取候选信息 (entry_candidates已按质量降序)
                    planned_allocations = {}  # {pair_id: pct}
                    opening_signals = {}      # {pair_id: signal}

                    for pair, signal, score, pct in entry_candidates:
                        planned_allocations[pair.pair_id] = pct
                        opening_signals[pair.pair_id] = signal

                    # === 逐个开仓 (动态缩放保持公平) ===
                    actual_opened = 0
                    for pair_id, pct in planned_allocations.items():
                        pair = self.pairs_manager.get_pair_by_id(pair_id)
                        signal = opening_signals[pair_id]

                        # === DEBUG: 开仓前Portfolio状态 ===
                        self.Debug(f"\n[配对#{actual_opened + 1}] {pair_id}", 2)
                        self.Debug(f"  当前MarginRemaining: ${self.Portfolio.MarginRemaining:,.2f}", 2)

                        # 动态缩放计算
                        current_margin = (self.Portfolio.MarginRemaining - buffer) * pct
                        if current_margin <= 0:
                            self.Debug(f"[开仓] 保证金已耗尽,停止开仓", 1)
                            break

                        scale_factor = initial_margin / (self.Portfolio.MarginRemaining - buffer)
                        margin_allocated = current_margin * scale_factor  # = initial_margin * pct

                        # === DEBUG: 分配计算详情 ===
                        self.Debug(f"  计划分配比例(pct): {pct:.3f}", 2)
                        self.Debug(f"  当前可用保证金(去buffer): ${(self.Portfolio.MarginRemaining - buffer):,.2f}", 2)
                        self.Debug(f"  current_margin (可用*pct): ${current_margin:,.2f}", 2)
                        self.Debug(f"  scale_factor (初始/可用): {scale_factor:.4f}", 2)
                        self.Debug(f"  margin_allocated (最终): ${margin_allocated:,.2f}", 2)

                        # 检查1: 订单锁定检查
                        if self.tickets_manager.is_pair_locked(pair_id):
                            self.Debug(f"[开仓跳过] {pair_id} 订单处理中,跳过", 2)
                            continue

                        # 检查2: 最小投资额
                        if margin_allocated < self.min_investment:
                            self.Debug(f"[开仓] {pair_id} 分配保证金{margin_allocated:.0f}不足最小投资额,跳过", 2)
                            continue

                        # 检查3: 保证金是否仍足够 (理论上应该足够,但保险起见)
                        available_for_check = self.Portfolio.MarginRemaining - buffer
                        if margin_allocated > available_for_check:
                            self.Debug(f"[开仓] {pair_id} 边界检查失败:", 1)
                            self.Debug(f"  需要保证金: ${margin_allocated:,.2f}", 1)
                            self.Debug(f"  可用保证金(去buffer): ${available_for_check:,.2f}", 1)
                            self.Debug(f"  Portfolio.MarginRemaining: ${self.Portfolio.MarginRemaining:,.2f}", 1)
                            self.Debug(f"  buffer: ${buffer:,.2f}", 1)
                            continue

                        # 执行开仓 (传入保证金,Pairs内部计算市值和数量)
                        # === DEBUG: 开仓执行前 ===
                        margin_before_open = self.Portfolio.MarginRemaining
                        self.Debug(f"  开仓前MarginRemaining: ${margin_before_open:,.2f}", 2)

                        # 执行开仓并注册订单追踪
                        tickets = pair.open_position(signal, margin_allocated, data)
                        if tickets:
                            self.tickets_manager.register_tickets(pair_id, tickets, OrderAction.OPEN)
                            actual_opened += 1

                        # === DEBUG: 开仓执行后 ===
                        margin_after_open = self.Portfolio.MarginRemaining
                        margin_consumed = margin_before_open - margin_after_open
                        self.Debug(f"  开仓后MarginRemaining: ${margin_after_open:,.2f}", 2)
                        self.Debug(f"  实际消耗保证金: ${margin_consumed:,.2f}", 2)

                        # 获取质量分数用于日志
                        score = pair.get_quality_score()
                        self.Debug(
                            f"[开仓] #{actual_opened} {pair_id} "
                            f"保证金:{margin_allocated:.0f} "
                            f"质量:{score:.3f}", 1
                        )

                    # 执行结果
                    expected_count = len(entry_candidates)
                    if actual_opened < expected_count:
                        skipped_count = expected_count - actual_opened
                        self.Debug(f"[开仓] 执行结果: 计划{expected_count}个,实际{actual_opened}个,跳过{skipped_count}个", 1)
                    else:
                        self.Debug(f"[开仓] 成功开仓{actual_opened}个配对", 1)

                else:
                    # 保证金不足,输出日志并跳过
                    if self.config.main['debug_level'] >= 1:
                        self.Debug(f"[开仓] 可用保证金${initial_margin:,.0f}低于最小投资${self.min_investment:,.0f},本轮跳过")


    def OnOrderEvent(self, event):
        """
        订单事件回调

        委托给TicketsManager统一处理订单状态更新
        检测并处理异常配对(单腿失败等)
        """
        # 委托给TicketsManager处理
        self.tickets_manager.on_order_event(event)

        # 检查是否有异常配对需要处理
        anomaly_pairs = self.tickets_manager.get_anomaly_pairs()
        if anomaly_pairs:
            for pair_id in anomaly_pairs:
                self.Debug(
                    f"[订单异常] {pair_id} 检测到单腿失败(Canceled/Invalid), "
                    f"已标记为异常持仓,风控将处理",
                    1
                )
                # 注意: 实际的单腿强平由风控模块的异常持仓检查处理
                # 此处仅记录日志,不直接操作
