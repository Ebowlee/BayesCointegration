# region imports
from AlgorithmImports import *
from System import Action
from datetime import datetime, timedelta
from src.config import StrategyConfig
from src.UniverseSelection import MyUniverseSelectionModel
from src.Pairs import Pairs
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
        self.SetBrokerageModel(
            self.config.main['brokerage_name'],
            self.config.main['account_type']
        )

        # === 初始化选股模块（保持不变）===
        self.universe_selector = MyUniverseSelectionModel(
            self,
            self.config.universe_selection,
            self.config.sector_code_to_name,
            self.config.sector_name_to_code
        )
        self.SetUniverseSelection(self.universe_selector)

        # 定期触发选股（保持原有调度）
        date_rule = getattr(self.DateRules, self.config.main['schedule_frequency'])()
        time_rule = self.TimeRules.At(*self.config.main['schedule_time'])
        self.Schedule.On(date_rule, time_rule, Action(self.universe_selector.TriggerSelection))

        # === 初始化分析工具 ===
        from src.analysis.DataProcessor import DataProcessor
        from src.analysis.CointegrationAnalyzer import CointegrationAnalyzer
        from src.analysis.BayesianModeler import BayesianModeler
        from src.analysis.PairSelector import PairSelector
        from src.PairsManager import PairsManager

        self.data_processor = DataProcessor(self, self.config.analysis)
        self.cointegration_analyzer = CointegrationAnalyzer(self, self.config.analysis)
        self.bayesian_modeler = BayesianModeler(self, self.config.analysis, None)  # state参数暂时为None
        self.pair_selector = PairSelector(self, self.config.analysis)

        # === 初始化配对管理器 ===
        self.pairs_manager = PairsManager(self, self.config.pairs_trading)

        # === 初始化风控模块 ===
        self.portfolio_level_risk_manager = PortfolioLevelRiskManager(self)  # Portfolio层面风控
        self.pair_level_risk_manager = PairLevelRiskManager(self)  # Pair层面风控

        # === 初始化核心数据结构 ===
        self.symbols = []  # 当前选中的股票列表

        # === 初始化状态管理 ===
        self.is_analyzing = False  # 是否正在分析
        self.last_analysis_time = None  # 上次分析时间
        self.strategy_cooldown_until = None  # 策略冷却截止时间

        # === 添加市场基准 ===
        self.market_benchmark = self.AddEquity("SPY", Resolution.Daily).Symbol
        self.market_volatility_cooldown_until = None  # 市场波动冷却截止时间

        # === 资金管理参数（一次性计算）===
        self.initial_cash = self.config.main['cash']
        self.cash_buffer = self.initial_cash * self.config.main['cash_buffer_ratio']
        self.min_allocation = self.initial_cash * self.config.pairs_trading['min_position_pct']

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
        cointegration_result = self.cointegration_analyzer.find_cointegrated_pairs(
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
        selected_pairs = self.pair_selector.evaluate_and_select(raw_pairs, clean_data)
        self.Debug(f"[配对分析] 步骤3&4完成: 评估并筛选出{len(selected_pairs)}个最佳配对", 2)

        if not selected_pairs:
            self.Debug("[配对分析] 筛选后无合格配对，结束分析", 2)
            return

        # 显示前3个配对
        for pair in selected_pairs[:3]:
            self.Debug(f"  - {pair['symbol1'].Value}&{pair['symbol2'].Value}: "f"质量分数{pair['quality_score']:.3f}", 2)

        # === 步骤5: 贝叶斯建模 ===
        modeling_result = self.bayesian_modeler.model_pairs(selected_pairs, clean_data)
        modeled_pairs = modeling_result['modeled_pairs']

        if not modeled_pairs:
            self.Debug("[配对分析] 建模失败，结束分析", 2)
            return

        self.Debug(f"[配对分析] 步骤5完成: {len(modeled_pairs)}个配对建模成功", 2)

        # === 步骤6: 创建并管理Pairs对象 ===
        self.pairs_manager.create_pairs_from_models(modeled_pairs)
        self.Debug(f"[配对分析] 完成: PairsManager管理{len(self.pairs_manager.all_pairs)}个配对", 2)
    


    def OnData(self, data: Slice):
        """处理实时数据 - OnData架构的核心"""

        # === 检查策略冷却状态 ===
        if self.strategy_cooldown_until:
            if self.Time < self.strategy_cooldown_until:
                # 仍在冷却期内
                return
            else:
                # 冷却期结束，重置状态
                self.Debug(f"[风控恢复] 冷却期结束，恢复正常交易", 1)
                self.strategy_cooldown_until = None

        # 如果正在分析，跳过
        if self.is_analyzing:
            return

        # 如果没有可交易配对，跳过
        if not self.pairs_manager.has_tradeable_pairs():
            return

        # === Portfolio层面风控（一行搞定）===
        if self.portfolio_level_risk_manager.manage_portfolio_risks():
            return  # 风控触发，结束当前bar


        # === 正常交易逻辑 ===
        # 分类获取配对
        pairs_with_position = self.pairs_manager.get_pairs_with_position()
        pairs_without_position = self.pairs_manager.get_pairs_without_position()

        # === 1. 处理有持仓配对（风控+平仓）===
        # 只对有持仓的配对进行风控，使用生成器过滤风险配对
        for safe_pair in self.pair_level_risk_manager.manage_position_risks(pairs_with_position.values()):
            # safe_pair已经通过所有风控检查且确定有持仓

            # 获取交易信号
            signal = safe_pair.get_signal(data)

            # 处理平仓信号
            if signal == "CLOSE":
                # 正常平仓（Z-score回归）
                self.Debug(f"[OnData] {safe_pair.pair_id} 收到平仓信号(Z-score回归)", 1)
                safe_pair.close_position()

            elif signal == "STOP_LOSS":
                # 止损平仓（Z-score超限）
                self.Debug(f"[OnData] {safe_pair.pair_id} 收到止损信号(Z-score超限)", 1)
                safe_pair.close_position()

            # HOLD信号不需要处理，继续持仓

        # === 2. 处理无持仓配对（开仓）===
        if pairs_without_position and self.pairs_manager.can_open_new_position():
            # 收集所有开仓信号
            opening_signals = []
            for pair in pairs_without_position.values():
                signal = pair.get_signal(data)
                if signal in ["LONG_SPREAD", "SHORT_SPREAD"]:
                    opening_signals.append((pair, signal, pair.get_quality_score()))

            if opening_signals:
                # 按quality_score降序排序
                opening_signals.sort(key=lambda x: x[2], reverse=True)

                # 计算可用资金（扣除5%缓冲）
                available_cash = max(0, self.Portfolio.Cash - self.cash_buffer)

                if available_cash > 0:
                    # 资金分配逻辑
                    if available_cash < self.min_allocation:
                        # 资金不足，全部给最优配对
                        best_pair, signal, score = opening_signals[0]
                        self.Debug(
                            f"[OnData] 资金不足，全部分配给最优配对 {best_pair.pair_id} "
                            f"(score:{score:.3f})", 2
                        )
                        best_pair.open_position(signal, available_cash)
                    else:
                        # 资金充裕，按公式分配
                        for pair, signal, score in opening_signals:
                            # 检查是否还能开新仓
                            if not self.pairs_manager.can_open_new_position():
                                self.Debug("[OnData] 达到最大配对数限制，停止开仓", 2)
                                break

                            # 动态计算分配比例：10% + score*(25%-10%)
                            min_pct = self.config.pairs_trading['min_position_pct']
                            max_pct = self.config.pairs_trading['max_position_pct']
                            allocation_pct = min_pct + score * (max_pct - min_pct)

                            # 基于可用现金计算分配金额
                            allocation = available_cash * allocation_pct

                            # 但不低于最小分配金额
                            allocation = max(allocation, self.min_allocation)

                            # 检查剩余资金
                            if allocation > available_cash:
                                # 剩余资金不足，停止开仓
                                self.Debug("[OnData] 剩余资金不足，停止开仓", 2)
                                break

                            # 执行开仓
                            pair.open_position(signal, allocation)
                            available_cash -= allocation
                            self.Debug(
                                f"[OnData] 开仓成功 {pair.pair_id} "
                                f"分配:{allocation:.0f} "
                                f"剩余:{available_cash:.0f}", 1
                            )

                            # 检查剩余资金是否太少
                            if available_cash < self.min_allocation:
                                self.Debug("[OnData] 剩余资金不足，停止开仓", 2)
                                break
