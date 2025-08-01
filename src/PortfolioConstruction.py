# region imports
from AlgorithmImports import *
from collections import defaultdict
from typing import Tuple, Optional, List
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
    
    # 常量定义
    TAG_DELIMITER = '|'
    WEIGHT_TOLERANCE = 0.01
    
    def __init__(self, algorithm, config, pair_ledger):
        super().__init__() 
        self.algorithm = algorithm
        self.pair_ledger = pair_ledger
        self.margin_rate = config.get('margin_rate', 0.5)
        self.pair_reentry_cooldown_days = config.get('pair_reentry_cooldown_days', 7)
        self.max_pairs = config.get('max_pairs', 8)
        self.cash_buffer = config.get('cash_buffer', 0.05)
        # 冷却期管理已移至PairLedger
        
        # 预计算常用值
        self.available_capital = 1.0 - self.cash_buffer
        self.capital_per_pair = self.available_capital / self.max_pairs
        
        self.algorithm.Debug(f"[PortfolioConstruction] 初始化完成 (保证金率: {self.margin_rate}, "
                           f"冷却期: {self.pair_reentry_cooldown_days}天, "
                           f"最大配对数: {self.max_pairs}, 现金缓冲: {self.cash_buffer*100}%)")
                           
    def create_targets(self, algorithm, insights) -> List[PortfolioTarget]:
        """创建投资组合目标"""
        targets = []
        
        # 按 GroupId 分组
        grouped_insights = defaultdict(list)
        for insight in insights:
            if insight.GroupId is not None:
                grouped_insights[insight.GroupId].append(insight)
        
        # 处理每组 Insight
        for group_id, group in grouped_insights.items():
            targets.extend(self._process_signal_pair(group))
        
        return targets

    def _process_signal_pair(self, group: List[Insight]) -> List[PortfolioTarget]:
        """处理一对信号,返回有效的PortfolioTarget列表"""
        if len(group) != 2:
            return []
            
        insight1, insight2 = group
        symbol1, symbol2 = insight1.Symbol, insight2.Symbol
        
        # 解析beta参数
        beta_mean = self._parse_beta_from_tag(insight1.Tag)
        if beta_mean is None:
            return []

        # 获取当前持仓状态并验证信号
        current_position = self._get_pair_position_status(symbol1, symbol2)
        validated_direction = self._validate_signal(current_position, insight1.Direction, symbol1, symbol2)

        if validated_direction is None:
            return []

        # 生成PortfolioTarget
        return self._build_pair_targets(symbol1, symbol2, validated_direction, beta_mean)
    
    def _parse_beta_from_tag(self, tag: str) -> Optional[float]:
        """从Tag中解析beta参数"""
        try:
            tag_parts = tag.split(self.TAG_DELIMITER)
            return float(tag_parts[2])
        except (IndexError, ValueError) as e:
            self.algorithm.Debug(f"[PC] Tag解析失败 - Tag: {tag}, 预期格式: 'symbol1&symbol2|alpha|beta|zscore', 错误: {e}")
            return None
    
    def _build_pair_targets(self, symbol1: Symbol, symbol2: Symbol, 
                           direction: InsightDirection, beta: float) -> List[PortfolioTarget]:
        """
        构建配对的目标持仓
        协整关系: log(symbol1) = alpha + beta × log(symbol2)
        """
        # 平仓信号处理
        if direction == InsightDirection.Flat:
            return self._handle_flat_signal(symbol1, symbol2)
        
        # 检查做空能力
        if direction == InsightDirection.Up and not self.can_short(symbol2):
            self._log_cannot_short(symbol1, symbol2)
            return []
        elif direction == InsightDirection.Down and not self.can_short(symbol1):
            self._log_cannot_short(symbol1, symbol2)
            return []
        
        # 计算权重
        weights = self._calculate_pair_weights(direction, beta)
        if weights is None:
            return []
        
        symbol1_weight, symbol2_weight = weights
        
        # 记录日志（不再更新状态，由CustomRiskManager在订单成交后更新）
        action = "BUY, SELL" if direction == InsightDirection.Up else "SELL, BUY"
        self.algorithm.Debug(f"[PC]: [{symbol1.Value}, {symbol2.Value}] | [{action}] | "
                           f"[{symbol1_weight:.4f}, {symbol2_weight:.4f}] | beta={beta:.3f}")
        
        return [
            PortfolioTarget.Percent(self.algorithm, symbol1, symbol1_weight),
            PortfolioTarget.Percent(self.algorithm, symbol2, symbol2_weight)
        ]
    
    def _handle_flat_signal(self, symbol1: Symbol, symbol2: Symbol) -> List[PortfolioTarget]:
        """处理平仓信号"""
        self.algorithm.Debug(f"[PC]: [{symbol1.Value}, {symbol2.Value}] | [FLAT, FLAT]")
        # 不再需要记录冷却期，因为PairLedger会在订单成交后自动记录last_exit_time
        return [
            PortfolioTarget.Percent(self.algorithm, symbol1, 0),
            PortfolioTarget.Percent(self.algorithm, symbol2, 0)
        ]
    
    def _calculate_pair_weights(self, direction: InsightDirection, beta: float) -> Optional[Tuple[float, float]]:
        """
        计算配对权重，统一处理Up和Down方向
        返回: (symbol1_weight, symbol2_weight) 或 None
        """
        beta_abs = abs(beta)
        m = self.margin_rate
        c = self.capital_per_pair
        
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
        """获取配对的当前持仓状态"""
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
        """验证信号有效性"""
        # 获取配对信息
        pair_info = self.pair_ledger.get_pair_info(symbol1, symbol2)
        
        # 检查风控状态
        if pair_info and pair_info.risk_triggered:
            self.algorithm.Debug(f"[PC] 跳过配对{symbol1.Value}-{symbol2.Value}，已触发{pair_info.risk_type}风控")
            return None
        
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
        """检查是否可以开新仓"""
        # 使用PairLedger的统一检查方法
        if not self.pair_ledger.is_tradeable(symbol1, symbol2, self.pair_reentry_cooldown_days):
            return False
        
        # 检查最大配对数
        if self.pair_ledger.get_active_pairs_count() >= self.max_pairs:
            self.algorithm.Debug(f"[PC] 已达最大配对数限制 {self.max_pairs}")
            return False
        
        return True
    
    # 冷却期检查已移至PairLedger.is_tradeable方法中
    
    def _log_cannot_short(self, symbol1: Symbol, symbol2: Symbol):
        """记录无法做空的日志"""
        self.algorithm.Debug(f"[PC]: 无法做空,跳过配对 [{symbol1.Value}, {symbol2.Value}]")
    
    def can_short(self, symbol: Symbol) -> bool:
        """检查是否可以做空"""
        if self.algorithm.LiveMode:
            security = self.algorithm.Securities[symbol]
            shortable = security.ShortableProvider.ShortableQuantity(symbol, self.algorithm.Time)
            return shortable is not None and shortable > 0
        return True  # 回测环境默认可做空