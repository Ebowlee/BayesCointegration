# region imports
from AlgorithmImports import *
from collections import defaultdict
from typing import List, Tuple, Optional
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
    
    # Constants
    TAG_EXPECTED_FORMAT = "'symbol1&symbol2|alpha|beta|zscore'"
    WEIGHT_TOLERANCE = 0.01
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


    def _process_signal_pair(self, group: List[Insight]) -> List[PortfolioTarget]:
        """
        处理一对信号,返回有效的PortfolioTarget列表
        """
        if len(group) != 2:
            return []
            
        insight1, insight2 = group
        symbol1, symbol2 = insight1.Symbol, insight2.Symbol
        original_direction = insight1.Direction

        # 解析Tag信息
        beta_mean = self._parse_beta_from_tag(insight1.Tag)
        if beta_mean is None:
            return []

        # 获取当前持仓状态并验证信号
        current_position = self._get_pair_position_status(symbol1, symbol2)
        validated_direction = self._validate_signal(current_position, original_direction, symbol1, symbol2)

        if validated_direction is None:
            return []

        # 生成PortfolioTarget
        return self._BuildPairTargets(symbol1, symbol2, validated_direction, beta_mean)
    
    def _parse_beta_from_tag(self, tag: str) -> Optional[float]:
        """解析Tag中的beta参数"""
        try:
            tag_parts = tag.split('|')
            return float(tag_parts[2])
        except Exception as e:
            self.algorithm.Debug(f"[PC] Tag解析失败 - Tag: {tag}, 预期格式: {self.TAG_EXPECTED_FORMAT}, 错误: {e}")
            return None
    def _BuildPairTargets(self, symbol1: Symbol, symbol2: Symbol, direction: InsightDirection, beta: float) -> List[PortfolioTarget]:
        """
        按照新的资金分配算法构建目标持仓
        
        协整关系: log(symbol1) = alpha + beta × log(symbol2)
        即: symbol1 = y(因变量), symbol2 = x(自变量)
        
        资金分配目标:
        1. 考虑现金缓冲后的资金利用率
        2. 保持Beta中性风险对冲
        """
        # 平仓信号
        if direction == InsightDirection.Flat:
            return self._create_flat_targets(symbol1, symbol2)
        
        # 建仓信号:计算权重
        weights = self._calculate_pair_weights(direction, beta)
        if weights is None:
            return []
        
        symbol1_weight, symbol2_weight = weights
        
        # 检查是否可以做空
        if not self._can_execute_trade(symbol1, symbol2, direction):
            self.algorithm.Debug(f"[PC]: 无法做空,跳过配对 [{symbol1.Value}, {symbol2.Value}]")
            return []
        
        # 记录交易日志
        action1, action2 = self._get_trade_actions(direction)
        self.algorithm.Debug(f"[PC]: [{symbol1.Value}, {symbol2.Value}] | [{action1}, {action2}] | [{symbol1_weight:.4f}, {symbol2_weight:.4f}] | beta={beta:.3f}")
        
        # 更新配对状态为活跃
        self.pair_ledger.set_pair_status(symbol1, symbol2, True)
        
        return [
            PortfolioTarget.Percent(self.algorithm, symbol1, symbol1_weight),
            PortfolioTarget.Percent(self.algorithm, symbol2, symbol2_weight)
        ]
    
    def _create_flat_targets(self, symbol1: Symbol, symbol2: Symbol) -> List[PortfolioTarget]:
        """创建平仓目标"""
        self.algorithm.Debug(f"[PC]: [{symbol1.Value}, {symbol2.Value}] | [FLAT, FLAT]")
        self._record_cooling_history(symbol1, symbol2)
        self.pair_ledger.set_pair_status(symbol1, symbol2, False)
        return [
            PortfolioTarget.Percent(self.algorithm, symbol1, 0),
            PortfolioTarget.Percent(self.algorithm, symbol2, 0)
        ]
    
    def _calculate_pair_weights(self, direction: InsightDirection, beta: float) -> Optional[Tuple[float, float]]:
        """计算配对权重"""
        # 计算每对可用资金
        available_capital = 1.0 - self.cash_buffer
        capital_per_pair = available_capital / self.max_pairs
        beta_abs = abs(beta)
        m = self.margin_rate
        
        if direction == InsightDirection.Up:
            # y(symbol1)做多, x(symbol2)做空
            y_fund = capital_per_pair * beta_abs / (m + beta_abs)
            x_fund = capital_per_pair * m / (m + beta_abs)
            symbol1_weight = y_fund
            symbol2_weight = -x_fund / m
        elif direction == InsightDirection.Down:
            # y(symbol1)做空, x(symbol2)做多
            y_fund = capital_per_pair * m * beta_abs / (1 + m * beta_abs)
            x_fund = capital_per_pair / (1 + m * beta_abs)
            symbol1_weight = -y_fund / m
            symbol2_weight = x_fund
        else:
            return None
        
        # 验证权重分配
        self._validate_weight_allocation(symbol1_weight, symbol2_weight, capital_per_pair, m)
        
        return (symbol1_weight, symbol2_weight)
    
    def _validate_weight_allocation(self, weight1: float, weight2: float, expected: float, margin_rate: float):
        """验证权重分配是否正确"""
        # 计算实际资金占用
        if weight1 > 0:  # symbol1做多
            total_allocation = abs(weight1) + abs(weight2 * margin_rate)
        else:  # symbol1做空
            total_allocation = abs(weight1 * margin_rate) + abs(weight2)
        
        if abs(total_allocation - expected) > self.WEIGHT_TOLERANCE:
            self.algorithm.Debug(f"[PC] 权重分配异常: 总分配={total_allocation:.4f}, 预期={expected:.4f}")
    
    def _can_execute_trade(self, symbol1: Symbol, symbol2: Symbol, direction: InsightDirection) -> bool:
        """检查是否可以执行交易"""
        if direction == InsightDirection.Up:
            return self.can_short(symbol2)
        elif direction == InsightDirection.Down:
            return self.can_short(symbol1)
        return True
    
    def _get_trade_actions(self, direction: InsightDirection) -> Tuple[str, str]:
        """获取交易动作描述"""
        if direction == InsightDirection.Up:
            return ("BUY", "SELL")
        elif direction == InsightDirection.Down:
            return ("SELL", "BUY")
        return ("FLAT", "FLAT")
    def _get_pair_position_status(self, symbol1: Symbol, symbol2: Symbol) -> Optional[InsightDirection]:
        """
        获取配对的当前持仓状态
        
        Args:
            symbol1, symbol2: 配对的两个股票
            
        Returns:
            InsightDirection: 当前持仓方向, None表示未持仓或异常状态
        """
        portfolio = self.algorithm.Portfolio
        s1_holding = portfolio[symbol1]
        s2_holding = portfolio[symbol2]
        
        # 如果两只股票都没有持仓
        if not s1_holding.Invested and not s2_holding.Invested:
            return None
        
        # 获取持仓数量
        s1_quantity = s1_holding.Quantity
        s2_quantity = s2_holding.Quantity
        
        # 确定持仓方向
        return self._determine_position_direction(s1_quantity, s2_quantity, symbol1, symbol2)
    
    def _determine_position_direction(self, q1: float, q2: float, symbol1: Symbol, symbol2: Symbol) -> Optional[InsightDirection]:
        """根据持仓数量确定配对方向"""
        # 检查异常状态：同向持仓
        if q1 != 0 and q2 != 0 and (q1 > 0) == (q2 > 0):
            self.algorithm.Debug(f"[PC] 警告：检测到异常同向持仓 - {symbol1.Value}: {q1}, {symbol2.Value}: {q2}")
            return None
        
        # 正常配对方向判断
        if q1 > 0 and q2 <= 0:
            return InsightDirection.Up
        elif q1 < 0 and q2 >= 0:
            return InsightDirection.Down
        
        return None


    def _validate_signal(self, current_position: Optional[InsightDirection], signal_direction: InsightDirection, 
                        symbol1: Symbol, symbol2: Symbol) -> Optional[InsightDirection]:
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
        
        # 未持仓:检查是否可以建仓
        if current_position is None:
            return self._validate_new_position(signal_direction, symbol1, symbol2)
        
        # 已持仓:检查信号方向
        if current_position == signal_direction:
            return None  # 同方向,忽略重复信号
        else:
            return InsightDirection.Flat  # 反方向,转为平仓
    
    def _validate_new_position(self, signal_direction: InsightDirection, symbol1: Symbol, symbol2: Symbol) -> Optional[InsightDirection]:
        """验证是否可以建立新仓位"""
        # 检查冷却期
        if self._is_in_cooling_period(symbol1, symbol2):
            return None
        
        # 检查最大配对数
        if not self._can_open_new_position():
            return None
        
        return signal_direction
    
    def _can_open_new_position(self) -> bool:
        """检查是否可以开新仓位"""
        return self.pair_ledger.get_active_pairs_count() < self.max_pairs


    def _is_in_cooling_period(self, symbol1: Symbol, symbol2: Symbol) -> bool:
        """
        检查配对是否在冷却期内
        
        Args:
            symbol1, symbol2: 配对股票
            
        Returns:
            bool: True表示在冷却期内,不能建仓
        """
        last_flat_time = self._get_last_flat_time(symbol1, symbol2)
        if last_flat_time is None:
            return False
        
        time_diff = self.algorithm.Time - last_flat_time
        return time_diff.days < self.pair_reentry_cooldown_days
    
    def _get_last_flat_time(self, symbol1: Symbol, symbol2: Symbol) -> Optional[datetime]:
        """获取配对最后的平仓时间"""
        # 检查正向和反向键
        for pair_key in [(symbol1, symbol2), (symbol2, symbol1)]:
            if pair_key in self.pair_cooling_history:
                return self.pair_cooling_history[pair_key]
        return None
    
    def _record_cooling_history(self, symbol1: Symbol, symbol2: Symbol):
        """
        记录配对的平仓时间,用于冷却期计算
        
        Args:
            symbol1, symbol2: 配对股票
        """
        # 记录双向配对以防止反向建仓绕过冷却期
        current_time = self.algorithm.Time
        self.pair_cooling_history[(symbol1, symbol2)] = current_time
        self.pair_cooling_history[(symbol2, symbol1)] = current_time
    
    def can_short(self, symbol: Symbol) -> bool:
        """
        检查是否可以做空(回测环境所有股票都可以做空,实盘时需要检测)
        """
        if self.algorithm.LiveMode:
            security = self.algorithm.Securities[symbol]
            shortable = security.ShortableProvider.ShortableQuantity(symbol, self.algorithm.Time)
            return shortable is not None and shortable > 0
        return True  # 回测环境默认可做空