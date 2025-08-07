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
    1. 持仓时间管理: 超过30天强制平仓
    2. 配对回撤监控: 配对整体亏损超过10%止损
    3. 单边回撤监控: 单只股票亏损超过15%止损
    4. 行业集中度监控: 单个行业暴露超过30%时平仓最早的配对
    5. 实时风险评估: 每日检查所有持仓
    
    工作机制:
    - 框架保证每日调用ManageRisk方法
    - 即使没有新交易信号也会执行风控检查
    - 可以独立生成平仓指令
    - 使用OnOrderEvent记录的真实建仓时间
    
    风控阈值:
    - max_holding_days: 30天 (硬性限制)
    - max_pair_drawdown: 10% (配对整体)
    - max_single_drawdown: 15% (单边资产)
    - sector_exposure_threshold: 30% (行业集中度)
    
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
    
    def __init__(self, algorithm, config: dict, order_tracker: OrderTracker, pair_registry, 
                 sector_code_to_name: dict = None, central_pair_manager=None):
        """
        初始化风险管理模型
        
        Args:
            algorithm: QuantConnect算法实例
            config: 风控配置参数
            order_tracker: 订单追踪器实例
            pair_registry: 配对注册表实例（DEPRECATED, 将在v4.0移除）
            sector_code_to_name: 行业代码到名称的映射字典
            central_pair_manager: 中央配对管理器（新增，用于获取配对状态）
        """
        super().__init__()
        self.algorithm = algorithm
        self.order_tracker = order_tracker
        self.pair_registry = pair_registry
        self.sector_code_to_name = sector_code_to_name or {}
        self.central_pair_manager = central_pair_manager
        
        # 风控参数
        # 风控阈值边界条件说明:
        # - max_holding_days: 30天 (使用>比较，第31天触发超时平仓)
        # - cooldown_days: 7天 (使用<比较，第7天仍在冷却期，第8天才能重新建仓)
        # - max_pair_drawdown: 10% (使用<比较，回撤需要超过-10%才触发，如-10.1%)
        # - max_single_drawdown: 15% (使用<比较，回撤需要超过-15%才触发，如-15.1%)
        # - sector_exposure_threshold: 30% (使用>比较，超过30%触发平仓)
        self.max_holding_days = config.get('max_holding_days', 30)
        self.cooldown_days = config.get('cooldown_days', 7)
        self.max_pair_drawdown = config.get('max_pair_drawdown', 0.10)
        self.max_single_drawdown = config.get('max_single_drawdown', 0.15)
        self.sector_exposure_threshold = config.get('sector_exposure_threshold', 0.30)
        
        # 验证风控参数
        assert 0 < self.max_holding_days <= 365, f"持仓天数必须在1-365之间，当前值: {self.max_holding_days}"
        assert 0 < self.cooldown_days <= 30, f"冷却期必须在1-30天之间，当前值: {self.cooldown_days}"
        assert 0 < self.max_pair_drawdown <= 1, f"配对回撤阈值必须在0-100%之间，当前值: {self.max_pair_drawdown}"
        assert 0 < self.max_single_drawdown <= 1, f"单边回撤阈值必须在0-100%之间，当前值: {self.max_single_drawdown}"
        
        # 调用验证
        self.last_check_date = None
        self.daily_check_count = 0
        
        # 风控统计
        self.risk_triggers = {
            'holding_timeout': 0,
            'pair_stop_loss': 0,
            'single_stop_loss': 0,
            'sector_concentration': 0
        }
        
        self.algorithm.Debug(
            f"[RiskManagement] 初始化完成 - "
            f"最大持仓{self.max_holding_days}天, "
            f"配对止损{self.max_pair_drawdown*100}%, "
            f"单边止损{self.max_single_drawdown*100}%, "
            f"行业集中度阈值{self.sector_exposure_threshold*100}%"
        )
    
    def ManageRisk(self, algorithm, targets: List[PortfolioTarget]) -> List[PortfolioTarget]:
        """
        主风控方法 - 框架每日调用
        
        v3.5.0重构：清晰分离T+0和T+1风控逻辑
        
        执行流程:
        1. 验证每日调用
        2. T+0检查（基于Portfolio实时状态，自下而上）
        3. T+1检查（基于历史信息）
        4. 合并去重所有平仓指令
        5. 生成风险调整后的targets
        6. 过滤冷却期内的新建仓（T+1逻辑）
        
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
            targets_count = len(targets) if targets is not None else 0
            self.algorithm.Debug(
                f"[RiskManagement] 第{self.daily_check_count}次日常风控检查: {current_date}, "
                f"收到{targets_count}个targets"
            )
            self.last_check_date = current_date
        
        # 复制原始targets
        risk_adjusted_targets = list(targets) if targets else []
        
        # ============ T+0风控检查（自下而上） ============
        t0_liquidations = self._perform_t0_checks()
        
        # ============ T+1风控检查（历史信息） ============
        t1_liquidations = self._perform_t1_checks()
        
        # ============ 合并所有平仓指令 ============
        all_liquidations = []
        processed_pairs = set()
        
        # 合并T+0和T+1的平仓列表，去重
        # 优先保留T+0的结果（更紧急的风控）
        for symbol1, symbol2, reason in t0_liquidations:
            pair_key = (symbol1, symbol2) if symbol1.Value < symbol2.Value else (symbol2, symbol1)
            if pair_key not in processed_pairs:
                all_liquidations.append((symbol1, symbol2, reason))
                processed_pairs.add(pair_key)
        
        # 然后添加T+1的结果（如果没有被T+0处理过）
        for symbol1, symbol2, reason in t1_liquidations:
            pair_key = (symbol1, symbol2) if symbol1.Value < symbol2.Value else (symbol2, symbol1)
            if pair_key not in processed_pairs:
                all_liquidations.append((symbol1, symbol2, reason))
                processed_pairs.add(pair_key)
        
        # ============ 生成平仓指令 ============
        if all_liquidations:
            liquidation_targets = self._create_liquidation_targets(all_liquidations)
            
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
                f"[RiskManagement] 风控汇总: T+0平仓{len(t0_liquidations)}对, "
                f"T+1平仓{len(t1_liquidations)}对, "
                f"合计生成{len(liquidation_targets)}个平仓指令"
            )
        
        # ============ T+1: 过滤冷却期内的新建仓信号 ============
        risk_adjusted_targets = self._filter_cooldown_targets(risk_adjusted_targets)
        
        # 记录正常持仓状态（每10天记录一次）
        active_pairs = self._get_active_pairs()
        for pair_info in active_pairs:
            holding_days = pair_info['holding_days']
            if holding_days % 10 == 0 and holding_days > 0:
                symbol1, symbol2 = pair_info['pair']
                pair_drawdown = self._calculate_pair_drawdown(symbol1, symbol2)
                single_drawdowns = self._calculate_single_drawdowns(symbol1, symbol2)
                self.algorithm.Debug(
                    f"[RiskManagement] 配对状态 [{symbol1.Value},{symbol2.Value}]: "
                    f"持仓{holding_days}天, 配对回撤{pair_drawdown*100:.1f}%, "
                    f"单边回撤[{single_drawdowns[symbol1]*100:.1f}%, {single_drawdowns[symbol2]*100:.1f}%]"
                )
        
        return risk_adjusted_targets
    
    def _perform_t0_checks(self) -> List[Tuple]:
        """
        T+0风控检查：基于Portfolio实时状态，自下而上检查
        
        检查顺序（重要）：
        1. Level 1: 个股级别 - 单边最大回撤
        2. Level 2: 配对级别 - 配对整体回撤
        3. Level 3: 组合级别 - Portfolio总回撤（预留）
        4. Level 4: 行业级别 - 行业集中度
        
        Returns:
            List[Tuple]: 需要平仓的配对列表，格式为[(symbol1, symbol2, reason), ...]
        """
        liquidation_pairs = []
        active_pairs = self._get_active_pairs()
        
        if not active_pairs:
            return liquidation_pairs
        
        # 遍历每个活跃配对
        processed_for_t0 = set()  # 避免重复计数
        
        for pair_info in active_pairs:
            symbol1, symbol2 = pair_info['pair']
            pair_key = (symbol1, symbol2) if symbol1.Value < symbol2.Value else (symbol2, symbol1)
            
            # Level 1: 个股级别 - 单边回撤检查（15%）
            single_drawdowns = self._calculate_single_drawdowns(symbol1, symbol2)
            single_stop_triggered = False
            
            for symbol, drawdown in single_drawdowns.items():
                if drawdown < -self.max_single_drawdown:
                    self.algorithm.Debug(
                        f"[T+0-L1] 个股止损 [{symbol1.Value},{symbol2.Value}]: "
                        f"{symbol.Value}回撤{drawdown*100:.1f}% < -{self.max_single_drawdown*100}%"
                    )
                    if pair_key not in processed_for_t0:
                        liquidation_pairs.append((symbol1, symbol2, f"{symbol.Value}单边止损"))
                        self.risk_triggers['single_stop_loss'] += 1
                        processed_for_t0.add(pair_key)
                        single_stop_triggered = True
                    break  # 一旦触发个股止损，不再检查其他个股
            
            # 如果个股级别已触发止损，跳过配对级别检查
            if single_stop_triggered:
                continue
            
            # Level 2: 配对级别 - 配对整体回撤（10%）
            pair_drawdown = self._calculate_pair_drawdown(symbol1, symbol2)
            if pair_drawdown < -self.max_pair_drawdown:
                self.algorithm.Debug(
                    f"[T+0-L2] 配对止损 [{symbol1.Value},{symbol2.Value}]: "
                    f"回撤{pair_drawdown*100:.1f}% < -{self.max_pair_drawdown*100}%"
                )
                if pair_key not in processed_for_t0:
                    liquidation_pairs.append((symbol1, symbol2, "配对止损"))
                    self.risk_triggers['pair_stop_loss'] += 1
                    processed_for_t0.add(pair_key)
        
        # Level 3: 组合级别 - Portfolio总回撤（预留接口）
        # portfolio_drawdown = self._calculate_portfolio_drawdown()
        # if portfolio_drawdown < -self.max_portfolio_drawdown:
        #     # 可以选择平仓部分或全部持仓
        #     pass
        
        # Level 4: 行业级别 - 行业集中度（30%）
        sector_overlimit_pairs = self._check_sector_concentration()
        for pair in sector_overlimit_pairs:
            self.algorithm.Debug(
                f"[T+0-L4] 行业集中度超限，平仓最早配对: [{pair[0].Value},{pair[1].Value}]"
            )
            liquidation_pairs.append((pair[0], pair[1], "行业集中度超限"))
        
        return liquidation_pairs
    
    def _perform_t1_checks(self) -> List[Tuple]:
        """
        T+1风控检查：基于历史信息检查
        
        检查项目：
        1. 配对完整性（同向持仓、单边持仓等）
        2. 持仓时间限制（30天）
        3. 异常订单检测（OrderTracker报告的异常）
        
        Returns:
            List[Tuple]: 需要平仓的配对列表，格式为[(symbol1, symbol2, reason), ...]
        """
        liquidation_pairs = []
        active_pairs = self._get_active_pairs()
        
        if not active_pairs:
            return liquidation_pairs
        
        for pair_info in active_pairs:
            symbol1, symbol2 = pair_info['pair']
            holding_days = pair_info['holding_days']
            
            # 1. 配对完整性检查
            integrity_status = self._check_pair_integrity(symbol1, symbol2)
            if integrity_status in ["same_direction_error", "single_side_only"]:
                self.algorithm.Debug(
                    f"[T+1] 配对异常 [{symbol1.Value},{symbol2.Value}]: {integrity_status}"
                )
                liquidation_pairs.append((symbol1, symbol2, f"配对异常:{integrity_status}"))
                continue  # 发现异常直接处理，不再检查其他条件
            
            # 2. 持仓时间检查
            if holding_days > self.max_holding_days:
                self.algorithm.Debug(
                    f"[T+1] 持仓超时 [{symbol1.Value},{symbol2.Value}]: "
                    f"{holding_days}天 > {self.max_holding_days}天"
                )
                liquidation_pairs.append((symbol1, symbol2, "持仓超时"))
                self.risk_triggers['holding_timeout'] += 1
        
        # 3. 异常订单检查（使用改进后的逻辑）
        abnormal_pairs = self._check_abnormal_orders()
        if abnormal_pairs:
            # 为了兼容测试，保留原有的日志格式
            self.algorithm.Debug(
                f"[RiskManagement] OrderTracker报告{len(abnormal_pairs)}个潜在异常配对"
            )
        for symbol1, symbol2 in abnormal_pairs:
            # 避免重复处理已经在配对完整性检查中处理过的配对
            pair_key = (symbol1, symbol2) if symbol1 and symbol2 and symbol1.Value < symbol2.Value else (symbol1, symbol2)
            already_processed = any(
                (s1 == symbol1 and s2 == symbol2) or (s1 == symbol2 and s2 == symbol1)
                for s1, s2, _ in liquidation_pairs
            )
            if not already_processed:
                self.algorithm.Debug(
                    f"[T+1] 订单异常 [{symbol1.Value if symbol1 else 'None'},"
                    f"{symbol2.Value if symbol2 else 'None'}]"
                )
                liquidation_pairs.append((symbol1, symbol2, "订单执行异常"))
        
        return liquidation_pairs
    
    def _get_active_pairs(self) -> List[Dict]:
        """
        获取所有活跃持仓配对
        
        v3.8.0: 优先从CentralPairManager获取，fallback到原有逻辑
        
        Returns:
            List[Dict]: 活跃配对信息列表，每个元素包含:
                - 'pair': (symbol1, symbol2)
                - 'holding_days': 持仓天数
        """
        # v3.8.0: 优先从CentralPairManager获取活跃配对信息
        if self.central_pair_manager:
            return self.central_pair_manager.get_active_pairs()
        
        # Fallback到原有逻辑
        active_pairs = []
        processed_symbols = set()
        
        # 使用列表副本避免迭代时修改字典的问题
        portfolio_items = list(self.algorithm.Portfolio.items())
        
        # 遍历所有持仓
        for symbol, holding in portfolio_items:
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
            
            # 处理价格为0的特殊情况（数据缺失）
            if current_price == 0:
                self.algorithm.Debug(
                    f"[RiskManagement] 警告: {symbol.Value}价格为0，跳过回撤计算"
                )
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
            
            # v3.8.0: 登记平仓到CentralPairManager
            if self.central_pair_manager:
                self.central_pair_manager.register_exit(symbol1, symbol2)
            
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
        
        使用OrderTracker检测异常配对，返回需要平仓的异常配对列表
        
        v3.5.0修复：只处理Portfolio中有实际持仓的异常配对，
        避免未建仓的配对（如AMZN,GM）被错误识别为异常
        
        Returns:
            List[Tuple]: 需要平仓的异常配对列表，格式为[(symbol1, symbol2), ...]
        """
        abnormal_pairs = self.order_tracker.get_abnormal_pairs()
        pairs_to_liquidate = []
        
        if abnormal_pairs:
            self.algorithm.Debug(
                f"[RiskManagement] OrderTracker报告{len(abnormal_pairs)}个潜在异常配对"
            )
            
            for symbol1, symbol2 in abnormal_pairs:
                # v3.5.0关键修复：验证Portfolio中是否真的有持仓
                has_position = False
                
                # 检查symbol1是否有持仓
                if symbol1 and self.algorithm.Portfolio[symbol1].Invested:
                    has_position = True
                
                # 检查symbol2是否有持仓
                if symbol2 and self.algorithm.Portfolio[symbol2].Invested:
                    has_position = True
                
                # 只处理真正有持仓的异常配对
                if has_position:
                    self.algorithm.Debug(
                        f"[RiskManagement] 确认异常配对（有持仓）: [{symbol1.Value if symbol1 else 'None'},{symbol2.Value if symbol2 else 'None'}]"
                    )
                    pairs_to_liquidate.append((symbol1, symbol2))
                else:
                    # 无持仓的配对仅记录日志，不生成平仓指令
                    self.algorithm.Debug(
                        f"[RiskManagement] 忽略无持仓配对: [{symbol1.Value if symbol1 else 'None'},{symbol2.Value if symbol2 else 'None'}]"
                    )
        
        return pairs_to_liquidate
    
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
    
    def _check_sector_concentration(self) -> List[Tuple[Symbol, Symbol]]:
        """
        检查行业集中度并返回需要平仓的配对
        
        计算每个行业的资金暴露比例，如果超过阈值，
        则平仓该行业中建仓时间最早的配对。
        
        Returns:
            List[Tuple[Symbol, Symbol]]: 需要平仓的配对列表
        """
        # 获取所有活跃配对
        active_pairs = self._get_active_pairs()
        if not active_pairs:
            return []
        
        # 计算每个行业的暴露
        sector_exposure = {}
        sector_pairs = {}  # 记录每个行业的配对列表
        total_portfolio_value = self.algorithm.Portfolio.TotalPortfolioValue
        
        if total_portfolio_value <= 0:
            return []
        
        for pair_info in active_pairs:
            symbol1, symbol2 = pair_info['pair']
            
            # 获取两只股票的行业信息
            sector1 = self._get_symbol_sector(symbol1)
            sector2 = self._get_symbol_sector(symbol2)
            
            # 配对应该在同一行业
            if sector1 != sector2:
                self.algorithm.Debug(
                    f"[RiskManagement] 警告：配对跨行业 [{symbol1.Value}({sector1}), "
                    f"{symbol2.Value}({sector2})]"
                )
                continue
            
            if sector1 is None:
                continue
            
            # 计算该配对的市值暴露
            holding1 = self.algorithm.Portfolio[symbol1]
            holding2 = self.algorithm.Portfolio[symbol2]
            pair_value = abs(holding1.HoldingsValue) + abs(holding2.HoldingsValue)
            pair_exposure = pair_value / total_portfolio_value
            
            # 累加到行业暴露
            if sector1 not in sector_exposure:
                sector_exposure[sector1] = 0
                sector_pairs[sector1] = []
            
            sector_exposure[sector1] += pair_exposure
            sector_pairs[sector1].append({
                'pair': (symbol1, symbol2),
                'holding_days': pair_info['holding_days'],
                'exposure': pair_exposure
            })
        
        # 检查是否有行业超过阈值
        pairs_to_liquidate = []
        for sector, exposure in sector_exposure.items():
            if exposure > self.sector_exposure_threshold:
                sector_name = self.sector_code_to_name.get(sector, str(sector))
                self.algorithm.Debug(
                    f"[RiskManagement] 行业集中度超限 - {sector_name}: "
                    f"{exposure*100:.1f}% > {self.sector_exposure_threshold*100}%"
                )
                
                # 找出该行业中建仓时间最早的配对
                if sector in sector_pairs and sector_pairs[sector]:
                    # 按持仓天数排序，选择最早的配对
                    oldest_pair = max(sector_pairs[sector], key=lambda x: x['holding_days'])
                    pairs_to_liquidate.append(oldest_pair['pair'])
                    
                    self.algorithm.Debug(
                        f"[RiskManagement] 选择平仓最早配对: "
                        f"[{oldest_pair['pair'][0].Value}, {oldest_pair['pair'][1].Value}], "
                        f"持仓{oldest_pair['holding_days']}天"
                    )
                    
                    self.risk_triggers['sector_concentration'] += 1
        
        return pairs_to_liquidate
    
    def _get_symbol_sector(self, symbol: Symbol):
        """
        获取股票的行业代码
        
        Args:
            symbol: 股票代码
            
        Returns:
            行业代码，如果无法获取则返回None
        """
        try:
            security = self.algorithm.Securities[symbol]
            if hasattr(security, 'Fundamentals') and security.Fundamentals:
                if hasattr(security.Fundamentals, 'AssetClassification'):
                    return security.Fundamentals.AssetClassification.MorningstarSectorCode
        except Exception as e:
            self.algorithm.Debug(f"[RiskManagement] 获取{symbol.Value}行业信息失败: {e}")
        
        return None