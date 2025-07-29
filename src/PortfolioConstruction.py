# region imports
from AlgorithmImports import *
from collections import defaultdict
# endregion

class BayesianCointegrationPortfolioConstructionModel(PortfolioConstructionModel):
    """
    贝叶斯协整投资组合构建模型:
    - 从 AlphaModel 获取按 GroupId 分组的 insights
    - 解析 Insight Tag 获取 beta 参数
    - 根据信号方向生成配对的 PortfolioTarget:
      - Up: symbol1做多, symbol2做空
      - Down: symbol1做空, symbol2做多
      - Flat: 平仓两只股票
    - 实现资金管理: 最多8对配对, 5%现金缓冲
    - 通过 PairLedger 跟踪配对状态
    """
    def __init__(self, algorithm, config, pair_ledger):
        super().__init__() 
        self.algorithm = algorithm
        self.pair_ledger = pair_ledger
        self.margin_rate = config.get('margin_rate', 0.5)
        self.pair_reentry_cooldown_days = config.get('pair_reentry_cooldown_days', 7)
        self.max_pairs = config.get('max_pairs', 8)
        self.cash_buffer = config.get('cash_buffer', 0.05)
        self.pair_cooling_history = {}  # {(symbol1, symbol2): last_flat_datetime}
        self.algorithm.Debug(f"[PortfolioConstruction] 初始化完成 (保证金率: {self.margin_rate}, 冷却期: {self.pair_reentry_cooldown_days}天, 最大配对数: {self.max_pairs}, 现金缓冲: {self.cash_buffer*100}%)")
    def create_targets(self, algorithm, insights):
        targets = []
        
        # 按 GroupId 分组
        grouped_insights = defaultdict(list)
        for insight in insights:
            if insight.GroupId is not None:
                grouped_insights[insight.GroupId].append(insight)
        
        # 遍历每组 Insight
        for group_id, group in grouped_insights.items():
            pair_targets = self._process_signal_pair(group)
            targets.extend(pair_targets)
        
        return targets


    def _process_signal_pair(self, group):
        """
        处理一对信号,返回有效的PortfolioTarget列表
        """
        if len(group) != 2:
            return []
            
        insight1, insight2 = group
        symbol1, symbol2 = insight1.Symbol, insight2.Symbol
        original_direction = insight1.Direction

        # 解析Tag信息
        try:
            tag_parts = insight1.Tag.split('|')
            beta_mean = float(tag_parts[2])
        except Exception as e:
            self.algorithm.Debug(f"[PC] Tag解析失败 - Tag: {insight1.Tag}, 预期格式: 'symbol1&symbol2|alpha|beta|zscore', 错误: {e}")
            return []

        # 获取当前持仓状态并验证信号
        current_position = self._get_pair_position_status(symbol1, symbol2)
        validated_direction = self._validate_signal(current_position, original_direction, symbol1, symbol2)

        if validated_direction is None:
            # 无效信号,忽略
            return []

        # 生成PortfolioTarget
        targets = self._BuildPairTargets(symbol1, symbol2, validated_direction, beta_mean)
        
        return targets
    def _BuildPairTargets(self, symbol1, symbol2, direction, beta):
        """
        按照新的资金分配算法构建目标持仓
        
        协整关系: log(symbol1) = alpha + beta × log(symbol2)
        即: symbol1 = y(因变量), symbol2 = x(自变量)
        
        资金分配目标:
        1. 考虑现金缓冲后的资金利用率
        2. 保持Beta中性风险对冲
        """
        # 计算每对可用资金 = (总资金 - 缓冲) / 最大配对数
        available_capital = 1.0 - self.cash_buffer
        capital_per_pair = available_capital / self.max_pairs
        beta_abs = abs(beta)
        m = self.margin_rate
        
        # 平仓信号
        if direction == InsightDirection.Flat:
            self.algorithm.Debug(f"[PC]: [{symbol1.Value}, {symbol2.Value}] | [FLAT, FLAT]")
            # 记录平仓时间用于冷却期计算
            self._record_cooling_history(symbol1, symbol2)
            # 更新配对状态为非活跃
            self.pair_ledger.set_pair_status(symbol1, symbol2, False)
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
            
            # 验证权重分配
            total_allocation = abs(symbol1_weight) + abs(symbol2_weight * m)
            if abs(total_allocation - capital_per_pair) > 0.01:
                self.algorithm.Debug(f"[PC] 权重分配异常: 总分配={total_allocation:.4f}, 预期={capital_per_pair:.4f}")
            
            self.algorithm.Debug(f"[PC]: [{symbol1.Value}, {symbol2.Value}] | [BUY, SELL] | [{symbol1_weight:.4f}, {symbol2_weight:.4f}] | beta={beta:.3f}")
            # 更新配对状态为活跃
            self.pair_ledger.set_pair_status(symbol1, symbol2, True)
            return [PortfolioTarget.Percent(self.algorithm, symbol1, symbol1_weight), 
                   PortfolioTarget.Percent(self.algorithm, symbol2, symbol2_weight)] 
        
        elif direction == InsightDirection.Down and self.can_short(symbol1):
            # InsightDirection.Down: y(symbol1)做空,x(symbol2)做多
            # 算法:x做多出资 + y做空出资 = c,y/m = beta*x
            y_fund = capital_per_pair * m * beta_abs / (1 + m * beta_abs)  # y做空保证金
            x_fund = capital_per_pair / (1 + m * beta_abs)                 # x做多资金
            
            # 转换为PortfolioTarget权重(头寸大小)
            symbol1_weight = -y_fund / m              # y做空:权重 = 资金/保证金率
            symbol2_weight = x_fund                   # x做多:权重 = 资金
            
            # 验证权重分配
            total_allocation = abs(symbol1_weight * m) + abs(symbol2_weight)
            if abs(total_allocation - capital_per_pair) > 0.01:
                self.algorithm.Debug(f"[PC] 权重分配异常: 总分配={total_allocation:.4f}, 预期={capital_per_pair:.4f}")
            
            self.algorithm.Debug(f"[PC]: [{symbol1.Value}, {symbol2.Value}] | [SELL, BUY] | [{symbol1_weight:.4f}, {symbol2_weight:.4f}] | beta={beta:.3f}")
            # 更新配对状态为活跃
            self.pair_ledger.set_pair_status(symbol1, symbol2, True)
            return [PortfolioTarget.Percent(self.algorithm, symbol1, symbol1_weight), 
                   PortfolioTarget.Percent(self.algorithm, symbol2, symbol2_weight)] 
        
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
        
        # 获取持仓状态
        s1_invested = portfolio[symbol1].Invested
        s2_invested = portfolio[symbol2].Invested
        
        # 如果两只股票都没有持仓，返回None
        if not s1_invested and not s2_invested:
            return None
        
        # 获取持仓数量
        s1_quantity = portfolio[symbol1].Quantity if s1_invested else 0
        s2_quantity = portfolio[symbol2].Quantity if s2_invested else 0
        
        # 判断配对方向（即使只有部分成交也能识别）
        if s1_quantity > 0 and s2_quantity <= 0:
            # 多symbol1，空symbol2（或准备空）
            if s2_quantity < 0 or (s2_quantity == 0 and s1_invested):
                return InsightDirection.Up
        elif s1_quantity < 0 and s2_quantity >= 0:
            # 空symbol1，多symbol2（或准备多）
            if s2_quantity > 0 or (s2_quantity == 0 and s1_invested):
                return InsightDirection.Down
        elif s1_quantity != 0 and s2_quantity != 0 and (s1_quantity > 0) == (s2_quantity > 0):
            # 异常状态：同向持仓
            self.algorithm.Debug(f"[PC] 警告：检测到异常同向持仓 - {symbol1.Value}: {s1_quantity}, {symbol2.Value}: {s2_quantity}")
            return None
        
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
            # 未持仓，检查是否达到最大配对数
            if self.pair_ledger.get_active_pairs_count() >= self.max_pairs:
                return None  # 已达到最大配对数，忽略新建仓信号
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
                if time_diff.days < self.pair_reentry_cooldown_days:
                    return True
        
        return False
    
    def _record_cooling_history(self, symbol1: Symbol, symbol2: Symbol):
        """
        记录配对的平仓时间,用于冷却期计算
        
        Args:
            symbol1, symbol2: 配对股票
        """
        # 记录双向配对以防止反向建仓绕过冷却期
        self.pair_cooling_history[(symbol1, symbol2)] = self.algorithm.Time
        self.pair_cooling_history[(symbol2, symbol1)] = self.algorithm.Time
    
    def can_short(self, symbol: Symbol) -> bool:
        """
        检查是否可以做空(回测环境所有股票都可以做空,实盘时需要检测)
        """
        if self.algorithm.LiveMode:
            security = self.algorithm.Securities[symbol]
            shortable = security.ShortableProvider.ShortableQuantity(symbol, self.algorithm.Time)
            return shortable is not None and shortable > 0
        return True  # 回测环境默认可做空