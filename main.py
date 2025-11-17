# region imports
from AlgorithmImports import *
from System import Action
from src.config import StrategyConfig
from src.UniverseSelection import SectorBasedUniverseSelection
from src.analysis.DataProcessor import DataProcessor
from src.analysis.CointegrationAnalyzer import CointegrationAnalyzer
from src.analysis.BayesianModeler import BayesianModeler
from src.analysis.PairSelector import PairSelector
from src.analysis.IndustryQuotaManager import IndustryQuotaManager 
from src.Pairs import Pairs
from src.PairsManager import PairsManager
from src.TicketsManager import TicketsManager
from src.risk import RiskManager
from src.execution import ExecutionManager, OrderExecutor, MarginAllocator
# endregion


class BayesianCointegrationStrategy(QCAlgorithm):
    """基于OnData的贝叶斯协整策略"""

    def Initialize(self):
        """初始化策略"""
        # === 加载参数配置 ===
        self.config = StrategyConfig()
        self.debug_mode = self.config.main.debug_mode
        self.SetStartDate(*self.config.main.start_date)
        self.SetEndDate(*self.config.main.end_date)
        self.SetCash(self.config.main.cash)
        self.UniverseSettings.Resolution = self.config.main.resolution
        self.SetBrokerageModel(self.config.main.brokerage_name, self.config.main.account_type)

        # === Benchmark symbols列表(需过滤，不参与选股) ===
        self.benchmark_symbols = []
        self.market_benchmark = self.AddEquity("SPY", self.config.main.resolution).Symbol
        self.benchmark_symbols.append(self.market_benchmark)
        self.SetBenchmark(self.market_benchmark)


        # === 初始化选股模块 ===
        self.universe_selector = SectorBasedUniverseSelection(self)
        self.SetUniverseSelection(self.universe_selector)
        self.symbols = []

        # 选股触发调度器
        # 传入market_benchmark确保在首个交易日触发(而非日历月首)
        date_rule = getattr(self.DateRules, self.config.main.schedule_frequency)(self.market_benchmark)
        time_rule = self.TimeRules.At(*self.config.main.schedule_time)
        self.Schedule.On(date_rule, time_rule, Action(self.universe_selector.trigger_selection))

        # === 初始化分析工具 ===
        self.data_processor = DataProcessor(self, self.config.analysis)
        self.industry_quota_manager = IndustryQuotaManager(self, self.config.industry_quota)
        self.bayesian_modeler = BayesianModeler(self, self.config.analysis, self.config.bayesian_modeler)
        self.pair_selector = PairSelector(self, self.config.analysis, self.config.pair_selector)
        self.pairs_manager = PairsManager(self, self.config.pairs_trading)


        # === 初始化状态管理 ===
        self.is_analyzing = False  # 是否正在分析
        self.last_analysis_time = None  # 上次分析时间

        # === 添加VIX指数（用于市场条件检查）===
        vix_config = self.config.risk_management.market_condition
        self.vix_symbol = self.AddIndex(vix_config.vix_symbol, vix_config.vix_resolution).Symbol
        self.benchmark_symbols.append(self.vix_symbol)  

        # === 初始化辅助工具 ===
        self.tickets_manager = TicketsManager(self, self.pairs_manager)
        self.risk_manager = RiskManager(self, self.config, self.pairs_manager)
        self.order_executor = OrderExecutor(self, self.tickets_manager)
        self.margin_allocator = MarginAllocator(self, self.config)
        self.execution_manager = ExecutionManager(self, self.pairs_manager, self.risk_manager, self.tickets_manager, self.order_executor, self.margin_allocator)

        # v7.31.4: 月度风控统计追踪
        self.monthly_close_stats = {}  # {year_month: {reason: count}}
        self.last_stat_month = None



    def Debug(self, message: str, level: int = 0):
        """
        分层日志输出

        Args:
            message: 日志内容
            level: 日志级别
                - 0: 核心交易事件 (生产模式, 10-30年回测)
                - 1: 详细调试信息 (调试模式, 1年回测)

        设计:
            - log_level=0: 仅输出 level=0 日志 (生产)
            - log_level=1: 输出 level=0 AND level=1 日志 (调试, 包含关系)
            - 使用 level <= log_level 实现层级包含逻辑

        历史:
            - v7.8.3: 删除SecurityChanges日志,过滤逻辑已无用
            - v7.8.5: 移除死代码,简化为纯wrapper
            - v7.9.0: 添加level参数支持两级日志架构
        """
        log_level = getattr(self.config.main, 'log_level', 0)  # dataclass使用getattr代替.get()
        if self.debug_mode and level <= log_level:
            QCAlgorithm.Debug(self, message)


    def record_close_stat(self, reason: str):
        """
        记录平仓原因统计 (v7.31.4: 月度风控统计)

        Args:
            reason: 平仓原因 (MEAN_REVERSION, DRAWDOWN, TIMEOUT, PAIR_BREAK等)
        """
        from collections import defaultdict

        current_month = self.Time.strftime('%Y-%m')
        if current_month not in self.monthly_close_stats:
            self.monthly_close_stats[current_month] = defaultdict(int)

        # 归类原因
        if reason in ['MEAN_REVERSION', 'PAIR_BREAK']:
            category = reason
        elif 'DRAWDOWN' in reason or 'CUMULATIVE_LOSS' in reason:
            category = 'DRAWDOWN'
        elif 'TIMEOUT' in reason:
            category = 'TIMEOUT'
        else:
            category = 'OTHER'

        self.monthly_close_stats[current_month][category] += 1


    def OnSecuritiesChanged(self, changes: SecurityChanges):
        """处理证券变更事件 - 触发配对分析

        重要: 不调用 base.OnSecuritiesChanged(changes)
        原因: QCAlgorithm的base实现仅打印冗长的SecurityChanges日志(列出所有Symbol+ID)
              我们已有简洁的自定义日志(仅打印数量),无需框架级日志污染
        """

        # 添加新股票（过滤掉所有benchmark: SPY, VIX等）
        added_count = 0
        for security in changes.AddedSecurities:
            # 过滤掉benchmark symbols
            if security.Symbol in self.benchmark_symbols:
                continue
            if security.Symbol not in self.symbols:
                self.symbols.append(security.Symbol)
                added_count += 1

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
        """执行配对分析流程（步骤1-5）(v7.12.0: 动态行业配额)"""

        # === 步骤1: 数据处理 ===
        data_result = self.data_processor.process(self.symbols)
        clean_data = data_result['clean_data']
        valid_symbols = data_result['valid_symbols']

        if len(valid_symbols) < 2:
            return

        # === v7.12.0: 计算行业配额 ===
        industry_quotas = self.industry_quota_manager.calculate_quotas(
            pairs_manager=self.pairs_manager
        )

        # === 步骤2: 协整检验 (v7.12.0: 动态创建analyzer并传递配额) ===
        cointegration_analyzer = CointegrationAnalyzer(
            self,
            self.config.cointegration_analyzer,
            industry_quotas
        )
        cointegration_result = cointegration_analyzer.cointegration_procedure(valid_symbols, clean_data)
        raw_pairs = cointegration_result['raw_pairs']

        if not raw_pairs:
            return

        # === 步骤3: 构建PairData字典 ===
        from src.analysis.PairData import PairData
        pair_data_dict = {}
        for pair_info in raw_pairs:
            pair_key = (pair_info['symbol1'], pair_info['symbol2'])
            pair_data_dict[pair_key] = PairData.from_clean_data(pair_info, clean_data)

        # === 步骤4: 贝叶斯建模（v7.4.0: 移至前置，处理所有协整对） ===
        modeling_results = self.bayesian_modeler.modeling_procedure(raw_pairs, pair_data_dict)

        if not modeling_results:
            return

        # === 步骤5: 质量评估和配对筛选（v7.4.0: 移至后置，使用贝叶斯后验参数） ===
        selected_pairs = self.pair_selector.selection_procedure(modeling_results)

        if not selected_pairs:
            return

        # === 步骤6: 通过类方法工厂创建Pairs对象 ===
        new_pairs_dict = {}
        for model_result in selected_pairs:
            # 使用类方法工厂创建Pairs对象（与PairData.from_clean_data()一致）
            pair = Pairs.from_model_result(self, model_result, self.config.pairs_trading)

            # v7.32.0: 设置行业配额tier (用于get_planned_allocation_pct)
            industry_code = str(pair.industry_code) if pair.industry_code else None
            if industry_code and industry_code in industry_quotas:
                tier = industry_quotas[industry_code].get('tier', 'tier0')
            else:
                tier = 'tier0'  # 默认tier0 (预热期或无历史数据)
            pair.set_industry_quota_tier(tier)

            new_pairs_dict[pair.pair_id] = pair

        # === 步骤7: 交给PairsManager管理 ===
        self.pairs_manager.update_pairs(new_pairs_dict)

        # v7.28.3: 配对流程审计日志（显示完整漏斗）
        coint_count = len(raw_pairs)
        model_count = len([r for r in modeling_results if r is not None])
        select_count = len(selected_pairs)
        created_count = len(new_pairs_dict)

        self.Debug(
            f"[配对流程] 协整通过{coint_count}对 → "
            f"贝叶斯成功{model_count}对 → "
            f"质量筛选{select_count}对 → "
            f"最终创建{created_count}对",
            level=1
        )

        # v7.36.0: 聚合各行业累积收益率 (用于日志显示)
        from collections import defaultdict
        industry_stats = defaultdict(lambda: {'realized_pnl': 0.0, 'realized_cost': 0.0})

        for pair in self.pairs_manager.all_pairs.values():
            if pair.industry_code is None or pair.trade_count == 0:
                continue
            industry_code = str(pair.industry_code)
            industry_stats[industry_code]['realized_pnl'] += pair.realized_pnl
            industry_stats[industry_code]['realized_cost'] += pair.realized_cost

        # v7.36.0: 详细日志 - 显示各行业配对创建统计 + 累积收益率
        industry_pair_count = defaultdict(int)
        for pair in new_pairs_dict.values():
            if pair.industry_code:
                industry_pair_count[str(pair.industry_code)] += 1

        if industry_pair_count:
            industry_names = self.config.constants['industry_names']
            readable_stats = {}

            for code, count in industry_pair_count.items():
                industry_name = industry_names.get(int(code), f'未知({code})')

                # 计算累积收益率
                if code in industry_stats and industry_stats[code]['realized_cost'] > 0:
                    weighted_return = (industry_stats[code]['realized_pnl'] /
                                       industry_stats[code]['realized_cost']) * 100
                    label = f"{industry_name}({weighted_return:+.1f}%)"
                else:
                    # 无历史数据 (预热期或首次创建)
                    label = f"{industry_name}(预热期)"

                readable_stats[label] = count

            self.Debug(f"[配对创建] 各行业配对数量: {readable_stats}", level=1)
    


    def OnData(self, data: Slice):
        """处理实时数据 - OnData架构的核心"""
        # v7.31.4: 月末风控统计汇总
        current_month = self.Time.strftime('%Y-%m')
        if self.last_stat_month and current_month != self.last_stat_month:
            # 月份切换,输出上月统计
            if self.last_stat_month in self.monthly_close_stats:
                stats = self.monthly_close_stats[self.last_stat_month]
                total = sum(stats.values())
                self.Debug(
                    f"[月度总结-{self.last_stat_month}] "
                    f"平仓{total}次: "
                    f"均值回归{stats.get('MEAN_REVERSION', 0)}次, "
                    f"回撤{stats.get('DRAWDOWN', 0)}次, "
                    f"超时{stats.get('TIMEOUT', 0)}次, "
                    f"协整破裂{stats.get('PAIR_BREAK', 0)}次, "
                    f"其他{stats.get('OTHER', 0)}次",
                    level=0
                )
        self.last_stat_month = current_month

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

