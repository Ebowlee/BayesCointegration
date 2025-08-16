# region imports
from AlgorithmImports import *
from typing import List, Dict, Tuple, Optional
# endregion


class BayesianCointegrationRiskManagementModel(RiskManagementModel):
    """
    贝叶斯协整策略风险管理模型
    
    负责实时监控和管理策略风险，作为独立的风控层运行。
    包含五大风控机制：
    1. 配对止损 - 监控配对整体回撤
    2. 时间管理 - 分级持仓时间控制
    3. 行业集中度 - 防止单一行业过度暴露
    4. 市场异常 - 系统性风险保护
    5. 单腿检查 - 防止非对冲风险
    
    前置风控（已在其他模块实现）：
    - AlphaModel: 配对过期清理、持仓检查、Z-score极端值控制
    - PortfolioConstruction: 质量过滤、冷却期管理、资金管理
    - CentralPairManager: 开仓资格验证、实例管理
    """
    
    def __init__(self, algorithm, config: dict = None, 
                 sector_code_to_name: dict = None,
                 central_pair_manager = None):
        """
        初始化风险管理模型
        
        Args:
            algorithm: 算法实例
            config: 风控参数配置
            sector_code_to_name: 行业代码映射
            central_pair_manager: CPM实例（可选）
        """
        super().__init__()
        self.algorithm = algorithm
        self.config = config or {}
        self.sector_code_to_name = sector_code_to_name or {}
        self.central_pair_manager = central_pair_manager
        
        # 风控参数
        self.max_pair_drawdown = self.config.get('max_pair_drawdown', 0.10)  # 配对最大回撤10%
        self.max_single_drawdown = self.config.get('max_single_drawdown', 0.15)  # 单边最大回撤15%
        self.sector_exposure_threshold = self.config.get('sector_exposure_threshold', 0.50)  # 行业集中度50%
        self.sector_reduction_factor = self.config.get('sector_reduction_factor', 0.75)  # 超限时缩减到75%
        
        # 时间管理参数
        self.loss_cutoff_days = 15  # 15天仍亏损则全部平仓
        self.partial_exit_days = 20  # 20天减仓50%
        self.max_holding_days = 30  # 30天强制平仓
        
        # 市场异常参数
        self.market_crash_threshold = 0.03  # 市场单日跌3%触发
        self.market_severe_threshold = 0.05  # 市场单日跌5%触发
        
        # 内部状态
        self.risk_triggers = {}  # 记录风控触发情况
        
    def ManageRisk(self, algorithm: QCAlgorithm, targets: List[PortfolioTarget]) -> List[PortfolioTarget]:
        """
        风险管理主方法 - 依次执行各项风控检查
        
        Args:
            algorithm: 算法实例
            targets: 来自PortfolioConstruction的目标仓位
            
        Returns:
            List[PortfolioTarget]: 风险调整后的目标仓位
        """
        # 重置风控触发记录
        self.risk_triggers = {
            'expired_pairs': [],     # 新增：过期配对
            'pair_drawdown': [],
            'holding_time': [],
            'sector_concentration': [],
            'market_condition': [],
            'incomplete_pairs': []
        }
        
        # 0. 过期配对检查（最高优先级）
        targets = self._check_expired_pairs(targets)
        
        # 1. 配对止损检查
        targets = self._check_pair_drawdown(targets)
        
        # 2. 时间管理检查
        targets = self._check_holding_time(targets)
        
        # 3. 单腿异常检查（提前处理）
        targets = self._check_incomplete_pairs(targets)
        
        # 4. 行业集中度检查
        targets = self._check_sector_concentration(targets)
        
        # 5. 市场异常检查
        targets = self._check_market_condition(targets)
        
        # 输出风控触发汇总
        self._log_risk_summary()
        
        return targets
    
    # ----------------------------------------------------------------------
    def _check_expired_pairs(self, targets: List[PortfolioTarget]) -> List[PortfolioTarget]:
        """
        检查并处理过期配对
        
        从CPM获取过期配对信息并生成平仓指令。
        过期配对是指上个周期存在但本周期不再有协整关系的配对。
        
        Args:
            targets: 当前目标仓位
            
        Returns:
            调整后的目标仓位（加入过期配对的平仓指令）
        """
        if not self.central_pair_manager:
            return targets
        
        # 从CPM获取风控警报
        alerts = self.central_pair_manager.get_risk_alerts()
        expired_pairs = alerts.get('expired_pairs', [])
        
        if not expired_pairs:
            return targets
        
        # 处理每个过期配对
        for item in expired_pairs:
            pair_key = item['pair_key']  # (symbol1_value, symbol2_value)
            reason = item.get('reason', 'expired')
            
            # 从pair_key获取symbol值
            symbol1_value, symbol2_value = pair_key
            
            # 查找对应的Symbol对象
            symbol1 = None
            symbol2 = None
            for symbol in self.algorithm.Securities.Keys:
                if symbol.Value == symbol1_value:
                    symbol1 = symbol
                elif symbol.Value == symbol2_value:
                    symbol2 = symbol
            
            # 生成平仓targets
            if symbol1 and self.algorithm.Portfolio[symbol1].Invested:
                targets.append(PortfolioTarget.Percent(self.algorithm, symbol1, 0))
                self.algorithm.Debug(f"[RiskManagement] 清理过期配对资产: {symbol1_value}")
            
            if symbol2 and self.algorithm.Portfolio[symbol2].Invested:
                targets.append(PortfolioTarget.Percent(self.algorithm, symbol2, 0))
                self.algorithm.Debug(f"[RiskManagement] 清理过期配对资产: {symbol2_value}")
            
            # 记录风控触发
            self.risk_triggers['expired_pairs'].append(f"{symbol1_value}&{symbol2_value}")
        
        # 清空CPM的过期配对列表（已处理）
        self.central_pair_manager.clear_expired_pairs()
        
        if len(expired_pairs) > 0:
            self.algorithm.Debug(f"[RiskManagement] 处理{len(expired_pairs)}个过期配对")
        
        return targets
    
    # ----------------------------------------------------------------------
    def _check_pair_drawdown(self, targets: List[PortfolioTarget]) -> List[PortfolioTarget]:
        """
        检查配对整体回撤和单边回撤
        
        监控每个配对的整体盈亏和单边盈亏，当超过阈值时触发止损。
        - 配对回撤 = 总盈亏 / 总成本
        - 单边回撤 = 单边盈亏 / 单边成本
        
        Args:
            targets: 当前目标仓位
            
        Returns:
            调整后的目标仓位
        """
        if not self.central_pair_manager:
            return targets
        
        # 获取有持仓的活跃配对
        active_pairs = self.central_pair_manager.get_active_pairs_with_position()
        if not active_pairs:
            return targets
        
        # 构建Symbol查找字典（优化查找性能）
        symbols_dict = {s.Value: s for s in self.algorithm.Securities.Keys}
        
        # 检查每个配对的回撤
        for pair_info in active_pairs:
            pair_key = pair_info['pair_key']
            symbol1_value, symbol2_value = pair_key
            
            # 快速查找Symbol对象
            symbol1 = symbols_dict.get(symbol1_value)
            symbol2 = symbols_dict.get(symbol2_value)
            
            if not symbol1 or not symbol2:
                continue
            
            # 获取持仓信息
            h1 = self.algorithm.Portfolio[symbol1]
            h2 = self.algorithm.Portfolio[symbol2]
            
            # 确保两边都有持仓
            if not h1.Invested or not h2.Invested:
                continue
            
            # 计算成本（始终为正）
            cost1 = abs(h1.HoldingsCost)
            cost2 = abs(h2.HoldingsCost)
            total_cost = cost1 + cost2
            
            if total_cost <= 0:
                continue
            
            # 计算回撤率（UnrealizedProfit 已经考虑了做多/做空方向）
            # 负值表示亏损，正值表示盈利
            s1_drawdown = h1.UnrealizedProfit / cost1 if cost1 != 0 else 0
            s2_drawdown = h2.UnrealizedProfit / cost2 if cost2 != 0 else 0
            total_pnl = h1.UnrealizedProfit + h2.UnrealizedProfit
            pair_drawdown = total_pnl / total_cost
            
            # 判断是否需要止损
            trigger_single = s1_drawdown < -self.max_single_drawdown or s2_drawdown < -self.max_single_drawdown
            trigger_pair = pair_drawdown < -self.max_pair_drawdown
            
            if trigger_single or trigger_pair:
                # 生成平仓targets
                targets.extend([
                    PortfolioTarget.Percent(self.algorithm, symbol1, 0),
                    PortfolioTarget.Percent(self.algorithm, symbol2, 0)
                ])
                
                # 记录风控触发信息
                if trigger_single:
                    trigger_symbol = 'symbol1' if s1_drawdown < -self.max_single_drawdown else 'symbol2'
                    self.risk_triggers['pair_drawdown'].append({
                        'pair': f"{symbol1_value}&{symbol2_value}",
                        'type': 'single_stop',
                        's1_drawdown': s1_drawdown,
                        's2_drawdown': s2_drawdown,
                        'trigger': trigger_symbol
                    })
                    
                    self.algorithm.Debug(
                        f"[RiskManagement] 单边止损触发: {symbol1_value}&{symbol2_value}, "
                        f"{symbol1_value}回撤{s1_drawdown:.2%}, {symbol2_value}回撤{s2_drawdown:.2%}"
                    )
                else:
                    self.risk_triggers['pair_drawdown'].append({
                        'pair': f"{symbol1_value}&{symbol2_value}",
                        'type': 'pair_stop',
                        'drawdown': pair_drawdown,
                        'pnl': total_pnl,
                        'cost': total_cost
                    })
                    
                    self.algorithm.Debug(
                        f"[RiskManagement] 配对止损触发: {symbol1_value}&{symbol2_value}, "
                        f"回撤{pair_drawdown:.2%}, 亏损${total_pnl:.2f}"
                    )
        
        return targets
    
    # ----------------------------------------------------------------------
    def _check_holding_time(self, targets: List[PortfolioTarget]) -> List[PortfolioTarget]:
        """
        分级时间管理
        
        根据持仓时间执行不同的风控动作：
        - 15天：仍亏损则全部平仓
        - 20天：无论盈亏减仓50%
        - 30天：强制全部平仓
        
        注意：当前依赖 entry_time 字段，需要 Execution 模块完成后填充
        
        Args:
            targets: 当前目标仓位
            
        Returns:
            调整后的目标仓位
        """
        if not self.central_pair_manager:
            return targets
        
        # 获取配对持仓信息（只返回有 entry_time 的配对）
        pairs_info = self.central_pair_manager.get_pairs_with_holding_info()
        if not pairs_info:
            # 暂时没有 entry_time 数据，等待 Execution 模块完成
            return targets
        
        # 构建Symbol查找字典
        symbols_dict = {s.Value: s for s in self.algorithm.Securities.Keys}
        
        for pair_info in pairs_info:
            pair_key = pair_info['pair_key']
            holding_days = pair_info['holding_days']
            symbol1_value, symbol2_value = pair_key
            
            # 查找Symbol对象
            symbol1 = symbols_dict.get(symbol1_value)
            symbol2 = symbols_dict.get(symbol2_value)
            
            if not symbol1 or not symbol2:
                continue
            
            # 获取持仓信息
            h1 = self.algorithm.Portfolio[symbol1]
            h2 = self.algorithm.Portfolio[symbol2]
            
            if not h1.Invested or not h2.Invested:
                continue
            
            # 计算配对盈亏
            total_pnl = h1.UnrealizedProfit + h2.UnrealizedProfit
            pair_str = f"{symbol1_value}&{symbol2_value}"
            
            # 分级时间管理逻辑
            if holding_days >= self.max_holding_days:
                # 30天强制平仓
                targets.extend([
                    PortfolioTarget.Percent(self.algorithm, symbol1, 0),
                    PortfolioTarget.Percent(self.algorithm, symbol2, 0)
                ])
                self.risk_triggers['holding_time'].append({
                    'pair': pair_str,
                    'type': 'max_holding',
                    'days': holding_days
                })
                self.algorithm.Debug(f"[RiskManagement] 持仓超时强制平仓: {pair_str}, 已持有{holding_days}天")
                
            elif holding_days >= self.partial_exit_days:
                # 20天减仓50%
                current_value1 = h1.HoldingsValue
                current_value2 = h2.HoldingsValue
                total_value = self.algorithm.Portfolio.TotalPortfolioValue
                
                if total_value > 0:
                    targets.extend([
                        PortfolioTarget.Percent(self.algorithm, symbol1, (current_value1 / total_value) * 0.5),
                        PortfolioTarget.Percent(self.algorithm, symbol2, (current_value2 / total_value) * 0.5)
                    ])
                    self.risk_triggers['holding_time'].append({
                        'pair': pair_str,
                        'type': 'partial_exit',
                        'days': holding_days
                    })
                    self.algorithm.Debug(f"[RiskManagement] 持仓{holding_days}天减仓50%: {pair_str}")
                
            elif holding_days >= self.loss_cutoff_days and total_pnl < 0:
                # 15天仍亏损则平仓
                targets.extend([
                    PortfolioTarget.Percent(self.algorithm, symbol1, 0),
                    PortfolioTarget.Percent(self.algorithm, symbol2, 0)
                ])
                self.risk_triggers['holding_time'].append({
                    'pair': pair_str,
                    'type': 'loss_cutoff',
                    'days': holding_days,
                    'pnl': total_pnl
                })
                self.algorithm.Debug(
                    f"[RiskManagement] 持仓{holding_days}天仍亏损平仓: {pair_str}, 亏损${total_pnl:.2f}"
                )
        
        return targets
    
    # ----------------------------------------------------------------------
    def _check_incomplete_pairs(self, targets: List[PortfolioTarget]) -> List[PortfolioTarget]:
        """
        单腿异常检查
        
        检测和处理不完整的配对持仓：
        - 配对中只有一腿有持仓
        - 孤立持仓（不在任何配对中）
        
        Args:
            targets: 当前目标仓位
            
        Returns:
            调整后的目标仓位
        """
        if not self.central_pair_manager:
            return targets
        
        # 1. 获取所有有持仓的股票（symbol.Value集合）
        invested_symbols = set()
        for symbol in self.algorithm.Securities.Keys:
            if self.algorithm.Portfolio[symbol].Invested:
                invested_symbols.add(symbol.Value)
        
        if not invested_symbols:
            return targets
        
        # 2. 获取所有活跃配对（CPM认为应该有持仓的）
        active_pairs = self.central_pair_manager.get_active_pairs_with_position()
        
        # 3. 收集需要平仓的股票
        symbols_to_liquidate = set()
        paired_symbols = set()  # 记录所有在配对中的股票
        
        # 检查每个配对的完整性
        for pair_info in active_pairs:
            pair_key = pair_info['pair_key']
            symbol1_value, symbol2_value = pair_key
            
            # 记录这两个股票在配对中
            paired_symbols.add(symbol1_value)
            paired_symbols.add(symbol2_value)
            
            # 检查两边是否都有持仓
            has_s1 = symbol1_value in invested_symbols
            has_s2 = symbol2_value in invested_symbols
            
            # 如果只有一边有持仓（单腿异常）
            if has_s1 != has_s2:
                # 找出有持仓的那一边
                if has_s1:
                    symbols_to_liquidate.add(symbol1_value)
                    self.algorithm.Debug(
                        f"[RiskManagement] 检测到单腿持仓: {symbol1_value} "
                        f"(缺失配对: {symbol2_value})"
                    )
                    self.risk_triggers['incomplete_pairs'].append({
                        'type': 'incomplete_pair',
                        'symbol': symbol1_value,
                        'pair_key': pair_key
                    })
                else:
                    symbols_to_liquidate.add(symbol2_value)
                    self.algorithm.Debug(
                        f"[RiskManagement] 检测到单腿持仓: {symbol2_value} "
                        f"(缺失配对: {symbol1_value})"
                    )
                    self.risk_triggers['incomplete_pairs'].append({
                        'type': 'incomplete_pair',
                        'symbol': symbol2_value,
                        'pair_key': pair_key
                    })
        
        # 4. 检查孤立持仓（有持仓但不在任何配对中）
        isolated_positions = invested_symbols - paired_symbols
        for symbol_value in isolated_positions:
            symbols_to_liquidate.add(symbol_value)
            self.algorithm.Debug(
                f"[RiskManagement] 检测到孤立持仓: {symbol_value} (不在任何配对中)"
            )
            self.risk_triggers['incomplete_pairs'].append({
                'type': 'isolated_position',
                'symbol': symbol_value
            })
        
        # 5. 生成平仓指令
        if symbols_to_liquidate:
            # 构建Symbol查找字典
            symbols_dict = {s.Value: s for s in self.algorithm.Securities.Keys}
            
            for symbol_value in symbols_to_liquidate:
                symbol = symbols_dict.get(symbol_value)
                if symbol:
                    targets.append(PortfolioTarget.Percent(self.algorithm, symbol, 0))
            
            self.algorithm.Debug(
                f"[RiskManagement] 单腿异常平仓: {len(symbols_to_liquidate)}个持仓"
            )
        
        return targets
    
    # ----------------------------------------------------------------------
    def _check_sector_concentration(self, targets: List[PortfolioTarget]) -> List[PortfolioTarget]:
        """
        行业集中度控制
        
        监控各行业的仓位占比，防止单一行业过度暴露。
        当某行业超过阈值（如50%）时，该行业所有配对同比例缩减。
        
        Args:
            targets: 当前目标仓位
            
        Returns:
            调整后的目标仓位
        """
        if not self.central_pair_manager:
            return targets
        
        # 1. 获取有持仓的活跃配对
        active_pairs = self.central_pair_manager.get_active_pairs_with_position()
        if not active_pairs:
            return targets
        
        total_portfolio_value = self.algorithm.Portfolio.TotalPortfolioValue
        if total_portfolio_value <= 0:
            return targets
        
        # 2. 一次遍历收集所有信息
        from collections import defaultdict
        sector_data = defaultdict(lambda: {'exposure': 0, 'pairs': []})
        symbols_dict = {s.Value: s for s in self.algorithm.Securities.Keys}
        
        for pair_info in active_pairs:
            pair_key = pair_info['pair_key']
            symbol1_value, symbol2_value = pair_key
            
            # 获取Symbol对象
            symbol1 = symbols_dict.get(symbol1_value)
            symbol2 = symbols_dict.get(symbol2_value)
            if not symbol1 or not symbol2:
                continue
            
            # 获取持仓信息（一次获取，后续复用）
            h1 = self.algorithm.Portfolio[symbol1]
            h2 = self.algorithm.Portfolio[symbol2]
            
            # 计算暴露（无需检查Invested，0值自然处理）
            pair_exposure = abs(h1.HoldingsValue) + abs(h2.HoldingsValue)
            if pair_exposure == 0:  # 跳过无实际持仓的配对
                continue
            
            # 获取行业（只需查一只股票）
            try:
                security = self.algorithm.Securities[symbol1]
                if not security.Fundamentals:
                    continue
                sector_code = security.Fundamentals.AssetClassification.MorningstarSectorCode
            except:
                continue
            
            # 预计算权重，存储所有信息
            sector_data[sector_code]['exposure'] += pair_exposure
            sector_data[sector_code]['pairs'].append({
                'pair_key': pair_key,
                'symbol1': symbol1,
                'symbol2': symbol2,
                'weight1': h1.HoldingsValue / total_portfolio_value,
                'weight2': h2.HoldingsValue / total_portfolio_value
            })
        
        # 3. 处理超限行业（只有有数据时才处理）
        if not sector_data:
            return targets
            
        total_exposure = sum(d['exposure'] for d in sector_data.values())
        
        # 4. 检查每个行业的暴露
        for sector_code, data in sector_data.items():
            exposure_ratio = data['exposure'] / total_exposure
            
            if exposure_ratio > self.sector_exposure_threshold:
                sector_name = self.sector_code_to_name.get(sector_code, str(sector_code))
                
                self.algorithm.Debug(
                    f"[RiskManagement] 行业集中度超限: {sector_name} "
                    f"暴露{exposure_ratio:.1%} > 阈值{self.sector_exposure_threshold:.1%}, "
                    f"缩减到{self.sector_reduction_factor:.1%}"
                )
                
                # 使用预存的信息生成targets
                for pair_data in data['pairs']:
                    new_weight1 = pair_data['weight1'] * self.sector_reduction_factor
                    new_weight2 = pair_data['weight2'] * self.sector_reduction_factor
                    
                    targets.append(PortfolioTarget.Percent(self.algorithm, pair_data['symbol1'], new_weight1))
                    targets.append(PortfolioTarget.Percent(self.algorithm, pair_data['symbol2'], new_weight2))
                    
                    # 记录风控触发
                    symbol1_value, symbol2_value = pair_data['pair_key']
                    self.risk_triggers['sector_concentration'].append({
                        'sector': sector_name,
                        'exposure_ratio': exposure_ratio,
                        'pair': f"{symbol1_value}&{symbol2_value}",
                        'action': 'reduce'
                    })
                
                self.algorithm.Debug(
                    f"[RiskManagement] {sector_name}行业{len(data['pairs'])}个配对"
                    f"缩减到{self.sector_reduction_factor:.0%}"
                )
        
        return targets
    
    # ----------------------------------------------------------------------
    def _check_market_condition(self, targets: List[PortfolioTarget]) -> List[PortfolioTarget]:
        """
        市场异常保护
        
        监控市场整体状况（如SPY），在系统性风险时采取保护措施：
        - 单日跌3%：暂停新建仓
        - 单日跌5%：所有仓位减半
        
        Args:
            targets: 当前目标仓位
            
        Returns:
            调整后的目标仓位
        """
        # TODO: 实现市场异常检查
        # 1. 获取SPY或市场指数数据
        # 2. 计算单日涨跌幅
        # 3. 根据阈值调整targets
        
        self.algorithm.Debug("[RiskManagement] 市场异常检查 - 待实现")
        return targets
    
    # ----------------------------------------------------------------------
    def _log_risk_summary(self):
        """
        输出风控触发汇总日志
        """
        total_triggers = sum(len(v) for v in self.risk_triggers.values())
        if total_triggers > 0:
            self.algorithm.Debug(f"[RiskManagement] 本次触发{total_triggers}项风控")
            for risk_type, triggers in self.risk_triggers.items():
                if triggers:
                    self.algorithm.Debug(f"  - {risk_type}: {len(triggers)}项")