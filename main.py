# region imports
from AlgorithmImports import *
from System import Action
from src.config import StrategyConfig
from src.UniverseSelection import SectorBasedUniverseSelection
from src.analysis.DataProcessor import DataProcessor
from src.analysis.CointegrationAnalyzer import CointegrationAnalyzer
from src.analysis.PairData import PairData
from src.analysis.BayesianModeler import BayesianModeler
from src.analysis.PairSelector import PairSelector
from src.Pairs import Pairs
from src.PairsManager import PairsManager
from src.analysis.IndustryQuotaManager import IndustryQuotaManager
from src.RiskManager import RiskManager
# endregion


class BayesianCointegrationStrategy(QCAlgorithm):
    """v7.98.2: 单一职责重构 (check_portfolio_drawdown 纯检测)"""

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

        # === Benchmark设置 ===
        self.benchmark_symbols = []
        self.market_benchmark = self.AddEquity("SPY", self.config.main.resolution).Symbol
        self.benchmark_symbols.append(self.market_benchmark)
        self.SetBenchmark(self.market_benchmark)

        # === 初始化选股模块 ===
        self.universe_selector = SectorBasedUniverseSelection(self)
        self.SetUniverseSelection(self.universe_selector)
        self.symbols = []

        # 选股触发调度器
        date_rule = getattr(self.DateRules, self.config.main.schedule_frequency)(self.market_benchmark)
        time_rule = self.TimeRules.At(*self.config.main.schedule_time)
        self.Schedule.On(date_rule, time_rule, Action(self.universe_selector.trigger_selection))

        # === ETF订阅 ===
        self.etf_symbols = []                                   # 存储已订阅的ETF Symbol对象
        self.etf_industry_mapping = {}                          # 反向映射: {ticker: [industry_codes]}
        if self.config.etf_universe.enabled:
            self._subscribe_industry_etfs()

        # === 初始化分析管道 ===
        self.data_processor = DataProcessor(self, self.config.data_processor)

        # === 初始化协整分析器 ===
        self.cointegration_analyzer = CointegrationAnalyzer(self, self.config.cointegration_analyzer)

        # === 初始化贝叶斯建模器 (v7.96.0: 配置参数名更新) ===
        self.bayesian_modeler = BayesianModeler(self, self.config.data_processor, self.config.bayesian_modeler)

        # === 初始化配对选择器 ===
        self.pair_selector = PairSelector(self, self.config.data_processor, self.config.pair_selector)

        # === 初始化配对管理器 ===
        self.pairs_manager = PairsManager(self, self.config)

        # === 初始化行业配额管理器 ===
        self.industry_quota_manager = IndustryQuotaManager(self, self.config.industry_quota)

        # === 初始化风控模块 (v7.98.1: 简化配置路径) ===
        # VIX订阅 (用于MarketCondition检查)
        vix_symbol_str = self.config.risk_manager.vix_symbol  # 'VIX'
        self.vix_symbol = self.AddData(CBOE, vix_symbol_str, Resolution.Daily).Symbol

        # 初始化RiskManager
        self.risk_manager = RiskManager(self, self.config)


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
        """
        log_level = getattr(self.config.main, 'log_level', 0)
        if self.debug_mode and level <= log_level:
            QCAlgorithm.Debug(self, message)


    def OnSecuritiesChanged(self, changes: SecurityChanges):
        """
        处理证券变更事件 - 输出选股结果

        重要: 不调用 base.OnSecuritiesChanged(changes)
        原因: QCAlgorithm的base实现仅打印冗长的SecurityChanges日志
              我们已有简洁的自定义日志,无需框架级日志污染

        v7.98.0: 过滤VIX符号,防止被误加入配对池
        """
        # 添加新股票 (过滤benchmark + VIX)
        added_count = 0
        for security in changes.AddedSecurities:
            # 过滤benchmark
            if security.Symbol in self.benchmark_symbols:
                continue
            # v7.98.0: 过滤VIX符号 (防止被误配对)
            if hasattr(self, 'vix_symbol') and security.Symbol == self.vix_symbol:
                continue
            if security.Symbol not in self.symbols:
                self.symbols.append(security.Symbol)
                added_count += 1

        # 移除旧股票 (过滤benchmark)
        removed_symbols = [s.Symbol for s in changes.RemovedSecurities
                          if s.Symbol not in self.benchmark_symbols]
        self.symbols = [s for s in self.symbols if s not in removed_symbols]

        # === 输出选股结果统计 ===
        stock_count = len([s for s in self.symbols if s not in self.etf_symbols])
        etf_count = len([s for s in self.symbols if s in self.etf_symbols])

        self.Debug(
            f"[选股汇总] 新增{added_count}只 | "
            f"总计{len(self.symbols)}只 (股票{stock_count} + ETF{etf_count})",
            level=0
        )

        # === 触发分析管道 ===
        if len(self.symbols) >= 2:
            self._run_analysis_pipeline()


    def _run_analysis_pipeline(self):
        """
        运行分析管道 (v7.86.0 更新)

        完整流程 (8步):
        - 步骤1: DataProcessor 数据预处理
        - 步骤2: CointegrationAnalyzer 协整检验
        - 步骤3: IndustryQuotaManager 应用行业配额
        - 步骤4: 构建 PairData 字典
        - 步骤5: BayesianModeler 贝叶斯建模
        - 步骤6: PairSelector 配对质量筛选
        - 步骤7: 创建 Pairs 对象
        - 步骤8: PairsManager 分类管理

        Returns:
            None (结果通过PairsManager管理)
        """
        # === 步骤1: 数据处理 ===
        self.Debug("[Analysis] 步骤1: 数据处理", level=1)

        data_result = self.data_processor.process(self.symbols)
        clean_data = data_result['clean_data']
        data_valid_symbols = data_result['valid_symbols']
        stats = data_result['statistics']

        # 输出处理统计
        self.Debug(
            f"[数据处理] 输入{stats['total']}只 → "
            f"有效{stats['final_valid']}只 | "
            f"缺失{stats.get('data_missing', 0)}只 | "
            f"不完整{stats.get('incomplete', 0)}只 | "
            f"高波动{stats.get('high_volatility', 0)}只 | "
            f"极端跌幅{stats.get('extreme_drawdown', 0)}只",
            level=1
        )

        if len(data_valid_symbols) < 2:
            self.Debug("[Analysis] 有效股票不足2只,终止分析管道", level=1)
            return

        # === 步骤2: 协整检验 ===
        self.Debug("[Analysis] 步骤2: 协整检验", level=1)

        coint_result = self.cointegration_analyzer.cointegration_procedure(data_valid_symbols, clean_data)
        coint_tested_pairs = coint_result['pairs']
        coint_stats = coint_result['statistics']

        # 输出协整汇总 (在详细协整分析日志之后)
        industry_breakdown = coint_stats.get('industry_group_breakdown', {})
        industries_with_pairs = sum(1 for stats in industry_breakdown.values() if stats['pairs_found'] > 0)

        self.Debug(
            f"[协整汇总] 候选配对{coint_stats.get('total_pairs_tested', 0)}对 | "
            f"{len(industry_breakdown)}个行业 → "
            f"通过{len(coint_tested_pairs)}对 | "
            f"有效行业{industries_with_pairs}个",
            level=1
        )

        if len(coint_tested_pairs) < 1:
            self.Debug("[Analysis] 无协整配对,终止分析管道", level=1)
            return

        # === 步骤3: 应用行业配额 ===
        self.Debug("[Analysis] 步骤3: 应用行业配额", level=1)

        quota_filtered_pairs = self.industry_quota_manager.apply_quotas(coint_result)

        if len(quota_filtered_pairs) < 1:
            self.Debug("[Analysis] 无配对通过配额筛选,终止分析管道", level=1)
            return

        self.Debug(f"[配额汇总] 协整通过{len(coint_tested_pairs)}对 → 配额筛选后{len(quota_filtered_pairs)}对", level=1)

        # 缓存数据供后续步骤使用
        self.clean_data = clean_data
        self.data_valid_symbols = data_valid_symbols
        self.coint_pairs_after_quota_filtered = quota_filtered_pairs

        # === 步骤4: 构建PairData字典 ===
        pair_data = {}
        for pair_info in quota_filtered_pairs:
            pair_key = (pair_info['symbol1'], pair_info['symbol2'])
            pair_data[pair_key] = PairData.from_clean_data(pair_info, clean_data)

        # 缓存供后续步骤使用
        self.pair_data = pair_data

        self.Debug(f"[PairData] 构建{len(pair_data)}个配对数据对象", level=1)

        # === 步骤5: 贝叶斯建模 ===
        self.Debug("[Analysis] 步骤5: 贝叶斯建模", level=1)

        model_results = self.bayesian_modeler.modeling_procedure(quota_filtered_pairs, pair_data)

        if len(model_results) < 1:
            self.Debug("[Analysis] 贝叶斯建模失败,无有效结果,终止分析管道", level=1)
            return

        # 缓存供后续步骤使用
        self.model_results = model_results

        self.Debug(f"[BayesianModeler] 成功建模{len(model_results)}个配对", level=1)

        # === 步骤6: 配对质量筛选 ===
        self.Debug("[Analysis] 步骤6: 配对质量筛选", level=1)

        selected_pairs = self.pair_selector.selection_procedure(model_results)

        if len(selected_pairs) < 1:
            self.Debug("[Analysis] 无高质量配对,终止分析管道", level=1)
            return

        # 缓存供后续步骤使用
        self.selected_pairs = selected_pairs

        self.Debug(f"[PairSelector] 筛选{len(selected_pairs)}个高质量配对", level=1)

        # === 步骤7: 创建Pairs对象 ===
        self.Debug("[Analysis] 步骤7: 创建Pairs对象", level=1)

        new_pairs_dict = {}
        for model_result in selected_pairs:
            # 使用类方法工厂创建Pairs实例对象
            pair = Pairs.from_model_result(self, model_result, self.config.pairs)
            new_pairs_dict[pair.pair_id] = pair

        self.Debug(f"[Pairs] 创建{len(new_pairs_dict)}个配对对象", level=1)

        # === 步骤8: PairsManager分类管理 ===
        self.Debug("[Analysis] 步骤8: PairsManager分类", level=1)

        self.pairs_manager.classify_pairs(new_pairs_dict)

        self.Debug(
            f"[配对分析] 完成: 创建{len(new_pairs_dict)}个新配对 | "
            f"共管理{len(self.pairs_manager.all_pairs)}个配对",
            level=1
        )


    def OnData(self, data: Slice):
        """
        每日数据事件处理 (v7.98.2)

        执行流程 (按优先级):
        1. Portfolio冷却期检查 → 跳过交易
        2. Portfolio回撤检查 → 触发则 Liquidate() 全仓平仓
        3. Pair级健康检查
        4. 开仓安全检查 (VIX)

        Note: Pair级处理逻辑和正常交易逻辑将在后续版本添加
        """
        # === 数据有效性检查 ===
        if data.Count == 0:
            return

        if len(self.pairs_manager.all_pairs) == 0:
            return

        # === 1. Portfolio级: 冷却期检查 (v7.98.2: 职责分离) ===
        if self.risk_manager.is_in_portfolio_cooldown():
            return  # 冷却期内跳过所有交易

        # === 2. Portfolio级: 回撤检查 ===
        triggered, description = self.risk_manager.check_portfolio_drawdown()
        if triggered:
            self.Debug(f"[风控] {description}", level=0)
            # 全仓平仓: 直接使用QC框架 (不走Intent模式)
            self.Liquidate()
            self.risk_manager.activate_portfolio_cooldown()
            return

        # === 3. Pair级: 健康检查 ===
        health_issues = self.pairs_manager.check_pairs_health()
        total_issues = sum(len(ids) for ids in health_issues.values())

        if total_issues > 0:
            self.Debug(f"[风控] 检测到{total_issues}个配对健康问题", level=0)

            # 遍历每种问题类型,执行平仓
            # v7.98.5: issue_type.upper() 直接转换为 reason (无需额外映射表)
            for issue_type, pair_ids in health_issues.items():
                reason = issue_type.upper()  # 'anomaly' → 'ANOMALY'
                for pair_id in pair_ids:
                    pair = self.pairs_manager.get_pair_by_id(pair_id)
                    if pair is None:
                        continue

                    intent = pair.get_close_intent(reason=reason)
                    if intent:
                        success = self.order_executor.execute_close(intent)
                        if success:
                            self.Debug(f"[风控] {pair_id} 平仓成功 (原因: {reason})", level=1)

        # === 4. 开仓安全检查 ===
        if not self.risk_manager.is_safe_to_open():
            return  # 禁止开仓 (VIX恐慌)

        # TODO: 正常交易逻辑 (后续版本)


    def _subscribe_industry_etfs(self):
        """
        订阅行业ETF

        订阅优先级:
        1. 先订阅11个核心ETF (sector_etfs_enabled=True时)
        2. 再订阅7个特种部队ETF (industry_etfs_enabled=True时)
        3. 特种部队会"替换"核心ETF对应的行业代码

        最终结果:
        - self.etf_symbols: 存储Symbol对象列表 (11个或18个)
        - self.etf_industry_mapping: 存储ticker→行业代码映射 (用于后续分析模块)
        """
        config = self.config.etf_universe
        subscribed_industries = set()  # 追踪已订阅的行业代码

        # === Step 1: 订阅11个核心Sector ETFs (第一批) ===
        if config.sector_etfs_enabled:
            for ticker, industries in config.sector_etf_mapping.items():
                symbol = self.AddEquity(ticker, self.config.main.resolution).Symbol
                self.etf_symbols.append(symbol)
                self.etf_industry_mapping[ticker] = industries
                subscribed_industries.update(industries)

                # 转换行业代码为中文名称
                industry_names_list = [
                    self.config.constants['industry_names'].get(code, f'未知{code}')
                    for code in industries
                ]
                industry_names_str = '、'.join(industry_names_list)
                self.Debug(f"[订阅核心ETF] {ticker} → {industry_names_str}", level=1)

        # === Step 2: 订阅7个特种部队ETFs (第二批, 替换逻辑) ===
        if config.industry_etfs_enabled:
            for ticker, industries in config.industry_etf_mapping.items():
                symbol = self.AddEquity(ticker, self.config.main.resolution).Symbol
                self.etf_symbols.append(symbol)

                # 处理单个或多个行业代码
                if isinstance(industries, int):
                    industries = [industries]

                self.etf_industry_mapping[ticker] = industries
                subscribed_industries.update(industries)

                # 转换行业代码为中文名称
                industry_names_list = [
                    self.config.constants['industry_names'].get(code, f'未知{code}')
                    for code in industries
                ]
                industry_names_str = '、'.join(industry_names_list)
                self.Debug(f"[订阅细分ETF] {ticker} → {industry_names_str}", level=1)

        self.Debug(
            f"[ETF订阅完成] 共{len(self.etf_symbols)}个ETF "
            f"(核心{11 if config.sector_etfs_enabled else 0} + "
            f"细分{7 if config.industry_etfs_enabled else 0}), "
            f"覆盖{len(subscribed_industries)}个行业",
            level=0
        )
