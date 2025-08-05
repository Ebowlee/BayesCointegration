# region imports
from AlgorithmImports import *
from typing import List, Dict, Tuple, Optional
from src.OrderTracker import OrderTracker
# endregion

class BayesianCointegrationRiskManagementModel(RiskManagementModel):
    """
    贝叶斯协整风险管理模型 - 监控和控制策略风险
    
    该模块是策略的风险控制层，独立于信号生成和仓位构建，
    每日主动监控所有持仓并执行必要的风控措施。
    
    核心功能:
    1. 持仓时间管理: 超过60天强制平仓
    2. 配对回撤监控: 配对整体亏损超过10%止损
    3. 单边回撤监控: 单只股票亏损超过20%止损
    4. 实时风险评估: 每日检查所有持仓
    
    工作机制:
    - 框架保证每日调用ManageRisk方法
    - 即使没有新交易信号也会执行风控检查
    - 可以独立生成平仓指令
    - 使用OnOrderEvent记录的真实建仓时间
    
    风控阈值:
    - max_holding_days: 60天 (硬性限制)
    - max_pair_drawdown: 10% (配对整体)
    - max_single_drawdown: 20% (单边资产)
    
    与其他模块的关系:
    - 输入: PortfolioConstruction的targets
    - 输出: 风险调整后的targets
    - 查询: 直接从Portfolio获取持仓信息
    - 更新: 通过返回的targets触发平仓
    
    设计理念:
    - 独立性: 不依赖其他模块的信号
    - 主动性: 主动扫描和监控风险
    - 保守性: 宁可错杀不可放过
    - 透明性: 详细记录所有风控动作
    """
    
    def __init__(self, algorithm, config: dict, order_tracker: OrderTracker, pair_registry):
        """
        初始化风险管理模型
        
        Args:
            algorithm: QuantConnect算法实例
            config: 风控配置参数
            order_tracker: 订单追踪器实例
            pair_registry: 配对注册表实例
        """
        super().__init__()
        self.algorithm = algorithm
        self.order_tracker = order_tracker
        self.pair_registry = pair_registry
        
        # 风控参数
        self.max_holding_days = config.get('max_holding_days', 30)
        self.cooldown_days = config.get('cooldown_days', 7)
        self.max_pair_drawdown = config.get('max_pair_drawdown', 0.10)
        self.max_single_drawdown = config.get('max_single_drawdown', 0.20)
        
        # 调用验证
        self.last_check_date = None
        self.daily_check_count = 0
        
        # 风控统计
        self.risk_triggers = {
            'holding_timeout': 0,
            'pair_stop_loss': 0,
            'single_stop_loss': 0
        }
        
        self.algorithm.Debug(
            f"[RiskManagement] 初始化完成 - "
            f"最大持仓{self.max_holding_days}天, "
            f"配对止损{self.max_pair_drawdown*100}%, "
            f"单边止损{self.max_single_drawdown*100}%"
        )
    
    def ManageRisk(self, algorithm, targets: List[PortfolioTarget]) -> List[PortfolioTarget]:
        """
        主风控方法 - 框架每日调用
        
        执行流程:
        1. 验证每日调用
        2. 扫描所有活跃持仓
        3. 检查各项风控指标
        4. 生成必要的平仓指令
        5. 合并原始targets和风控targets
        
        Args:
            algorithm: 算法实例
            targets: PC模块生成的原始targets
            
        Returns:
            List[PortfolioTarget]: 风险调整后的targets
        """
        # 验证每日调用
        current_date = algorithm.Time.date()
        if self.last_check_date != current_date:
            self.daily_check_count += 1
            self.algorithm.Debug(
                f"[RiskManagement] 第{self.daily_check_count}次日常风控检查: {current_date}, "
                f"收到{len(targets)}个targets"
            )
            self.last_check_date = current_date
        
        # 复制原始targets
        risk_adjusted_targets = list(targets) if targets else []
        
        # 获取所有活跃持仓配对
        active_pairs = self._get_active_pairs()
        
        # 检查每个配对的风控条件
        liquidation_pairs = []
        
        if active_pairs:
            for pair_info in active_pairs:
                symbol1, symbol2 = pair_info['pair']
                
                # 首先检查配对完整性
                integrity_status = self._check_pair_integrity(symbol1, symbol2)
                if integrity_status in ["same_direction_error", "single_side_only"]:
                    self.algorithm.Debug(
                        f"[RiskManagement] 配对异常 [{symbol1.Value},{symbol2.Value}]: {integrity_status}"
                    )
                    liquidation_pairs.append((symbol1, symbol2, f"配对异常:{integrity_status}"))
                    continue
                
                # 检查持仓时间
                holding_days = pair_info['holding_days']
                if holding_days > self.max_holding_days:
                    self.algorithm.Debug(
                        f"[RiskManagement] 持仓超时 [{symbol1.Value},{symbol2.Value}]: "
                        f"{holding_days}天 > {self.max_holding_days}天"
                    )
                    liquidation_pairs.append((symbol1, symbol2, "持仓超时"))
                    self.risk_triggers['holding_timeout'] += 1
                    continue
                
                # 计算回撤
                pair_drawdown = self._calculate_pair_drawdown(symbol1, symbol2)
                single_drawdowns = self._calculate_single_drawdowns(symbol1, symbol2)
                
                # 检查配对整体回撤
                if pair_drawdown < -self.max_pair_drawdown:
                    self.algorithm.Debug(
                        f"[RiskManagement] 配对止损 [{symbol1.Value},{symbol2.Value}]: "
                        f"回撤{pair_drawdown*100:.1f}% < -{self.max_pair_drawdown*100}%"
                    )
                    liquidation_pairs.append((symbol1, symbol2, "配对止损"))
                    self.risk_triggers['pair_stop_loss'] += 1
                    continue
                
                # 检查单边回撤
                for symbol, drawdown in single_drawdowns.items():
                    if drawdown < -self.max_single_drawdown:
                        self.algorithm.Debug(
                            f"[RiskManagement] 单边止损 [{symbol1.Value},{symbol2.Value}]: "
                            f"{symbol.Value}回撤{drawdown*100:.1f}% < -{self.max_single_drawdown*100}%"
                        )
                        liquidation_pairs.append((symbol1, symbol2, f"{symbol.Value}单边止损"))
                        self.risk_triggers['single_stop_loss'] += 1
                        break
                
                # 记录正常持仓状态（每10天记录一次）
                if holding_days % 10 == 0 and holding_days > 0:
                    self.algorithm.Debug(
                        f"[RiskManagement] 配对状态 [{symbol1.Value},{symbol2.Value}]: "
                        f"持仓{holding_days}天, 配对回撤{pair_drawdown*100:.1f}%, "
                        f"单边回撤[{single_drawdowns[symbol1]*100:.1f}%, {single_drawdowns[symbol2]*100:.1f}%]"
                    )
        
        # 生成平仓指令
        if liquidation_pairs:
            liquidation_targets = self._create_liquidation_targets(liquidation_pairs)
            
            # 记录原始targets和风控targets
            original_symbols = {t.Symbol for t in risk_adjusted_targets}
            liquidation_symbols = {t.Symbol for t in liquidation_targets}
            
            # 检查是否有冲突
            conflicts = original_symbols.intersection(liquidation_symbols)
            if conflicts:
                self.algorithm.Debug(
                    f"[RiskManagement] 警告: 风控指令与现有指令冲突，"
                    f"冲突股票: {[s.Value for s in conflicts]}"
                )
                # 移除冲突的原始指令
                risk_adjusted_targets = [t for t in risk_adjusted_targets 
                                       if t.Symbol not in liquidation_symbols]
            
            risk_adjusted_targets.extend(liquidation_targets)
            
            self.algorithm.Debug(
                f"[RiskManagement] 风控平仓{len(liquidation_pairs)}对, "
                f"生成{len(liquidation_targets)}个平仓指令, "
                f"最终指令数: {len(risk_adjusted_targets)}"
            )
        
        # 检查异常订单
        self._check_abnormal_orders()
        
        # 过滤冷却期内的新建仓信号
        risk_adjusted_targets = self._filter_cooldown_targets(risk_adjusted_targets)
        
        return risk_adjusted_targets
    
    def _get_active_pairs(self) -> List[Dict]:
        """
        获取所有活跃持仓配对
        
        使用Portfolio识别当前持仓，并通过OrderTracker获取时间信息
        
        Returns:
            List[Dict]: 活跃配对信息列表，每个元素包含:
                - 'pair': (symbol1, symbol2)
                - 'holding_days': 持仓天数
        """
        active_pairs = []
        processed_symbols = set()
        
        # 遍历所有持仓
        for symbol, holding in self.algorithm.Portfolio.items():
            if not holding.Invested or symbol in processed_symbols:
                continue
            
            # 查找配对的另一只股票
            paired_symbol = self._find_paired_symbol(symbol)
            if not paired_symbol:
                continue
            
            # 标记已处理
            processed_symbols.add(symbol)
            processed_symbols.add(paired_symbol)
            
            # 从PairRegistry获取原始配对顺序
            original_pair = self.pair_registry.get_pair_for_symbol(symbol)
            if not original_pair:
                # 如果找不到，使用当前顺序（兼容性）
                original_pair = (symbol, paired_symbol)
            
            # 获取持仓时间
            holding_days = self.order_tracker.get_holding_period(original_pair[0], original_pair[1])
            if holding_days is None:
                # 如果OrderTracker没有记录，估算为0天（刚建仓）
                holding_days = 0
            
            active_pairs.append({
                'pair': original_pair,  # 使用原始配对顺序
                'holding_days': holding_days
            })
        
        return active_pairs
    
    def _find_paired_symbol(self, symbol: Symbol) -> Optional[Symbol]:
        """
        查找与给定股票配对的另一只股票
        
        使用 PairRegistry 获取正确的配对关系
        
        Args:
            symbol: 需要查找配对的股票
            
        Returns:
            Optional[Symbol]: 配对的股票，如果没有找到返回None
        """
        # 使用 PairRegistry 获取配对信息
        paired_symbol = self.pair_registry.get_paired_symbol(symbol)
        
        # 验证配对的股票确实有持仓
        if paired_symbol and self.algorithm.Portfolio[paired_symbol].Invested:
            return paired_symbol
        
        return None
    
    def _calculate_pair_drawdown(self, symbol1: Symbol, symbol2: Symbol) -> float:
        """
        计算配对整体回撤率
        
        配对回撤 = (当前配对价值 - 配对成本) / 配对成本
        
        Args:
            symbol1, symbol2: 配对的两只股票
            
        Returns:
            float: 回撤率（负值表示亏损）
        """
        holdings1 = self.algorithm.Portfolio[symbol1]
        holdings2 = self.algorithm.Portfolio[symbol2]
        
        # 计算配对总成本（绝对值）
        total_cost = abs(holdings1.HoldingsCost) + abs(holdings2.HoldingsCost)
        if total_cost == 0:
            return 0
        
        # 计算配对总盈亏
        total_pnl = holdings1.UnrealizedProfit + holdings2.UnrealizedProfit
        
        # 计算回撤率
        return total_pnl / total_cost
    
    def _calculate_single_drawdowns(self, symbol1: Symbol, symbol2: Symbol) -> Dict[Symbol, float]:
        """
        计算单边资产回撤率
        
        做多回撤 = (现价 - 成本价) / 成本价
        做空回撤 = (成本价 - 现价) / 成本价
        
        Args:
            symbol1, symbol2: 配对的两只股票
            
        Returns:
            Dict[Symbol, float]: 各股票的回撤率
        """
        drawdowns = {}
        
        for symbol in [symbol1, symbol2]:
            holding = self.algorithm.Portfolio[symbol]
            if holding.Quantity == 0:
                drawdowns[symbol] = 0
                continue
            
            # 获取平均成本和当前价格
            avg_price = holding.AveragePrice
            current_price = holding.Price
            
            if avg_price == 0:
                drawdowns[symbol] = 0
                continue
            
            # 根据持仓方向计算回撤
            if holding.Quantity > 0:  # 做多
                drawdown = (current_price - avg_price) / avg_price
            else:  # 做空
                drawdown = (avg_price - current_price) / avg_price
            
            drawdowns[symbol] = drawdown
        
        return drawdowns
    
    def _create_liquidation_targets(self, liquidation_pairs: List[Tuple]) -> List[PortfolioTarget]:
        """
        创建平仓指令
        
        Args:
            liquidation_pairs: 需要平仓的配对列表，格式为[(symbol1, symbol2, reason), ...]
            
        Returns:
            List[PortfolioTarget]: 平仓指令列表
        """
        targets = []
        processed_symbols = set()  # 避免重复处理
        
        for symbol1, symbol2, reason in liquidation_pairs:
            # 避免重复添加同一股票的平仓指令
            if symbol1 not in processed_symbols:
                targets.append(PortfolioTarget.Percent(self.algorithm, symbol1, 0))
                processed_symbols.add(symbol1)
            
            if symbol2 not in processed_symbols:
                targets.append(PortfolioTarget.Percent(self.algorithm, symbol2, 0))
                processed_symbols.add(symbol2)
            
            # 记录平仓原因
            self.algorithm.Debug(
                f"[RiskManagement] 生成平仓指令 [{symbol1.Value},{symbol2.Value}]: {reason}"
            )
        
        return targets
    
    def _check_pair_integrity(self, symbol1: Symbol, symbol2: Symbol) -> str:
        """
        检查配对的完整性
        
        Args:
            symbol1, symbol2: 配对的两只股票
            
        Returns:
            str: 状态码
                - "no_position": 都没持仓
                - "normal": 正常配对持仓
                - "same_direction_error": 同向持仓错误
                - "single_side_only": 单边持仓
                - "ratio_mismatch": 比例严重偏离
        """
        h1 = self.algorithm.Portfolio[symbol1]
        h2 = self.algorithm.Portfolio[symbol2]
        
        # 情况1：都没持仓
        if not h1.Invested and not h2.Invested:
            return "no_position"
        
        # 情况2：都有持仓
        if h1.Invested and h2.Invested:
            # 检查方向是否相反
            if (h1.Quantity > 0) == (h2.Quantity > 0):
                return "same_direction_error"
            
            # 暂时不检查比例，因为我们没有beta信息
            # 未来可以从其他地方获取beta进行检查
            
            return "normal"
        
        # 情况3：单边持仓 - 异常
        return "single_side_only"
    
    def get_statistics(self) -> Dict:
        """
        获取风控统计信息
        
        Returns:
            Dict: 各类风控触发次数统计
        """
        return self.risk_triggers.copy()
    
    def _check_abnormal_orders(self):
        """
        检查并处理异常订单
        
        使用OrderTracker检测异常配对，记录但不主动处理
        （处理逻辑可根据需要扩展）
        """
        abnormal_pairs = self.order_tracker.get_abnormal_pairs()
        
        if abnormal_pairs:
            self.algorithm.Debug(
                f"[RiskManagement] 检测到{len(abnormal_pairs)}个异常配对"
            )
            
            for symbol1, symbol2 in abnormal_pairs:
                self.algorithm.Debug(
                    f"[RiskManagement] 异常配对: [{symbol1.Value},{symbol2.Value}]"
                )
    
    def _filter_cooldown_targets(self, targets: List[PortfolioTarget]) -> List[PortfolioTarget]:
        """
        过滤冷却期内的新建仓信号
        
        检查每个target，如果是建仓信号且配对在冷却期内，则过滤掉
        
        Args:
            targets: 原始目标列表
            
        Returns:
            List[PortfolioTarget]: 过滤后的目标列表
        """
        if not targets:
            return targets
        
        filtered_targets = []
        processed_symbols = set()
        
        for target in targets:
            symbol = target.Symbol
            
            # 如果是平仓信号（权重为0），直接保留
            if target.Quantity == 0:
                filtered_targets.append(target)
                continue
            
            # 如果已经有持仓，保留（可能是调仓，虽然策略不应该有）
            if self.algorithm.Portfolio[symbol].Invested:
                filtered_targets.append(target)
                continue
            
            # 对于新建仓信号，检查是否在冷却期
            if symbol in processed_symbols:
                # 这个股票已经被处理过（作为配对的一部分被过滤）
                continue
                
            # 使用 PairRegistry 查找配对的另一只股票
            paired_symbol = self.pair_registry.get_paired_symbol(symbol)
            
            if paired_symbol:
                # 检查冷却期
                if self.order_tracker.is_in_cooldown(symbol, paired_symbol, self.cooldown_days):
                    self.algorithm.Debug(
                        f"[RiskManagement] 配对在冷却期内，过滤建仓信号: "
                        f"[{symbol.Value},{paired_symbol.Value}]"
                    )
                    processed_symbols.add(symbol)
                    processed_symbols.add(paired_symbol)
                    continue
            
            filtered_targets.append(target)
        
        return filtered_targets