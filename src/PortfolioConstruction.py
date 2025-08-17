# region imports
from AlgorithmImports import *
from collections import defaultdict
from typing import Tuple, Optional, List, Dict
# endregion

class BayesianCointegrationPortfolioConstructionModel(PortfolioConstructionModel):
    """
    贝叶斯协整投资组合构建模型 - 智能Target生成器
    
    该模型是配对交易系统的执行转换层，负责将AlphaModel生成的交易信号
    转化为具体的投资组合目标。作为智能Target生成器，不仅机械转换信号，
    还进行合理性判断，避免频繁交易和低质量建仓。
    
    核心功能:
    1. 信号解析: 从Insight.Tag中提取交易参数
    2. 质量过滤: 跳过quality_score < 0.7的低质量信号
    3. 冷却期管理: 同一配对7天内不重复建仓
    4. 动态资金管理: 基于信号质量分配5%-15%的资金
    5. Beta中性对冲: 根据协整关系计算对冲比率
    
    工作流程:
    1. 接收AlphaModel的Insights (按GroupId配对)
    2. 解析每个配对的参数 (alpha, beta, z-score, quality_score)
    3. 分类信号为平仓或建仓
    4. 处理平仓信号并记录冷却期
    5. 过滤冷却期内和低质量的建仓信号
    6. 动态分配资金并生成PortfolioTarget
    
    资金管理策略:
    - 单对仓位: 5%-15% (基于quality_score动态调整)
    - 质量阈值: quality_score < 0.7的信号被过滤
    - 现金缓冲: 5% (应对追加保证金和滑点)
    - 优先级: quality_score高的配对优先分配资金
    
    冷却期机制:
    - 平仓后7天内不接受同配对的建仓信号
    - 使用sorted tuple确保配对标识一致性
    - 自动清理过期的冷却期记录
    
    信号处理:
    - Up信号: symbol1做多, symbol2做空 (beta加权)
    - Down信号: symbol1做空, symbol2做多 (beta加权)
    - Flat信号: 两只股票都平仓，记录冷却期
    - 完全信任AlphaModel的前置过滤
    
    配置参数:
    - margin_rate: 保证金率 (默认1.0)
    - min_position_per_pair: 最小仓位 (默认5%)
    - max_position_per_pair: 最大仓位 (默认15%)
    - cash_buffer: 现金缓冲 (默认5%)
    - cooldown_days: 冷却期天数 (默认7天)
    
    与其他模块的交互:
    - AlphaModel: 提供交易信号(Insights)
    - RiskManagement: 生成的targets可能被风控调整
    - Portfolio: 查询当前持仓和可用资金
    
    注意事项:
    - 作为智能Target生成器，不仅转换还要判断
    - Beta对冲确保市场中性
    - 实盘需要检查做空能力
    - 配对标识使用sorted tuple保证一致性
    """
    
    # 常量定义
    TAG_DELIMITER = '|'
    WEIGHT_TOLERANCE = 0.01  # 权重验证容差: 允许总资金分配与预期相差1%以内
    
    def __init__(self, algorithm, config, central_pair_manager=None):
        super().__init__() 
        self.algorithm = algorithm
        self.margin_rate = config.get('margin_rate', 0.5)
        self.cash_buffer = config.get('cash_buffer', 0.05)
        self.central_pair_manager = central_pair_manager  # CPM引用
        
        # 动态资金管理参数
        self.max_position_per_pair = config.get('max_position_per_pair', 0.10)  # 单对最大仓位10%
        self.min_position_per_pair = config.get('min_position_per_pair', 0.05)  # 单对最小仓位5%
        
        # 冷却期管理
        self.cooldown_days = config.get('cooldown_days', 7)  # 冷却期天数
        self.cooldown_records = {}  # {(symbol1, symbol2): exit_time}
        
        # 市场冷静期管理
        self.spy_symbol = None  # SPY符号，延迟初始化
        self.market_severe_threshold = config.get('market_severe_threshold', 0.05)  # SPY单日跌5%触发
        self.market_cooldown_days = config.get('market_cooldown_days', 14)  # 市场冷静期天数
        self.market_cooldown_until = None  # 冷静期结束日期
        
        self.algorithm.Debug(f"[PortfolioConstruction] 初始化完成 (保证金率: {self.margin_rate}, "
                           f"单对仓位: {self.min_position_per_pair*100}%-{self.max_position_per_pair*100}%, "
                           f"现金缓冲: {self.cash_buffer*100}%, 冷却期: {self.cooldown_days}天)")
                           
    def create_targets(self, algorithm, insights) -> List[PortfolioTarget]:
        """
        创建投资组合目标 - 主入口方法
        
        该方法将AlphaModel生成的交易信号转化为具体的持仓目标。
        作为智能Target生成器，进行合理性判断后生成目标。
        
        处理流程:
        1. 检查市场冷静期（新增）
        2. 按GroupId分组Insights (确保配对同时处理)
        3. 解析每个配对的信号参数
        4. 分类信号 (建仓 vs 平仓)
        5. 处理平仓信号，记录冷却期
        6. 过滤冷却期内和低质量的建仓信号
        7. 动态分配资金给过滤后的建仓信号
        
        Args:
            algorithm: QuantConnect算法实例
            insights: AlphaModel生成的信号列表
            
        Returns:
            List[PortfolioTarget]: 目标持仓列表，每个元素指定一只股票的目标权重
        """
        # 1. 市场冷静期检查（新增）
        if self._is_market_in_cooldown():
            self.algorithm.Debug("[PC] 市场冷静期中，暂停所有新建仓")
            return []  # 冷静期内不建仓
        
        # 2. 检查并更新市场状态
        self._check_market_condition()
        
        # 按 GroupId 分组
        grouped_insights = defaultdict(list)
        for insight in insights:
            if insight.GroupId is not None:
                grouped_insights[insight.GroupId].append(insight)
            else:
                # 调试：输出没有GroupId的Insight
                self.algorithm.Debug(f"[PC] 警告: Insight没有GroupId - Symbol: {insight.Symbol.Value}, Direction: {insight.Direction}")
        
        # 收集所有建仓信号
        new_position_signals = []
        flat_signals = []
        
        # 获取当前日期用于意图提交
        current_date = int(self.algorithm.Time.strftime('%Y%m%d'))
        
        for group_id, group in grouped_insights.items():
            if len(group) != 2:
                continue
            
            insight1, insight2 = group
            symbol1, symbol2 = insight1.Symbol, insight2.Symbol
            
            # 解析beta和quality_score
            params = self._parse_tag_params(insight1.Tag)
            if params is None:
                continue
            
            # 直接使用信号方向，信任AlphaModel的前置过滤
            signal_direction = insight1.Direction
            
            if signal_direction == InsightDirection.Flat:
                flat_signals.append((symbol1, symbol2))
            else:
                new_position_signals.append({
                    'symbol1': symbol1,
                    'symbol2': symbol2,
                    'direction': signal_direction,
                    'beta': params['beta'],
                    'quality_score': params.get('quality_score', 0.5)
                })
        
        # 处理平仓信号并提交平仓意图到CPM
        targets = []
        for symbol1, symbol2 in flat_signals:
            targets.extend(self._handle_flat_signal(symbol1, symbol2))
            
            # 提交平仓意图到CPM（如果CPM存在）
            if self.central_pair_manager:
                pair_key = (symbol1.Value, symbol2.Value)
                result = self.central_pair_manager.submit_intent(
                    pair_key=pair_key,
                    action="prepare_close",
                    intent_date=current_date
                )
                if result == "accepted":
                    self.algorithm.Debug(f"[PC→CPM] 平仓意图已接受: {symbol1.Value}&{symbol2.Value}")
                elif result != "ignored_no_position":
                    self.algorithm.Debug(f"[PC→CPM] 平仓意图结果: {result} for {symbol1.Value}&{symbol2.Value}")
        
        # 清理过期的冷却期记录
        self._cleanup_expired_cooldowns()
        
        # 过滤处于冷却期的配对
        filtered_signals = []
        cooldown_filtered = 0
        
        for signal in new_position_signals:
            pair_key = tuple(sorted([signal['symbol1'], signal['symbol2']]))
            
            # 检查是否在冷却期内
            if pair_key in self.cooldown_records:
                exit_time = self.cooldown_records[pair_key]
                days_since_exit = (self.algorithm.Time - exit_time).days
                
                if days_since_exit < self.cooldown_days:
                    cooldown_filtered += 1
                    self.algorithm.Debug(
                        f"[PC-冷却期] 跳过{signal['symbol1'].Value}&{signal['symbol2'].Value}, "
                        f"距离上次平仓{days_since_exit}天 (需要{self.cooldown_days}天)"
                    )
                    continue
            
            filtered_signals.append(signal)
        
        # 动态分配资金并处理过滤后的新建仓信号
        if filtered_signals:
            # 提交开仓意图到CPM（在资金分配之前）
            if self.central_pair_manager:
                for signal in filtered_signals:
                    pair_key = (signal['symbol1'].Value, signal['symbol2'].Value)
                    result = self.central_pair_manager.submit_intent(
                        pair_key=pair_key,
                        action="prepare_open",
                        intent_date=current_date
                    )
                    if result == "accepted":
                        self.algorithm.Debug(f"[PC→CPM] 开仓意图已接受: {signal['symbol1'].Value}&{signal['symbol2'].Value}")
                    elif result not in ["ignored_duplicate"]:
                        self.algorithm.Debug(f"[PC→CPM] 开仓意图被拒绝({result}): {signal['symbol1'].Value}&{signal['symbol2'].Value}")
            
            targets.extend(self._allocate_capital_and_create_targets(filtered_signals))
        
        # 只在有实际目标时输出简洁日志
        if targets:
            # 统计类型
            flat_count = sum(1 for t in targets if t.Quantity == 0)
            new_count = len(targets) - flat_count
            if flat_count > 0 and new_count > 0:
                self.algorithm.Debug(f"[PC] 生成{len(targets)}个目标: {new_count//2}对建仓, {flat_count//2}对平仓")
            elif flat_count > 0:
                self.algorithm.Debug(f"[PC] 生成{flat_count//2}对平仓目标")
            elif new_count > 0:
                self.algorithm.Debug(f"[PC] 生成{new_count//2}对建仓目标")
        return targets

    def _parse_tag_params(self, tag: str) -> Optional[Dict]:
        """
        从Insight.Tag中解析交易参数
        
        Tag是AlphaModel传递详细参数的载体，包含了执行交易所需的所有信息。
        
        Tag格式: 'symbol1&symbol2|alpha|beta|zscore|quality_score'
        其中:
        - symbol1&symbol2: 配对的股票代码
        - alpha: 协整关系的截距项
        - beta: 对冲比率 (beta=0.8表示1份symbol1对冲0.8份symbol2)
        - zscore: 当前偏离程度 (标准化残差)
        - quality_score: 配对质量分数 (0-1，来自AlphaModel的综合评分)
        
        Args:
            tag: Insight的Tag字符串
            
        Returns:
            Dict: 解析后的参数字典，包含:
                - beta: 对冲比率
                - zscore: 偏离程度
                - quality_score: 质量分数
            失败返回None
        """
        try:
            tag_parts = tag.split(self.TAG_DELIMITER)
            if len(tag_parts) < 5:
                self.algorithm.Debug(f"[PC] Tag格式错误: {tag}")
                return None
            
            # 解析参数
            beta = float(tag_parts[2])
            zscore = float(tag_parts[3])
            quality_score = float(tag_parts[4])
            
            result = {
                'beta': beta,
                'zscore': zscore,
                'quality_score': quality_score
            }
            
            return result
        except (IndexError, ValueError) as e:
            self.algorithm.Debug(f"[PC] Tag解析失败 - Tag: {tag}, 错误: {e}")
            return None
    
    def _allocate_capital_and_create_targets(self, new_position_signals: List[Dict]) -> List[PortfolioTarget]:
        """
        动态分配资金并创建投资组合目标 - 核心资金管理逻辑
        
        该方法实现了智能的资金分配算法，根据信号质量和可用资金
        动态决定每个配对的仓位大小。已包含质量过滤(< 0.7)。
        
        分配策略:
        1. 计算当前已使用资金 (考虑保证金)
        2. 确定可用资金 (总资金 - 已用 - 缓冲)
        3. 按quality_score排序信号 (高质量优先)
        4. 过滤quality_score < 0.7的低质量信号
        5. 逐个分配资金，直到资金耗尽
        6. 根据质量分数在min和max之间调整仓位
        
        资金计算公式:
        - 做多资金 = 仓位权重 × 总资产
        - 做空资金 = 仓位权重 × 总资产 × 保证金率
        - 单对分配 = min_position + (max-min) × quality_score
        
        Args:
            new_position_signals: 已过滤冷却期的建仓信号列表，每个包含:
                - symbol1, symbol2: 配对股票
                - direction: 交易方向 (Up/Down)
                - beta: 对冲比率
                - quality_score: 质量分数 (0-1)
            
        Returns:
            List[PortfolioTarget]: 目标持仓列表
        """
        targets = []
        
        # 从框架获取可用资金
        portfolio = self.algorithm.Portfolio
        total_value = portfolio.TotalPortfolioValue
        
        # 计算可用资金比例
        # 使用Portfolio.TotalHoldingsValue来获取已投资金额
        holdings_value = portfolio.TotalHoldingsValue
        used_capital = abs(holdings_value) / total_value if total_value > 0 else 0
        
        # 可用资金 = 总资金 - 现金缓冲 - 已使用资金
        available_capital = (1.0 - self.cash_buffer) - used_capital
        if available_capital <= 0:
            # 只在确实无资金时输出
            if len(new_position_signals) > 0:
                self.algorithm.Debug(f"[PC] 资金已满(已用{used_capital:.1%}),跳过{len(new_position_signals)}个新信号")
            return targets
        
        # 按质量分数排序
        sorted_signals = sorted(new_position_signals, key=lambda x: x['quality_score'], reverse=True)
        
        # 分配资金
        allocated_count = 0
        filtered_count = 0  # 记录被过滤的低质量信号
        for signal in sorted_signals:
            # 过滤低质量信号
            if signal['quality_score'] < 0.7:
                filtered_count += 1
                continue
            
            # 检查是否还有足够资金
            if available_capital < self.min_position_per_pair:
                break
            
            # 检查做空能力
            if signal['direction'] == InsightDirection.Up and not self.can_short(signal['symbol2']):
                self._log_cannot_short(signal['symbol1'], signal['symbol2'])
                continue
            elif signal['direction'] == InsightDirection.Down and not self.can_short(signal['symbol1']):
                self._log_cannot_short(signal['symbol1'], signal['symbol2'])
                continue
            
            # 计算分配给该配对的资金
            # 基于质量分数在min和max之间分配
            allocation = self.min_position_per_pair + \
                        (self.max_position_per_pair - self.min_position_per_pair) * signal['quality_score']
            
            # 确保不超过可用资金
            allocation = min(allocation, available_capital)
            
            # 计算权重
            weights = self._calculate_pair_weights(signal['direction'], signal['beta'], allocation)
            if weights is None:
                continue
            
            symbol1_weight, symbol2_weight = weights
            
            # 记录关键交易信息
            direction_str = "做多/做空" if signal['direction'] == InsightDirection.Up else "做空/做多"
            self.algorithm.Debug(
                f"[PC-执行] {signal['symbol1'].Value}&{signal['symbol2'].Value}: "
                f"{direction_str}, 资金{allocation:.1%}, beta={signal['beta']:.2f}, 质量={signal['quality_score']:.2f}"
            )
            
            # 创建targets
            targets.extend([
                PortfolioTarget.Percent(self.algorithm, signal['symbol1'], symbol1_weight),
                PortfolioTarget.Percent(self.algorithm, signal['symbol2'], symbol2_weight)
            ])
            
            # 注意：不在这里调用register_entry
            # CPM状态更新将在OnOrderEvent中基于实际成交进行
            
            # 更新可用资金
            available_capital -= allocation
            allocated_count += 1
        
        # 输出资金分配情况
        if allocated_count > 0 or filtered_count > 0:
            total_used = (1.0 - self.cash_buffer) - available_capital
            if filtered_count > 0:
                self.algorithm.Debug(f"[PC-资金] 新建{allocated_count}对配对, 过滤{filtered_count}个低质量信号, 资金使用率{total_used:.1%}")
            else:
                self.algorithm.Debug(f"[PC-资金] 新建{allocated_count}对配对, 资金使用率{total_used:.1%}")
        return targets
    
    def _cleanup_expired_cooldowns(self):
        """
        清理过期的冷却期记录
        
        定期清理超过冷却期的记录，避免内存泄漏。
        """
        current_time = self.algorithm.Time
        expired_pairs = []
        
        for pair_key, exit_time in self.cooldown_records.items():
            days_since_exit = (current_time - exit_time).days
            if days_since_exit >= self.cooldown_days:
                expired_pairs.append(pair_key)
        
        # 清理过期记录
        for pair_key in expired_pairs:
            del self.cooldown_records[pair_key]
        
        # 如果有清理，输出日志
        if expired_pairs:
            self.algorithm.Debug(f"[PC-清理] 清理{len(expired_pairs)}个过期冷却期记录")
    
    def _handle_flat_signal(self, symbol1: Symbol, symbol2: Symbol) -> List[PortfolioTarget]:
        """
        处理平仓信号
        
        生成两只股票都平仓的目标(权重=0)。
        同时记录平仓时间用于冷却期管理。
        
        Args:
            symbol1, symbol2: 配对股票
            
        Returns:
            List[PortfolioTarget]: 两个权重为0的目标
        """
        # 记录平仓时间（使用排序后的元组作为key，确保顺序一致）
        pair_key = tuple(sorted([symbol1, symbol2]))
        self.cooldown_records[pair_key] = self.algorithm.Time
        
        self.algorithm.Debug(f"[PC-平仓] {symbol1.Value}&{symbol2.Value} (进入{self.cooldown_days}天冷却期)")
        return [
            PortfolioTarget.Percent(self.algorithm, symbol1, 0),
            PortfolioTarget.Percent(self.algorithm, symbol2, 0)
        ]
    
    def _calculate_pair_weights(self, direction: InsightDirection, beta: float, allocation: float) -> Optional[Tuple[float, float]]:
        """
        计算Beta中性配对权重 - 核心对冲算法
        
        该方法实现了Beta中性的配对交易权重计算。通过严格的
        数学推导，确保配对的市场风险暴露为零。
        
        协整关系: log(P1) = alpha + beta × log(P2)
        其中: beta表示股票1对股票2的价格敏感度
        
        权重计算原理:
        - 对于Up信号 (z>0, 股票1高估):
          * 做多股票1: 权重 = 分配资金 × beta / (m + beta)
          * 做空股票2: 权重 = -分配资金 × m / (m + beta) / m
        - 对于Down信号 (z<0, 股票1低估):
          * 做空股票1: 权重 = -分配资金 × m × beta / (1 + m × beta) / m
          * 做多股票2: 权重 = 分配资金 / (1 + m × beta)
        
        其中m是保证金率，保证权重分配满足:
        1. Beta中性: 股票市值变化按beta比例对冲
        2. 资金约束: 总资金使用 = 分配额度
        
        Args:
            direction: 信号方向 (InsightDirection.Up/Down)
            beta: 对冲比率 (来自贝叶斯模型)
            allocation: 分配给该配对的资金比例 (0-1)
            
        Returns:
            Tuple[float, float]: (symbol1_weight, symbol2_weight)
                权重为正表示做多，为负表示做空
            None: 如果权重分配异常
        """
        beta_abs = abs(beta)
        m = self.margin_rate
        c = allocation  # 使用动态分配的资金
        
        if direction == InsightDirection.Up:
            # symbol1做多, symbol2做空
            y_fund = c * beta_abs / (m + beta_abs)                      # y做多资金
            x_fund = c * m / (m + beta_abs)                             # x做空保证金
            symbol1_weight = y_fund                                     # y做多:权重 = 资金
            symbol2_weight = -x_fund / m                                # x做空:权重 = 资金/保证金率
        else:  # InsightDirection.Down
            # symbol1做空, symbol2做多
            y_fund = c * m * beta_abs / (1 + m * beta_abs)              # y做空保证金
            x_fund = c / (1 + m * beta_abs)                             # x做多资金
            symbol1_weight = -y_fund / m                                # y做空:权重 = 资金/保证金率
            symbol2_weight = x_fund                                     # x做多:权重 = 资金
        
        # 验证权重分配
        total_allocation = abs(symbol1_weight) * (m if symbol1_weight < 0 else 1) + \
                          abs(symbol2_weight) * (m if symbol2_weight < 0 else 1)
        
        if abs(total_allocation - c) > self.WEIGHT_TOLERANCE:
            self.algorithm.Debug(f"[PC] 权重分配异常: 总分配={total_allocation:.4f}, 预期={c:.4f}")
            return None
        
        return symbol1_weight, symbol2_weight
    
    def _log_cannot_short(self, symbol1: Symbol, symbol2: Symbol):
        """记录无法做空的日志"""
        self.algorithm.Debug(f"[PC-跳过] {symbol1.Value}&{symbol2.Value}: 无法做空")
    
    def can_short(self, symbol: Symbol) -> bool:
        """
        检查股票是否可以做空
        
        在实盘交易中，需要确认券商有足够的股票可供借入。
        回测环境默认允许做空。
        
        Args:
            symbol: 股票代码
            
        Returns:
            bool: True表示可以做空
        """
        if self.algorithm.LiveMode:
            security = self.algorithm.Securities[symbol]
            shortable = security.ShortableProvider.ShortableQuantity(symbol, self.algorithm.Time)
            return shortable is not None and shortable > 0
        return True  # 回测环境默认可做空
    
    def _check_market_condition(self):
        """
        检查市场条件，判断是否需要启动冷静期
        
        当SPY单日下跌超过阈值（默认5%）时，启动市场冷静期。
        使用Daily分辨率数据，比较前一交易日的涨跌幅。
        """
        # 初始化SPY（延迟初始化，避免影响其他模块）
        if self.spy_symbol is None:
            self.spy_symbol = self.algorithm.AddEquity("SPY", Resolution.Daily).Symbol
            self.algorithm.Debug("[PC] 初始化SPY监控")
        
        try:
            # 获取最近2天的历史数据
            history = self.algorithm.History(self.spy_symbol, 2, Resolution.Daily)
            
            if history.empty or len(history) < 2:
                return  # 数据不足，跳过检查
            
            # 计算前一交易日的涨跌幅
            prev_close = history['close'].iloc[-2]
            last_close = history['close'].iloc[-1]
            
            if prev_close > 0:
                daily_change = (last_close - prev_close) / prev_close
                
                # 检查是否触发冷静期
                if daily_change <= -self.market_severe_threshold:
                    from datetime import timedelta
                    self.market_cooldown_until = self.algorithm.Time.date() + timedelta(days=self.market_cooldown_days)
                    
                    self.algorithm.Debug(
                        f"[PC] 市场风险预警: SPY单日下跌{-daily_change:.2%}，"
                        f"启动{self.market_cooldown_days}天冷静期至{self.market_cooldown_until}"
                    )
                    
        except Exception as e:
            self.algorithm.Debug(f"[PC] 市场检查失败: {str(e)}")
    
    def _is_market_in_cooldown(self) -> bool:
        """
        检查是否在市场冷静期内
        
        Returns:
            bool: True表示在冷静期内，应暂停新建仓
        """
        if self.market_cooldown_until and self.algorithm.Time.date() <= self.market_cooldown_until:
            from datetime import timedelta
            days_remaining = (self.market_cooldown_until - self.algorithm.Time.date()).days
            self.algorithm.Debug(
                f"[PC] 市场冷静期还剩{days_remaining}天（至{self.market_cooldown_until}）"
            )
            return True
        return False