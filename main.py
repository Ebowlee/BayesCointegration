# region imports
from AlgorithmImports import *
from System import Action
from datetime import datetime, timedelta
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

        # === 初始化风控状态 ===
        self.portfolio_high_water_mark = self.config.main['cash']  # 历史最高净值
        self.strategy_cooldown_until = None  # 策略冷却截止时间
        self.pair_high_water_marks = {}  # 配对历史最高净值 {pair_id: max_value}

        # === 添加市场基准 ===
        self.market_benchmark = self.AddEquity("SPY", Resolution.Daily).Symbol
        self.market_volatility_cooldown_until = None  # 市场波动冷却截止时间

        self.Debug("[Initialize] 策略初始化完成")
    


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
        self.pairs_manager.create_pairs_from_models(modeled_pairs)
        self.Debug(f"[配对分析] 完成: PairsManager管理{len(self.pairs_manager.all_pairs)}个配对")
    
    

    def _check_account_blowup(self) -> bool:
        """
        检查账户爆仓风险 - 基于初始资金
        返回: True=触发爆仓线, False=安全
        """
        portfolio_value = self.Portfolio.TotalPortfolioValue
        initial_capital = self.config.main['cash']
        remaining_ratio = portfolio_value / initial_capital

        # 从配置读取阈值
        BLOWUP_THRESHOLD = self.config.risk_management['blowup_threshold']

        if remaining_ratio < BLOWUP_THRESHOLD:
            self.Debug(
                f"[爆仓风控] 触发！"
                f"当前:{portfolio_value:,.0f} "
                f"初始:{initial_capital:,.0f} "
                f"剩余:{remaining_ratio:.1%}"
            )
            return True

        return False


    def _check_max_drawdown(self) -> bool:
        """
        检查最大回撤 - 基于历史最高点
        返回: True=触发回撤线, False=正常
        """
        portfolio_value = self.Portfolio.TotalPortfolioValue

        # 更新历史最高点
        if not hasattr(self, 'portfolio_high_water_mark'):
            self.portfolio_high_water_mark = portfolio_value
        else:
            self.portfolio_high_water_mark = max(
                self.portfolio_high_water_mark,
                portfolio_value
            )

        # 计算回撤
        drawdown = 0
        if self.portfolio_high_water_mark > 0:
            drawdown = (self.portfolio_high_water_mark - portfolio_value) / self.portfolio_high_water_mark

        # 从配置读取阈值
        MAX_DRAWDOWN_THRESHOLD = self.config.risk_management['drawdown_threshold']

        if drawdown > MAX_DRAWDOWN_THRESHOLD:
            self.Debug(
                f"[回撤风控] 触发！"
                f"最高点:{self.portfolio_high_water_mark:,.0f} "
                f"当前:{portfolio_value:,.0f} "
                f"回撤:{drawdown:.1%}"
            )
            return True

        return False


    def _check_market_volatility(self) -> bool:
        """
        检查市场剧烈波动
        返回: True=市场波动过大, False=正常
        """
        # 获取SPY的当日数据
        if not self.Securities.ContainsKey(self.market_benchmark):
            return False

        spy = self.Securities[self.market_benchmark]

        # 计算日内波动率 (High-Low)/Open
        if spy.Open == 0:
            return False

        daily_volatility = (spy.High - spy.Low) / spy.Open

        # 从配置读取阈值
        MARKET_SEVERE_THRESHOLD = self.config.analysis['market_severe_threshold']

        if daily_volatility > MARKET_SEVERE_THRESHOLD:
            self.Debug(
                f"[市场波动风控] 触发！"
                f"SPY日内波动:{daily_volatility:.2%} > 阈值:{MARKET_SEVERE_THRESHOLD:.2%}"
            )
            return True

        return False


    def OnData(self, data: Slice):
        """处理实时数据 - OnData架构的核心"""

        # === 检查策略冷却状态 ===
        if self.strategy_cooldown_until:
            if self.Time < self.strategy_cooldown_until:
                # 仍在冷却期内
                return
            else:
                # 冷却期结束，重置状态
                self.Debug(f"[风控恢复] 冷却期结束，恢复正常交易")
                self.strategy_cooldown_until = None

        # 如果正在分析，跳过
        if self.is_analyzing:
            return

        # 如果没有可交易配对，跳过
        if not self.pairs_manager.has_tradeable_pairs():
            return

        # === Portfolio层面风控（最高优先级）===

        # 1. 账户爆仓风控（生死线）
        if self._check_account_blowup():
            self.Debug("[爆仓风控] 执行清仓并永久停止策略")
            # 清仓所有持仓
            self.liquidate()
            # 设置永久冷却
            cooldown_days = self.config.risk_management['blowup_cooldown_days']
            self.strategy_cooldown_until = self.Time + timedelta(days=cooldown_days)
            self.Debug(f"[爆仓风控] 策略将冷却{cooldown_days}天至{self.strategy_cooldown_until.date()}")
            return  # 退出当前bar的执行


        # 2. 最大回撤风控（利润保护）
        if self._check_max_drawdown():
            self.Debug("[回撤风控] 执行清仓并暂时冷却策略")
            # 清仓所有持仓
            self.liquidate()
            # 设置冷却期
            cooldown_days = self.config.risk_management['drawdown_cooldown_days']
            self.strategy_cooldown_until = self.Time + timedelta(days=cooldown_days)
            self.Debug(f"[回撤风控] 策略将冷却{cooldown_days}天至{self.strategy_cooldown_until.date()}")
            return  # 退出当前bar的执行

        # 3. 市场剧烈波动风控（市场环境）
        if self._check_market_volatility():
            self.Debug("[市场波动风控] 市场波动过大，冷却新开仓14天")
            # 设置冷却期
            cooldown_days = self.config.analysis['market_cooldown_days']  # 14天
            self.strategy_cooldown_until = self.Time + timedelta(days=cooldown_days)
            self.Debug(f"[市场波动风控] 冷却新开仓至{self.strategy_cooldown_until.date()}")
            # 继续执行，不return

        # 4. 行业集中度风控
        sector_concentrations = self.pairs_manager.get_sector_concentration()
        threshold = self.config.risk_management['sector_exposure_threshold']
        target = self.config.risk_management['sector_target_exposure']

        for sector, info in sector_concentrations.items():
            if info['concentration'] > threshold:
                self.Debug(
                    f"[行业集中度风控] {sector}行业超限! "
                    f"集中度:{info['concentration']:.1%} > {threshold:.0%}"
                )

                # 计算减仓比例
                reduction_ratio = target / info['concentration']

                # 对该行业所有配对同比例减仓
                for pair in info['pairs']:
                    pair.reduce_position(reduction_ratio)

                self.Debug(
                    f"[行业集中度风控] {sector}行业{info['pair_count']}个配对 "
                    f"同比例减仓至{target:.0%}"
                )


        # === 正常交易逻辑 ===
        # 获取可交易配对
        tradeable_pairs = self.pairs_manager.get_all_tradeable_pairs()

        # === 配对层面风控和交易逻辑 ===
        for pair in tradeable_pairs:
            # 1. 持仓超期检查
            holding_days = pair.get_pair_holding_days()
            if holding_days is not None and holding_days > pair.max_holding_days:
                # 超期，清仓该配对
                self.Debug(
                    f"[配对风控] {pair.pair_id} 持仓{holding_days}天"
                    f"超过限制{pair.max_holding_days}天，执行清仓"
                )
                self.Liquidate(pair.symbol1)
                self.Liquidate(pair.symbol2)
                continue  # 跳过该配对的其他处理

            # 2. 异常持仓检查（单边持仓或方向相同）
            position_info = pair.get_position_info()

            # 检查异常状态
            is_partial = position_info['status'] == 'PARTIAL'
            is_same_direction = position_info['direction'] == 'same_direction'

            if is_partial or is_same_direction:
                # 构建异常描述
                if is_partial:
                    reason = "单边持仓"
                else:  # is_same_direction
                    reason = f"两腿方向相同({position_info['qty1']:+.0f}/{position_info['qty2']:+.0f})"

                self.Debug(f"[配对风控] {pair.pair_id} 发现{reason}，执行清仓")

                # 清理所有持仓
                if position_info['qty1'] != 0:
                    self.Liquidate(pair.symbol1)
                if position_info['qty2'] != 0:
                    self.Liquidate(pair.symbol2)

                continue

            # 3. 配对最大回撤检查
            if pair.get_position_status() == 'NORMAL':
                pair_value = pair.get_position_value()
                pair_id = pair.pair_id

                # 更新或初始化high_water_mark
                if pair_id not in self.pair_high_water_marks:
                    self.pair_high_water_marks[pair_id] = pair_value
                else:
                    self.pair_high_water_marks[pair_id] = max(
                        self.pair_high_water_marks[pair_id],
                        pair_value
                    )

                # 计算回撤
                hwm = self.pair_high_water_marks[pair_id]
                if hwm > 0:
                    drawdown = (hwm - pair_value) / hwm

                    MAX_PAIR_DD = self.config.risk_management['max_pair_drawdown']  # 20%

                    if drawdown > MAX_PAIR_DD:
                        self.Debug(
                            f"[配对风控] {pair.pair_id} 回撤{drawdown:.1%}"
                            f"超过限制{MAX_PAIR_DD:.0%}（HWM:{hwm:.0f}→{pair_value:.0f}），执行清仓"
                        )
                        self.Liquidate(pair.symbol1)
                        self.Liquidate(pair.symbol2)
                        # 清理high_water_mark记录
                        del self.pair_high_water_marks[pair_id]
                        continue

            # TODO: 实现正常交易信号处理
