# region imports
from AlgorithmImports import *
from collections import defaultdict
# endregion

class BayesianCointegrationPortfolioConstructionModel(PortfolioConstructionModel):
    """
    贝叶斯协整投资组合构建模型:
    - 从 AlphaModel 获取 insights.
    - 解析 Insight Tag 获取配对信息 (symbol2, beta).
    - 为每个 Insight (代表一个配对操作) 生成独立的 PortfolioTarget 对.
      - Down: [PortfolioTarget(symbol1, -1), PortfolioTarget(symbol2, beta)]
      - Up:   [PortfolioTarget(symbol1, 1), PortfolioTarget(symbol2, -beta)]
      - Flat: [PortfolioTarget(symbol1, 0), PortfolioTarget(symbol2, 0)]
    - 不在模型内部聚合单个资产的权重,保留每个配对的独立目标.
    - 返回所有生成的 PortfolioTarget 对象的扁平列表.
    """
    def __init__(self, algorithm, config):
        super().__init__() 
        self.algorithm = algorithm
        self.margin_rate = config.get('margin_rate', 0.5)
        self.cooling_period_days = config.get('cooling_period_days', 7)
        self.pair_cooling_history = {}  # {(symbol1, symbol2): last_flat_datetime}
        self.algorithm.Debug(f"[PortfolioConstruction] 初始化完成 (保证金率: {self.margin_rate}, 冷却期: {self.cooling_period_days}天)")



    def create_targets(self, algorithm, insights):
        targets = []
        
        # 资金状态监控
        self._log_portfolio_status(algorithm)
        
        # 统计计数器
        stats = {
            'total_groups': 0,
            'valid_signals': 0,
            'ignored_signals': 0,
            'converted_signals': 0,
            'error_signals': 0
        }

        # 按 GroupId 分组
        grouped_insights = defaultdict(list)
        for insight in insights:
            if insight.GroupId is not None:
                grouped_insights[insight.GroupId].append(insight)
        
        stats['total_groups'] = len(grouped_insights)
        
        # 遍历每组 Insight
        for group_id, group in grouped_insights.items():
            pair_targets = self._process_signal_pair(group, stats)
            targets.extend(pair_targets)
        
        # No longer output statistics to reduce log volume
        return targets


    def _process_signal_pair(self, group, stats):
        """
        处理一对信号,返回有效的PortfolioTarget列表
        """
        if len(group) != 2:
            self.algorithm.Debug(f"[PC] 接收到 {len(group)} 个信号, 预期应为2, 跳过")
            stats['error_signals'] += 1
            return []
            
        insight1, insight2 = group
        symbol1, symbol2 = insight1.Symbol, insight2.Symbol
        original_direction = insight1.Direction

        # 解析Tag信息
        try:
            tag_parts = insight1.Tag.split('|')
            beta_mean = float(tag_parts[2])
            num = int(tag_parts[4])
        except Exception as e:
            self.algorithm.Debug(f"[PC] 无法解析Tag: {insight1.Tag}, 错误: {e}")
            stats['error_signals'] += 1
            return []

        # 获取当前持仓状态并验证信号
        current_position = self._get_pair_position_status(symbol1, symbol2)
        validated_direction = self._validate_signal(current_position, original_direction, symbol1, symbol2)

        if validated_direction is None:
            # 无效信号,忽略
            stats['ignored_signals'] += 1
            return []

        # 检查信号是否被转换
        if original_direction != validated_direction:
            self.algorithm.Debug(f"[PC] 信号转换: {symbol1.Value}-{symbol2.Value} [{self._direction_to_str(original_direction)}→{self._direction_to_str(validated_direction)}]")
            stats['converted_signals'] += 1
        else:
            stats['valid_signals'] += 1

        # 生成PortfolioTarget
        targets = self._BuildPairTargets(symbol1, symbol2, validated_direction, beta_mean, num)
        
        # 监控预期资金分配
        if targets and validated_direction != InsightDirection.Flat:
            self._log_expected_allocation(self.algorithm, symbol1, symbol2, targets, beta_mean, num)
        
        return targets


    def _direction_to_str(self, direction):
        """将InsightDirection转换为可读字符串"""
        if direction == InsightDirection.Up:
            return "买|卖"
        elif direction == InsightDirection.Down:
            return "卖|买"
        elif direction == InsightDirection.Flat:
            return "平仓"
        else:
            return "未知"



    def _BuildPairTargets(self, symbol1, symbol2, direction, beta, num):
        """
        按照新的资金分配算法构建目标持仓
        
        协整关系: log(symbol1) = alpha + beta × log(symbol2)
        即: symbol1 = y(因变量), symbol2 = x(自变量)
        
        资金分配目标:
        1. 实现100%资金利用率
        2. 保持Beta中性风险对冲
        """
        capital_per_pair = 1.0 / num
        beta_abs = abs(beta)
        m = self.margin_rate
        
        # Beta筛选:只在合理范围内的beta值才建仓
        if beta_abs < 0.2 or beta_abs > 3.0:
            self.algorithm.Debug(f"[PC] Beta超出范围, 跳过: {symbol1.Value}-{symbol2.Value}, beta={beta:.3f}")
            return []

        # 平仓信号
        if direction == InsightDirection.Flat:
            self.algorithm.Debug(f"[PC]: [{symbol1.Value}, {symbol2.Value}] | [FLAT, FLAT]")
            # 记录平仓时间用于冷却期计算
            self._record_cooling_history(symbol1, symbol2)
            return [PortfolioTarget.Percent(self.algorithm, symbol1, 0), PortfolioTarget.Percent(self.algorithm, symbol2, 0)]

        # 建仓信号:根据协整关系和保证金机制计算权重
        if direction == InsightDirection.Up and self.can_short(symbol2):
            # InsightDirection.Up: y(symbol1)做多,x(symbol2)做空
            # 算法:x做空出资 + y做多出资 = c,(x/m)*beta = y
            y_fund = capital_per_pair * beta_abs / (m + beta_abs)  # y做多资金
            x_fund = capital_per_pair * m / (m + beta_abs)         # x做空保证金
            
            # 转换为PortfolioTarget权重(头寸大小)
            symbol1_weight = y_fund                    # y做多:权重 = 资金
            symbol2_weight = -x_fund / m              # x做空:权重 = 资金/保证金率
            
            # 检查订单大小是否满足最小要求
            if self._check_minimum_order_size(symbol1, symbol1_weight, symbol2, symbol2_weight):
                self.algorithm.Debug(f"[PC]: [{symbol1.Value}, {symbol2.Value}] | [BUY, SELL] | [{symbol1_weight:.4f}, {symbol2_weight:.4f}] | beta={beta:.3f}")
                return [PortfolioTarget.Percent(self.algorithm, symbol1, symbol1_weight), 
                       PortfolioTarget.Percent(self.algorithm, symbol2, symbol2_weight)]
            else:
                return [] 
        
        elif direction == InsightDirection.Down and self.can_short(symbol1):
            # InsightDirection.Down: y(symbol1)做空,x(symbol2)做多
            # 算法:x做多出资 + y做空出资 = c,y/m = beta*x
            y_fund = capital_per_pair * m * beta_abs / (1 + m * beta_abs)  # y做空保证金
            x_fund = capital_per_pair / (1 + m * beta_abs)                 # x做多资金
            
            # 转换为PortfolioTarget权重(头寸大小)
            symbol1_weight = -y_fund / m              # y做空:权重 = 资金/保证金率
            symbol2_weight = x_fund                   # x做多:权重 = 资金
            
            # 检查订单大小是否满足最小要求
            if self._check_minimum_order_size(symbol1, symbol1_weight, symbol2, symbol2_weight):
                self.algorithm.Debug(f"[PC]: [{symbol1.Value}, {symbol2.Value}] | [SELL, BUY] | [{symbol1_weight:.4f}, {symbol2_weight:.4f}] | beta={beta:.3f}")
                return [PortfolioTarget.Percent(self.algorithm, symbol1, symbol1_weight), 
                       PortfolioTarget.Percent(self.algorithm, symbol2, symbol2_weight)]
            else:
                return [] 
        
        else:
            self.algorithm.Debug(f"[PC]: 无法做空,跳过配对 [{symbol1.Value}, {symbol2.Value}]")
            return []



    def _get_pair_position_status(self, symbol1: Symbol, symbol2: Symbol):
        """
        获取配对的当前持仓状态
        
        Args:
            symbol1, symbol2: 配对的两个股票
            
        Returns:
            InsightDirection: 当前持仓方向, None表示未持仓或异常状态
        """
        portfolio = self.algorithm.Portfolio
        
        # 检查两个股票是否都有持仓
        if not (portfolio[symbol1].invested and portfolio[symbol2].invested):
            return None  # 未持仓或部分持仓
        
        # 根据持仓数量判断配对方向
        s1_quantity = portfolio[symbol1].quantity
        s2_quantity = portfolio[symbol2].quantity
        
        if s1_quantity > 0 and s2_quantity < 0:
            return InsightDirection.Up  # 多symbol1,空symbol2
        elif s1_quantity < 0 and s2_quantity > 0:
            return InsightDirection.Down  # 空symbol1,多symbol2
        else:
            # 异常状态:同向持仓或其他情况
            return None


    def _validate_signal(self, current_position, signal_direction, symbol1, symbol2):
        """
        验证信号有效性,包括冷却期检查
        
        Args:
            current_position: 当前持仓方向或None
            signal_direction: 信号方向
            symbol1, symbol2: 配对股票
            
        Returns:
            InsightDirection: 验证后的有效方向, None表示应忽略
        """
        # 平仓信号:必须有持仓
        if signal_direction == InsightDirection.Flat:
            return signal_direction if current_position is not None else None
        
        # 建仓信号:检查冷却期
        if signal_direction in [InsightDirection.Up, InsightDirection.Down]:
            if self._is_in_cooling_period(symbol1, symbol2):
                return None  # 冷却期内,忽略建仓信号
        
        # 建仓信号
        if current_position is None:
            # 未持仓,可以建仓
            return signal_direction
        elif current_position == signal_direction:
            # 同方向,忽略重复信号
            return None
        else:
            # 反方向,转为平仓
            return InsightDirection.Flat


    def _is_in_cooling_period(self, symbol1: Symbol, symbol2: Symbol) -> bool:
        """
        检查配对是否在冷却期内
        
        Args:
            symbol1, symbol2: 配对股票
            
        Returns:
            bool: True表示在冷却期内,不能建仓
        """
        # 检查正向和反向键
        pair_key1 = (symbol1, symbol2)
        pair_key2 = (symbol2, symbol1)
        
        for pair_key in [pair_key1, pair_key2]:
            if pair_key in self.pair_cooling_history:
                last_flat_time = self.pair_cooling_history[pair_key]
                time_diff = self.algorithm.Time - last_flat_time
                if time_diff.days < self.cooling_period_days:
                    self.algorithm.Debug(f"[PC] 冷却期内,忽略建仓: {symbol1.Value}-{symbol2.Value} (剩余{self.cooling_period_days - time_diff.days}天)")
                    return True
        
        return False
    
    def _record_cooling_history(self, symbol1: Symbol, symbol2: Symbol):
        """
        记录配对的平仓时间,用于冷却期计算
        
        Args:
            symbol1, symbol2: 配对股票
        """
        pair_key = (symbol1, symbol2)
        self.pair_cooling_history[pair_key] = self.algorithm.Time
        self.algorithm.Debug(f"[PC] 记录冷却历史: {symbol1.Value}-{symbol2.Value} 平仓于 {self.algorithm.Time.strftime('%Y-%m-%d')}")

    def _log_portfolio_status(self, algorithm):
        """监控投资组合资金状态和保证金机制"""
        portfolio = algorithm.Portfolio
        
        total_value = portfolio.total_portfolio_value
        cash = portfolio.cash
        margin_used = portfolio.total_margin_used  
        margin_remaining = portfolio.margin_remaining
        # buying_power 使用 cash + margin_remaining 计算
        buying_power = cash + margin_remaining
        
        utilization = (margin_used / total_value) * 100 if total_value > 0 else 0
        
        # 仅在有持仓时输出资金监控信息
        if any(portfolio[s].invested for s in portfolio.keys):
            algorithm.Debug(f"[资金监控] 总资产: ${total_value:.0f}, 现金: ${cash:.0f}, "
                           f"已用保证金: ${margin_used:.0f}, 购买力: ${buying_power:.0f}, "
                           f"资金利用率: {utilization:.1f}%")
            
            # 验证保证金机制是否正常工作
            self._validate_margin_mechanism(algorithm)

    def _log_expected_allocation(self, algorithm, symbol1, symbol2, targets, beta, num):
        """记录预期资金分配，用于后续对比验证"""
        capital_per_pair = 1.0 / num
        total_portfolio_value = algorithm.Portfolio.total_portfolio_value
        expected_dollar_per_pair = capital_per_pair * total_portfolio_value
        
        symbol1_weight = targets[0].Quantity if len(targets) > 0 else 0
        symbol2_weight = targets[1].Quantity if len(targets) > 1 else 0
        
        algorithm.Debug(f"[预期分配] {symbol1.Value}-{symbol2.Value}: "
                       f"单对资金{capital_per_pair:.1%}(${expected_dollar_per_pair:.0f}), "
                       f"权重[{symbol1_weight:.4f}, {symbol2_weight:.4f}], beta={beta:.3f}")
    
    def _validate_margin_mechanism(self, algorithm):
        """验证保证金机制是否正常工作"""
        portfolio = algorithm.Portfolio
        total_short_holdings = 0
        total_long_holdings = 0
        
        # 统计所有头寸
        for symbol in portfolio.keys:
            holding = portfolio[symbol]
            if holding.invested:
                if holding.quantity < 0:  # 做空头寸
                    total_short_holdings += abs(holding.holdings_value)
                else:  # 做多头寸
                    total_long_holdings += holding.holdings_value
        
        # 理论上，做空总价值的50%应该是所需保证金
        if total_short_holdings > 0:
            expected_margin_from_shorts = total_short_holdings * 0.5
            actual_margin_used = portfolio.total_margin_used
            total_holdings_value = total_short_holdings + total_long_holdings
            
            # 计算保证金效率
            margin_ratio = actual_margin_used / expected_margin_from_shorts if expected_margin_from_shorts > 0 else 0
            
            algorithm.Debug(f"[保证金分析] 做多: ${total_long_holdings:.0f}, 做空: ${total_short_holdings:.0f}, "
                           f"预期保证金: ${expected_margin_from_shorts:.0f}, "  
                           f"实际保证金: ${actual_margin_used:.0f}, "
                           f"效率: {margin_ratio:.2f}x")
            
            # 如果保证金效率异常，可能存在问题
            if margin_ratio > 1.8:
                algorithm.Debug(f"[保证金警告] 保证金效率异常，可能做空未使用50%保证金")

    def _check_minimum_order_size(self, symbol1: Symbol, weight1: float, symbol2: Symbol, weight2: float) -> bool:
        """检查订单大小是否满足最小要求，避免minimum order size警告"""
        portfolio_value = self.algorithm.Portfolio.total_portfolio_value
        min_percentage = self.algorithm.Settings.MinimumOrderMarginPortfolioPercentage
        
        # 计算预期订单金额
        expected_value1 = abs(weight1 * portfolio_value)
        expected_value2 = abs(weight2 * portfolio_value)
        min_threshold = portfolio_value * min_percentage
        
        # 检查两个订单是否都满足最小规模要求
        order1_valid = expected_value1 >= min_threshold
        order2_valid = expected_value2 >= min_threshold
        
        # 记录详细的订单大小信息
        self.algorithm.Debug(f"[订单检查] {symbol1.Value}: ${expected_value1:.0f}({order1_valid}), "
                           f"{symbol2.Value}: ${expected_value2:.0f}({order2_valid}), "
                           f"阈值: ${min_threshold:.0f}")
        
        if not (order1_valid and order2_valid):
            # 记录被拒绝的原因
            rejected_reasons = []
            if not order1_valid:
                rejected_reasons.append(f"{symbol1.Value}(${expected_value1:.0f})")
            if not order2_valid:
                rejected_reasons.append(f"{symbol2.Value}(${expected_value2:.0f})")
            
            self.algorithm.Debug(f"[订单拒绝] 订单金额低于阈值: {', '.join(rejected_reasons)}")
            return False
        
        return True

    # 检查是否可以做空(回测环境所有股票都可以做空,实盘时需要检测)
    def can_short(self, symbol: Symbol) -> bool:
        # security = self.algorithm.Securities[symbol]
        # shortable = security.ShortableProvider.ShortableQuantity(symbol, self.algorithm.Time)
        # return shortable is not None and shortable > 0
        return True






