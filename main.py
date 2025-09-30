# region imports
from AlgorithmImports import *
from System import Action
from datetime import datetime, timedelta
from src.config import StrategyConfig
from src.UniverseSelection import SectorBasedUniverseSelection
from src.Pairs import Pairs, TradingSignal
from src.RiskManagement import PortfolioLevelRiskManager, PairLevelRiskManager
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

        # === 初始化风控模块 ===
        self.portfolio_level_risk_manager = PortfolioLevelRiskManager(self, self.config)  # Portfolio层面风控
        self.pair_level_risk_manager = PairLevelRiskManager(self, self.config.risk_management)  # Pair层面风控

        # === 初始化核心数据结构 ===
        self.symbols = []  # 当前选中的股票列表

        # === 初始化状态管理 ===
        self.is_analyzing = False  # 是否正在分析
        self.last_analysis_time = None  # 上次分析时间
        self.strategy_cooldown_until = None  # 策略冷却截止时间

        # === 添加市场基准 ===
        self.market_benchmark = self.AddEquity("SPY", Resolution.Daily).Symbol
        self.market_volatility_cooldown_until = None  # 市场波动冷却截止时间

        # === 资金管理参数 ===
        self.initial_cash = self.config.main['cash']
        # 注意：cash_buffer现在是动态的，将在OnData中计算
        # 最小投资额是固定的
        self.min_investment = self.initial_cash * self.config.main['min_investment_ratio']

        # === 订单去重机制 ===
        self.daily_processed_signals = {}  # 每日已处理信号
        self._last_processing_date = None  # 上次处理日期

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

        # === 每日去重机制重置 ===
        current_date = self.Time.date()
        if self._last_processing_date != current_date:
            self.daily_processed_signals = {}
            self._last_processing_date = current_date
            self.Debug(f"[OnData] 新的一天: {current_date}, 重置信号去重", 2)

        # === 检查策略冷却状态 ===
        if self.strategy_cooldown_until and self.Time < self.strategy_cooldown_until:
            return

        if self.strategy_cooldown_until and self.Time >= self.strategy_cooldown_until:
            self.Debug(f"[风控恢复] 冷却期结束，恢复正常交易", 1)
            self.strategy_cooldown_until = None

        # 如果正在分析，跳过
        if self.is_analyzing:
            return

        # 如果没有可交易配对，跳过
        if not self.pairs_manager.has_tradeable_pairs():
            return

        # === Portfolio层面风控流程 ===
        # 按优先级逐个检查，体现风控层级

        # 1. 爆仓风控（最高优先级）
        if self.portfolio_level_risk_manager.is_account_blowup():
            self.Debug(f"[爆仓风控] 账户触发爆仓线，执行全部清仓", 1)
            self.Liquidate()
            cooldown_days = self.config.risk_management['blowup_cooldown_days']
            if cooldown_days > 0:
                self.strategy_cooldown_until = self.Time + timedelta(days=cooldown_days)
                self.Debug(f"[爆仓风控] 策略冷却{cooldown_days}天至{self.strategy_cooldown_until.date()}", 1)
            return

        # 2. 回撤风控（次高优先级）
        if self.portfolio_level_risk_manager.is_excessive_drawdown():
            self.Debug(f"[回撤风控] 账户超过最大回撤，执行全部清仓", 1)
            self.Liquidate()
            cooldown_days = self.config.risk_management['drawdown_cooldown_days']
            if cooldown_days > 0:
                self.strategy_cooldown_until = self.Time + timedelta(days=cooldown_days)
                self.Debug(f"[回撤风控] 策略冷却{cooldown_days}天至{self.strategy_cooldown_until.date()}", 1)
            return

        # 3. 行业集中度检查与调整
        sectors_to_adjust = self.portfolio_level_risk_manager.check_sector_concentration()
        if sectors_to_adjust:
            for sector, info in sectors_to_adjust.items():
                # 执行仓位调整
                for pair in info['pairs']:
                    pair.adjust_position(info['target_ratio'])

                self.Debug(
                    f"[行业集中度调整] {sector}行业从{info['concentration']:.1%}调整至"
                    f"{self.config.risk_management['sector_target_exposure']:.0%}", 1
                )

        # 分类获取配对
        pairs_with_position = self.pairs_manager.get_pairs_with_position()
        pairs_without_position = self.pairs_manager.get_pairs_without_position()


        # === Pair层面风控流程 ===
        for pair in pairs_with_position.values():

            # 0. 持仓完整性检查 (最优先)
            is_valid, error_msg = pair.check_position_integrity()
            if not is_valid:
                self.Debug(f"[持仓检查] {pair.pair_id} {error_msg}", 0)

                # 单边持仓直接清算
                if "单边持仓" in error_msg or "持仓方向错误" in error_msg:
                    self.Debug(f"[持仓检查] 强制清算异常持仓", 0)
                    pair.close_position()
                    self.pair_level_risk_manager.clear_pair_history(pair.pair_id)
                    continue

            # 1. 持仓超期检查（最高优先级）
            if self.pair_level_risk_manager.check_holding_timeout(pair):
                holding_days = pair.get_pair_holding_days()
                self.Debug(f"[Pair超期] {pair.pair_id} 持仓{holding_days}天超过{pair.max_holding_days}天限制，执行清仓", 1)
                pair.close_position()  # 统一使用Pairs的close_position方法,确保cooldown生效
                self.pair_level_risk_manager.clear_pair_history(pair.pair_id)
                continue

            # 2. 异常持仓检查（次高优先级）
            is_anomaly, anomaly_desc = self.pair_level_risk_manager.check_position_anomaly(pair)
            if is_anomaly:
                self.Debug(f"[Pair异常] {pair.pair_id} {anomaly_desc}，执行清仓", 1)
                pair.close_position()  # 统一使用Pairs的close_position方法,确保cooldown生效
                self.pair_level_risk_manager.clear_pair_history(pair.pair_id)
                continue

            # 3. 回撤超限检查
            if self.pair_level_risk_manager.check_pair_drawdown(pair):
                self.Debug(f"[Pair回撤] {pair.pair_id} 超过最大回撤{self.config.risk_management['max_pair_drawdown']:.0%}，执行清仓", 1)
                pair.close_position()  # 统一使用Pairs的close_position方法,确保cooldown生效
                self.pair_level_risk_manager.clear_pair_history(pair.pair_id)
                continue

            # 获取交易信号
            signal = pair.get_signal(data)

            # 处理平仓信号
            if signal == TradingSignal.CLOSE:
                self.Debug(f"[OnData] {pair.pair_id} 收到平仓信号(Z-score回归)", 1)
                pair.close_position()
                self.pair_level_risk_manager.clear_pair_history(pair.pair_id)

            elif signal == TradingSignal.STOP_LOSS:
                self.Debug(f"[OnData] {pair.pair_id} 收到止损信号(Z-score超限)", 1)
                pair.close_position()
                self.pair_level_risk_manager.clear_pair_history(pair.pair_id)


        # === 2. 市场环境检查（开仓前）===
        # 市场波动检查 - 如果市场波动过大，不开新仓
        if self.portfolio_level_risk_manager.is_high_market_volatility():
            cooldown_days = self.config.risk_management['market_cooldown_days']
            self.market_volatility_cooldown_until = self.Time + timedelta(days=cooldown_days)
            self.Debug(f"[市场环境] 市场波动率过高，暂停新开仓{cooldown_days}天", 1)
            return  # 直接返回，不执行开仓


        # === 3. 处理无持仓配对（开仓）===
        if pairs_without_position: 

            # 获取所有开仓候选（已按质量降序）
            entry_candidates = self.pairs_manager.get_sequenced_entry_candidates(data)

            if entry_candidates:
                # === 记录初始保证金 ===
                # 使用95%的MarginRemaining,保留5%作为动态缓冲
                initial_margin = self.Portfolio.MarginRemaining * self.config.pairs_trading['margin_usage_ratio']

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

                        # 动态缩放计算
                        current_margin = self.Portfolio.MarginRemaining * self.config.pairs_trading['margin_usage_ratio']
                        if current_margin <= 0:
                            self.Debug(f"[开仓] 保证金已耗尽,停止开仓", 1)
                            break

                        scale_factor = initial_margin / current_margin
                        margin_allocated = current_margin * pct * scale_factor  # = initial_margin * pct

                        # 检查1: 是否已处理过该信号
                        signal_key = (pair_id, signal, current_date)
                        if signal_key in self.daily_processed_signals:
                            self.Debug(f"[开仓] {pair_id} 今日已处理过该信号,跳过", 2)
                            continue

                        # 检查2: 最小投资额
                        if margin_allocated < self.min_investment:
                            self.Debug(f"[开仓] {pair_id} 分配保证金{margin_allocated:.0f}不足最小投资额,跳过", 2)
                            continue

                        # 检查3: 保证金是否仍足够 (理论上应该足够,但保险起见)
                        if margin_allocated > current_margin:
                            self.Debug(f"[开仓] {pair_id} 保证金不足(需要{margin_allocated:.0f},剩余{current_margin:.0f}),跳过", 1)
                            continue

                        # 执行开仓 (传入保证金,Pairs内部计算市值和数量)
                        pair.open_position(signal, margin_allocated, data)
                        actual_opened += 1

                        # 标记已处理
                        self.daily_processed_signals[signal_key] = self.Time

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
