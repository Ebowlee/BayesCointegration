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
# endregion


class BayesianCointegrationStrategy(QCAlgorithm):
    """v8.0.0: UniverseSelection验证版本 - 仅保留选股逻辑"""

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

        # === v8.0.0: ETF订阅 ===
        self.etf_symbols = []                                   # 存储已订阅的ETF Symbol对象
        self.etf_industry_mapping = {}                          # 反向映射: {ticker: [industry_codes]}
        if self.config.etf_universe.enabled:
            self._subscribe_industry_etfs()

        # === v8.0.1: 初始化分析管道 ===
        self.data_processor = DataProcessor(self, self.config.analysis)

        # === v8.1.0: 初始化协整分析器 ===
        self.cointegration_analyzer = CointegrationAnalyzer(self, self.config.cointegration_analyzer)

        # === v8.3.0: 初始化贝叶斯建模器 ===
        self.bayesian_modeler = BayesianModeler(
            self,
            self.config.analysis,
            self.config.bayesian_modeler
        )

        # === v8.4.0: 初始化配对选择器 ===
        self.pair_selector = PairSelector(
            self,
            self.config.analysis,
            self.config.pair_selector
        )


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
        """
        # 添加新股票 (过滤benchmark)
        added_count = 0
        for security in changes.AddedSecurities:
            if security.Symbol in self.benchmark_symbols:
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

        # === v8.0.1: 触发分析管道 ===
        if len(self.symbols) >= 2:
            self._run_analysis_pipeline()


    def _run_analysis_pipeline(self):
        """
        运行分析管道 (v8.4.0 PairSelector验证版本)

        完整流程 (7步):
        - 步骤1: DataProcessor - 数据处理 ✅
        - 步骤2: CointegrationAnalyzer - 协整检验 ✅
        - 步骤3: 构建PairData字典 ✅
        - 步骤4: BayesianModeler - 贝叶斯建模 ✅
        - 步骤5: PairSelector - 质量筛选 ✅
        - 步骤6: 创建Pairs对象 (待恢复)
        - 步骤7: PairsManager管理 (待恢复)
        """
        # === 步骤1: 数据处理 (v8.0.1) ===
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

        # === 步骤2: 协整检验 (v7.65.0: 不再应用配额) ===
        self.Debug("[Analysis] 步骤2: 协整检验", level=1)

        coint_result = self.cointegration_analyzer.cointegration_procedure(data_valid_symbols, clean_data)
        raw_coint_pairs = coint_result['raw_pairs']
        coint_stats = coint_result['statistics']

        # 输出协整汇总 (在详细协整分析日志之后)
        industry_breakdown = coint_stats.get('industry_group_breakdown', {})
        industries_with_pairs = sum(1 for stats in industry_breakdown.values() if stats['pairs_found'] > 0)

        self.Debug(
            f"[协整汇总] 候选配对{coint_stats.get('total_pairs_tested', 0)}对 | "
            f"{len(industry_breakdown)}个行业 → "
            f"通过{len(raw_coint_pairs)}对 | "
            f"有效行业{industries_with_pairs}个",
            level=1
        )

        if len(raw_coint_pairs) < 1:
            self.Debug("[Analysis] 无协整配对,终止分析管道", level=1)
            return

        # === 步骤3: 应用行业配额 (v7.65.0: 新增步骤) ===
        self.Debug("[Analysis] 步骤3: 应用行业配额", level=1)

        selected_pairs = self.industry_quota_manager.apply_quotas(coint_result)

        if len(selected_pairs) < 1:
            self.Debug("[Analysis] 无配对通过配额筛选,终止分析管道", level=1)
            return

        self.Debug(
            f"[配额汇总] 协整通过{len(raw_coint_pairs)}对 → 配额筛选后{len(selected_pairs)}对",
            level=1
        )

        # 缓存数据供后续步骤使用
        self.clean_data = clean_data
        self.data_valid_symbols = data_valid_symbols
        self.coint_pairs = selected_pairs  # v7.65.0: 使用配额筛选后的配对

        # === 步骤4: 构建PairData字典 ===
        pair_data = {}
        for pair_info in selected_pairs:
            pair_key = (pair_info['symbol1'], pair_info['symbol2'])
            pair_data[pair_key] = PairData.from_clean_data(pair_info, clean_data)

        # 缓存供后续步骤使用
        self.pair_data = pair_data

        self.Debug(
            f"[PairData] 构建{len(pair_data)}个配对数据对象",
            level=1
        )

        # === 步骤5: 贝叶斯建模 (v7.65.0: 步骤编号调整) ===
        self.Debug("[Analysis] 步骤5: 贝叶斯建模", level=1)

        model_results = self.bayesian_modeler.modeling_procedure(selected_pairs, pair_data)

        if len(model_results) < 1:
            self.Debug("[Analysis] 贝叶斯建模失败,无有效结果,终止分析管道", level=1)
            return

        # 缓存供后续步骤使用
        self.model_results = model_results

        self.Debug(
            f"[BayesianModeler] 成功建模{len(model_results)}个配对",
            level=1
        )

        # === 步骤5: 配对质量筛选 (v8.4.0) ===
        self.Debug("[Analysis] 步骤5: 配对质量筛选", level=1)

        selected_pairs = self.pair_selector.selection_procedure(model_results)

        if len(selected_pairs) < 1:
            self.Debug("[Analysis] 无高质量配对,终止分析管道", level=1)
            return

        # 缓存供后续步骤使用
        self.selected_pairs = selected_pairs

        self.Debug(f"[PairSelector] 筛选{len(selected_pairs)}个高质量配对", level=1)

        self.Debug(f"[Analysis] 步骤5完成 - 等待步骤6-7恢复", level=1)


    def _subscribe_industry_etfs(self):
        """
        订阅行业ETF (v8.0.0)

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
