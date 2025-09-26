# region imports
from AlgorithmImports import *
from System import Action
from src.config import StrategyConfig
from src.UniverseSelection import MyUniverseSelectionModel
from src.Pairs import Pairs
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

        # === 初始化核心数据结构 ===
        self.symbols = []  # 当前选中的股票列表

        # === 初始化状态管理 ===
        self.is_analyzing = False  # 是否正在分析
        self.last_analysis_time = None  # 上次分析时间

        self.Debug("[Initialize] 策略初始化完成")
    


    def OnSecuritiesChanged(self, changes: SecurityChanges):
        """处理证券变更事件 - 触发配对分析"""

        # 添加新股票
        for security in changes.AddedSecurities:
            if security.Symbol not in self.symbols:
                self.symbols.append(security.Symbol)

        # 移除旧股票
        removed_symbols = [s.Symbol for s in changes.RemovedSecurities]
        self.symbols = [s for s in self.symbols if s not in removed_symbols]

        # === 触发配对分析 ===
        if len(self.symbols) >= 2:
            self.Debug(f"[OnSecuritiesChanged] 当前股票池{len(self.symbols)}只，开始配对分析")

            # 标记正在分析
            self.is_analyzing = True
            self.last_analysis_time = self.Time

            # 执行分析流程
            self._analyze_and_create_pairs()

            # 分析完成
            self.is_analyzing = False
        else:
            self.Debug(f"[OnSecuritiesChanged] 股票数量不足({len(self.symbols)}只)，跳过分析")



    def _analyze_and_create_pairs(self):
        """执行配对分析流程（步骤1-5）"""

        # === 步骤1: 数据处理 ===
        data_result = self.data_processor.process(self.symbols)
        clean_data = data_result['clean_data']
        valid_symbols = data_result['valid_symbols']

        if len(valid_symbols) < 2:
            self.Debug(f"[配对分析] 有效股票不足({len(valid_symbols)}只)，结束分析")
            return

        self.Debug(f"[配对分析] 步骤1完成: {len(valid_symbols)}只股票数据有效")

        # === 步骤2: 协整检验 ===
        cointegration_result = self.cointegration_analyzer.find_cointegrated_pairs(
            valid_symbols,
            clean_data,
            self.config.sector_code_to_name
        )
        raw_pairs = cointegration_result['raw_pairs']

        if not raw_pairs:
            self.Debug("[配对分析] 未发现协整配对，结束分析")
            return

        self.Debug(f"[配对分析] 步骤2完成: 发现{len(raw_pairs)}个协整配对")

        # === 步骤3&4: 质量评估和配对筛选 ===
        selected_pairs = self.pair_selector.evaluate_and_select(raw_pairs, clean_data)
        self.Debug(f"[配对分析] 步骤3&4完成: 评估并筛选出{len(selected_pairs)}个最佳配对")

        if not selected_pairs:
            self.Debug("[配对分析] 筛选后无合格配对，结束分析")
            return

        # 显示前3个配对
        for pair in selected_pairs[:3]:
            self.Debug(f"  - {pair['symbol1'].Value}&{pair['symbol2'].Value}: "f"质量分数{pair['quality_score']:.3f}")

        # === 步骤5: 贝叶斯建模 ===
        modeling_result = self.bayesian_modeler.model_pairs(selected_pairs, clean_data)
        modeled_pairs = modeling_result['modeled_pairs']

        if not modeled_pairs:
            self.Debug("[配对分析] 建模失败，结束分析")
            return

        self.Debug(f"[配对分析] 步骤5完成: {len(modeled_pairs)}个配对建模成功")

        # === 步骤6: 创建并管理Pairs对象 ===
        self.pairs_manager.create_and_update_pairs(modeled_pairs)
        self.Debug(f"[配对分析] 完成: PairsManager管理{len(self.pairs_manager.all_pairs)}个配对")
    
    

    def OnData(self, data: Slice):
        """处理实时数据 - OnData架构的核心"""

        # 如果正在分析，跳过
        if self.is_analyzing:
            return

        # 如果没有可交易配对，跳过
        if not self.pairs_manager.has_tradeable_pairs():
            return

        # 获取可交易配对
        tradeable_pairs = self.pairs_manager.get_all_tradeable_pairs()

        # TODO: 实现交易逻辑
        # - 遍历配对，计算z-score
        # - 生成交易信号
        # - 执行订单
        # - 检查风控
