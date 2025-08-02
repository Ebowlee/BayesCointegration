# region imports
from AlgorithmImports import *
from collections import defaultdict
from typing import Tuple, Optional, List
# endregion

class BayesianCointegrationPortfolioConstructionModel(PortfolioConstructionModel):
    """
    贝叶斯协整投资组合构建模型 - 将交易信号转化为具体持仓
    
    该模型是配对交易系统的执行层，负责将AlphaModel生成的交易信号
    转化为具体的投资组合持仓。采用动态资金管理，根据信号质量和
    可用资金智能分配仓位。
    
    核心功能:
    1. 信号解析: 从Insight.Tag中提取交易参数
    2. 持仓验证: 检查当前持仓状态，避免重复建仓
    3. 动态资金管理: 基于信号质量分配5%-10%的资金
    4. Beta中性对冲: 根据协整关系计算对冲比率
    5. 风险控制: 冷却期管理，做空能力检查
    
    工作流程:
    1. 接收AlphaModel的Insights (按GroupId配对)
    2. 解析每个配对的参数 (beta, z-score, quality_score)
    3. 验证交易信号的有效性
    4. 收集所有有效信号
    5. 动态分配资金并生成PortfolioTarget
    
    资金管理策略:
    - 单对仓位: 5%-10% (基于quality_score动态调整)
    - 现金缓冲: 5% (应对追加保证金和滑点)
    - 优先级: quality_score高的配对优先分配资金
    - 保证金: 做空需要100%保证金(InteractiveBrokers)
    
    信号处理规则:
    - Up信号: symbol1做多, symbol2做空 (beta加权)
    - Down信号: symbol1做空, symbol2做多 (beta加权)
    - Flat信号: 两只股票都平仓
    - 反向信号: 先平仓再反向建仓 (两步执行)
    
    配置参数:
    - margin_rate: 保证金率 (默认1.0)
    - pair_reentry_cooldown_days: 平仓后冷却期 (默认14天)
    - min_position_per_pair: 最小仓位 (默认5%)
    - max_position_per_pair: 最大仓位 (默认10%)
    - cash_buffer: 现金缓冲 (默认5%)
    
    与其他模块的交互:
    - AlphaModel: 提供交易信号(Insights)
    - PairLedger: 查询配对状态和限制
    - RiskManagement: 生成的targets可能被风控调整
    - Execution: 最终执行具体订单
    
    注意事项:
    - 动态资金管理取代了固定配对数限制
    - Beta对冲确保市场中性
    - 实盘需要检查做空能力
    - 冷却期避免频繁交易同一配对
    """
    
    # 常量定义
    TAG_DELIMITER = '|'
    WEIGHT_TOLERANCE = 0.01
    
    def __init__(self, algorithm, config, pair_ledger):
        super().__init__() 
        self.algorithm = algorithm
        self.pair_ledger = pair_ledger
        self.margin_rate = config.get('margin_rate', 0.5)
        self.pair_reentry_cooldown_days = config.get('pair_reentry_cooldown_days', 7)
        self.cash_buffer = config.get('cash_buffer', 0.05)
        
        # 动态资金管理参数
        self.max_position_per_pair = config.get('max_position_per_pair', 0.10)  # 单对最大仓位10%
        self.min_position_per_pair = config.get('min_position_per_pair', 0.05)  # 单对最小仓位5%
        
        self.algorithm.Debug(f"[PortfolioConstruction] 初始化完成 (保证金率: {self.margin_rate}, "
                           f"冷却期: {self.pair_reentry_cooldown_days}天, "
                           f"单对仓位: {self.min_position_per_pair*100}%-{self.max_position_per_pair*100}%, "
                           f"现金缓冲: {self.cash_buffer*100}%)")
                           
    def create_targets(self, algorithm, insights) -> List[PortfolioTarget]:
        """
        创建投资组合目标 - 主入口方法
        
        该方法将AlphaModel生成的交易信号转化为具体的持仓目标。
        采用两阶段处理：先收集所有信号，再统一分配资金。
        
        处理流程:
        1. 按GroupId分组Insights (确保配对同时处理)
        2. 解析每个配对的信号参数
        3. 验证信号有效性 (检查当前持仓、冷却期等)
        4. 分类信号 (新建仓 vs 平仓)
        5. 优先处理平仓信号
        6. 动态分配资金给新建仓信号
        
        Args:
            algorithm: QuantConnect算法实例
            insights: AlphaModel生成的信号列表
            
        Returns:
            List[PortfolioTarget]: 目标持仓列表，每个元素指定一只股票的目标权重
        """
        # 日志：记录收到的Insights
        if insights:
            self.algorithm.Debug(f"[PC] 收到{len(insights)}个Insights")
        
        # 按 GroupId 分组
        grouped_insights = defaultdict(list)
        for insight in insights:
            if insight.GroupId is not None:
                grouped_insights[insight.GroupId].append(insight)
            else:
                # 调试：输出没有GroupId的Insight
                self.algorithm.Debug(f"[PC] 警告: Insight没有GroupId - Symbol: {insight.Symbol.Value}, Direction: {insight.Direction}")
        
        # 调试：输出分组结果
        self.algorithm.Debug(f"[PC] GroupId分组结果: {len(grouped_insights)}组")
        
        # 收集所有建仓信号
        new_position_signals = []
        flat_signals = []
        
        for group_id, group in grouped_insights.items():
            if len(group) != 2:
                continue
            
            insight1, insight2 = group
            symbol1, symbol2 = insight1.Symbol, insight2.Symbol
            
            # 解析beta和quality_score
            params = self._parse_tag_params(insight1.Tag)
            if params is None:
                continue
            
            # 获取当前持仓状态并验证信号
            current_position = self._get_pair_position_status(symbol1, symbol2)
            validated_direction = self._validate_signal(current_position, insight1.Direction, symbol1, symbol2)
            
            if validated_direction is None:
                continue
            
            if validated_direction == InsightDirection.Flat:
                flat_signals.append((symbol1, symbol2))
            else:
                new_position_signals.append({
                    'symbol1': symbol1,
                    'symbol2': symbol2,
                    'direction': validated_direction,
                    'beta': params['beta'],
                    'quality_score': params.get('quality_score', 0.5)
                })
        
        # 处理平仓信号
        targets = []
        for symbol1, symbol2 in flat_signals:
            targets.extend(self._handle_flat_signal(symbol1, symbol2))
        
        # 动态分配资金并处理新建仓信号
        if new_position_signals:
            targets.extend(self._allocate_capital_and_create_targets(new_position_signals))
        
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
            quality_score = float(tag_parts[4])  # 直接使用AlphaModel计算的quality_score
            
            return {
                'beta': beta,
                'zscore': zscore,
                'quality_score': quality_score
            }
        except (IndexError, ValueError) as e:
            self.algorithm.Debug(f"[PC] Tag解析失败 - Tag: {tag}, 错误: {e}")
            return None
    
    def _allocate_capital_and_create_targets(self, new_position_signals: List[Dict]) -> List[PortfolioTarget]:
        """
        动态分配资金并创建投资组合目标 - 核心资金管理逻辑
        
        该方法实现了智能的资金分配算法，根据信号质量和可用资金
        动态决定每个配对的仓位大小。
        
        分配策略:
        1. 计算当前已使用资金 (考虑保证金)
        2. 确定可用资金 (总资金 - 已用 - 缓冲)
        3. 按quality_score排序信号 (高质量优先)
        4. 逐个分配资金，直到资金耗尽
        5. 根据质量分数在min和max之间调整仓位
        
        资金计算公式:
        - 做多资金 = 仓位权重 × 总资产
        - 做空资金 = 仓位权重 × 总资产 × 保证金率
        - 单对分配 = min_position + (max-min) × quality_score
        
        Args:
            new_position_signals: 新建仓信号列表，每个包含:
                - symbol1, symbol2: 配对股票
                - direction: 交易方向 (Up/Down)
                - beta: 对冲比率
                - quality_score: 质量分数 (0-1)
            
        Returns:
            List[PortfolioTarget]: 目标持仓列表
        """
        targets = []
        
        # 计算当前已使用资金
        used_capital = 0
        for pair_info in self.pair_ledger.all_pairs.values():
            status = pair_info.get_position_status(self.algorithm)
            if status['has_position']:
                # 计算该配对占用的资金（考虑保证金）
                h1 = self.algorithm.Portfolio[pair_info.symbol1]
                h2 = self.algorithm.Portfolio[pair_info.symbol2]
                pair_capital = abs(h1.HoldingsValue) + abs(h2.HoldingsValue) * (self.margin_rate if h2.Quantity < 0 else 1)
                used_capital += pair_capital / self.algorithm.Portfolio.TotalPortfolioValue
        
        # 计算可用资金
        available_capital = (1.0 - self.cash_buffer) - used_capital
        if available_capital <= 0:
            self.algorithm.Debug(f"[PC] 无可用资金，跳过新建仓")
            return targets
        
        # 按质量分数排序
        sorted_signals = sorted(new_position_signals, key=lambda x: x['quality_score'], reverse=True)
        
        # 分配资金
        allocated_count = 0
        for signal in sorted_signals:
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
            
            # 记录日志
            action = "BUY, SELL" if signal['direction'] == InsightDirection.Up else "SELL, BUY"
            self.algorithm.Debug(f"[PC]: [{signal['symbol1'].Value}, {signal['symbol2'].Value}] | [{action}] | "
                               f"[{symbol1_weight:.4f}, {symbol2_weight:.4f}] | "
                               f"beta={signal['beta']:.3f} | allocation={allocation:.1%}")
            
            # 创建targets
            targets.extend([
                PortfolioTarget.Percent(self.algorithm, signal['symbol1'], symbol1_weight),
                PortfolioTarget.Percent(self.algorithm, signal['symbol2'], symbol2_weight)
            ])
            
            # 更新可用资金
            available_capital -= allocation
            allocated_count += 1
        
        # 输出资金使用情况
        total_used = (1.0 - self.cash_buffer) - available_capital
        self.algorithm.Debug(
            f"[PC] 资金分配完成: 新建仓{allocated_count}对, "
            f"资金使用{total_used:.1%}, 剩余可用{available_capital:.1%}"
        )
        return targets
    
    def _handle_flat_signal(self, symbol1: Symbol, symbol2: Symbol) -> List[PortfolioTarget]:
        """
        处理平仓信号
        
        生成两只股票都平仓的目标(权重=0)。
        PairLedger会在订单成交后自动记录平仓时间。
        
        Args:
            symbol1, symbol2: 配对股票
            
        Returns:
            List[PortfolioTarget]: 两个权重为0的目标
        """
        self.algorithm.Debug(f"[PC]: [{symbol1.Value}, {symbol2.Value}] | [FLAT, FLAT]")
        # 不再需要记录冷却期，因为PairLedger会在订单成交后自动记录last_exit_time
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
    
    def _get_pair_position_status(self, symbol1: Symbol, symbol2: Symbol) -> Optional[InsightDirection]:
        """
        获取配对的当前持仓状态
        
        通过查询Portfolio获取实时持仓信息，判断配对的当前方向。
        用于验证新信号是否与现有持仓冲突。
        
        持仓状态判断:
        - Up: symbol1做多(>0) 且 symbol2做空(≤0)
        - Down: symbol1做空(<0) 且 symbol2做多(≥0)
        - None: 无持仓或异常状态
        
        Args:
            symbol1, symbol2: 配对的两只股票
            
        Returns:
            InsightDirection: Up/Down表示当前持仓方向
            None: 无持仓
        """
        portfolio = self.algorithm.Portfolio
        s1_holding = portfolio[symbol1]
        s2_holding = portfolio[symbol2]
        
        # 快速检查：都未持仓
        if not s1_holding.Invested and not s2_holding.Invested:
            return None
        
        s1_quantity = s1_holding.Quantity
        s2_quantity = s2_holding.Quantity
        
        # 判断持仓方向
        if s1_quantity > 0 and s2_quantity <= 0:
            return InsightDirection.Up
        elif s1_quantity < 0 and s2_quantity >= 0:
            return InsightDirection.Down
        elif s1_quantity != 0 and s2_quantity != 0 and (s1_quantity > 0) == (s2_quantity > 0):
            # 异常：同向持仓
            self.algorithm.Debug(f"[PC] 警告：检测到异常同向持仓 - {symbol1.Value}: {s1_quantity}, "
                               f"{symbol2.Value}: {s2_quantity}")
        
        return None

    def _validate_signal(self, current_position: Optional[InsightDirection], 
                        signal_direction: InsightDirection, 
                        symbol1: Symbol, symbol2: Symbol) -> Optional[InsightDirection]:
        """
        验证交易信号有效性 - 信号过滤器
        
        该方法确保只有合理的信号才会被执行，避免重复建仓、
        过快反转等问题。
        
        验证规则:
        1. 平仓信号: 必须有持仓才能平仓
        2. 同向信号: 已持有相同方向，忽略
        3. 反向信号: 转为平仓 (避免直接反转)
        4. 新建仓: 检查冷却期和其他限制
        
        Args:
            current_position: 当前持仓方向 (Up/Down/None)
            signal_direction: 信号方向 (Up/Down/Flat)
            symbol1, symbol2: 配对股票
            
        Returns:
            InsightDirection: 验证后的有效方向
            None: 信号无效，应忽略
        """
        # 平仓信号：必须有持仓
        if signal_direction == InsightDirection.Flat:
            return signal_direction if current_position is not None else None
        
        # 已持仓情况
        if current_position is not None:
            # 同方向：忽略
            if current_position == signal_direction:
                return None
            # 反方向：转为平仓
            return InsightDirection.Flat
        
        # 新建仓检查
        if not self._can_open_new_position(symbol1, symbol2):
            return None
            
        return signal_direction
    
    def _can_open_new_position(self, symbol1: Symbol, symbol2: Symbol) -> bool:
        """
        检查是否可以开新仓
        
        通过PairLedger的统一接口检查各种交易限制。
        
        检查项目:
        1. 配对是否在本轮被发现
        2. 是否正在冷却期内
        3. 是否已有持仓
        
        Args:
            symbol1, symbol2: 配对股票
            
        Returns:
            bool: True表示可以开仓
        """
        # 使用PairLedger的统一检查方法
        if not self.pair_ledger.is_tradeable(symbol1, symbol2, self.pair_reentry_cooldown_days):
            return False
        
        return True
    
    # 冷却期检查已移至PairLedger.is_tradeable方法中
    
    def _log_cannot_short(self, symbol1: Symbol, symbol2: Symbol):
        """记录无法做空的日志"""
        self.algorithm.Debug(f"[PC]: 无法做空,跳过配对 [{symbol1.Value}, {symbol2.Value}]")
    
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