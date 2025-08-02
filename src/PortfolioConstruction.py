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
        self.cash_buffer = config.get('cash_buffer', 0.05)
        
        # 动态资金管理参数
        self.max_position_per_pair = config.get('max_position_per_pair', 0.10)  # 单对最大仓位10%
        self.min_position_per_pair = config.get('min_position_per_pair', 0.05)  # 单对最小仓位5%
        
        # 弃用固定分配，改用动态分配
        # self.max_pairs = config.get('max_pairs', 8)  # 不再需要
        # self.capital_per_pair = self.available_capital / self.max_pairs  # 不再固定
        
        self.algorithm.Debug(f"[PortfolioConstruction] 初始化完成 (保证金率: {self.margin_rate}, "
                           f"冷却期: {self.pair_reentry_cooldown_days}天, "
                           f"单对仓位: {self.min_position_per_pair*100}%-{self.max_position_per_pair*100}%, "
                           f"现金缓冲: {self.cash_buffer*100}%)")
                           
    def create_targets(self, algorithm, insights) -> List[PortfolioTarget]:
        """创建投资组合目标"""
        # 按 GroupId 分组
        grouped_insights = defaultdict(list)
        for insight in insights:
            if insight.GroupId is not None:
                grouped_insights[insight.GroupId].append(insight)
        
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
        """从Tag中解析参数
        
        Tag格式: 'symbol1&symbol2|alpha|beta|zscore|num_pairs'
        
        Returns:
            Dict: 包含beta和quality_score的字典
        """
        try:
            tag_parts = tag.split(self.TAG_DELIMITER)
            if len(tag_parts) < 4:
                self.algorithm.Debug(f"[PC] Tag格式错误: {tag}")
                return None
            
            # 解析参数
            beta = float(tag_parts[2])
            zscore = float(tag_parts[3])
            
            # 计算质量分数（基于z-score的绝对值）
            # z-score越大，信号越强
            quality_score = min(abs(zscore) / 3.0, 1.0)  # 归一化到[0,1]
            
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
        动态分配资金并创建投资组合目标
        
        Args:
            new_position_signals: 新建仓信号列表，每个包含symbol1, symbol2, direction, beta, quality_score
            
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
        
        self.algorithm.Debug(f"[PC] 分配资金给{allocated_count}对新配对，剩余可用资金: {available_capital:.1%}")
        return targets
    
    def _handle_flat_signal(self, symbol1: Symbol, symbol2: Symbol) -> List[PortfolioTarget]:
        """处理平仓信号"""
        self.algorithm.Debug(f"[PC]: [{symbol1.Value}, {symbol2.Value}] | [FLAT, FLAT]")
        # 不再需要记录冷却期，因为PairLedger会在订单成交后自动记录last_exit_time
        return [
            PortfolioTarget.Percent(self.algorithm, symbol1, 0),
            PortfolioTarget.Percent(self.algorithm, symbol2, 0)
        ]
    
    def _calculate_pair_weights(self, direction: InsightDirection, beta: float, allocation: float) -> Optional[Tuple[float, float]]:
        """
        计算配对权重，统一处理Up和Down方向
        
        Args:
            direction: 信号方向
            beta: beta系数
            allocation: 分配给该配对的资金比例
            
        Returns:
            (symbol1_weight, symbol2_weight) 或 None
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
        
        # 不再检查最大配对数，改用动态资金管理
        
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