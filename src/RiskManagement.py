# region imports
from AlgorithmImports import *
from typing import List, Dict, Tuple, Optional
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
    - 查询: PairLedger获取持仓信息
    - 更新: 通过返回的targets触发平仓
    
    设计理念:
    - 独立性: 不依赖其他模块的信号
    - 主动性: 主动扫描和监控风险
    - 保守性: 宁可错杀不可放过
    - 透明性: 详细记录所有风控动作
    """
    
    def __init__(self, algorithm, config: dict, pair_ledger):
        """
        初始化风险管理模型
        
        Args:
            algorithm: QuantConnect算法实例
            config: 风控配置参数
            pair_ledger: 配对账本实例
        """
        super().__init__()
        self.algorithm = algorithm
        self.pair_ledger = pair_ledger
        
        # 风控参数
        self.max_holding_days = config.get('max_holding_days', 60)
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
        
        if not active_pairs:
            return risk_adjusted_targets
        
        # 检查每个配对的风控条件
        liquidation_pairs = []
        
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
            risk_adjusted_targets.extend(liquidation_targets)
            
            self.algorithm.Debug(
                f"[RiskManagement] 风控平仓{len(liquidation_pairs)}对, "
                f"生成{len(liquidation_targets)}个平仓指令"
            )
        
        return risk_adjusted_targets
    
    def _get_active_pairs(self) -> List[Dict]:
        """
        获取所有活跃持仓配对
        
        Returns:
            List[Dict]: 活跃配对信息列表
        """
        # 从PairLedger获取风控数据
        return self.pair_ledger.get_risk_control_data()
    
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
                targets.append(PortfolioTarget(symbol1, 0))
                processed_symbols.add(symbol1)
            
            if symbol2 not in processed_symbols:
                targets.append(PortfolioTarget(symbol2, 0))
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
            # 未来可以从PairLedger获取beta进行检查
            
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