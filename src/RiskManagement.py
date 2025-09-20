# region imports
from AlgorithmImports import *
from typing import List, Dict, Tuple, Optional
# endregion


class BayesianCointegrationRiskManagementModel(RiskManagementModel):
    """
    贝叶斯协整策略风险管理模型 - 简化版本

    负责基础风险控制，作为最后一道防线运行。
    核心功能：
    1. 极端亏损止损 - 防止单只股票过度亏损
    2. 基础风控检查 - 验证持仓合理性

    简化设计理念：
    - 直接基于Portfolio查询，无需复杂的状态管理
    - 专注于最核心的风控功能
    - 其他风控已在Alpha和PC层实现
    """

    def __init__(self, algorithm, config: dict = None,
                 sector_code_to_name: dict = None):
        """
        初始化风险管理模型

        Args:
            algorithm: 算法实例
            config: 风控参数配置
            sector_code_to_name: 行业代码映射
        """
        super().__init__()
        self.algorithm = algorithm
        self.config = config or {}
        self.sector_code_to_name = sector_code_to_name or {}

        # 风控参数
        self.max_single_loss = self.config.get('max_single_loss', 0.15)  # 单只股票最大亏损15%
        self.max_portfolio_loss = self.config.get('max_portfolio_loss', 0.10)  # 组合最大回撤10%

        # 内部状态
        self.risk_triggers = {}  # 记录风控触发情况

    def ManageRisk(self, algorithm: QCAlgorithm, targets: List[PortfolioTarget]) -> List[PortfolioTarget]:
        """
        风险管理主方法 - 执行基础风控检查

        Args:
            algorithm: 算法实例
            targets: 来自PortfolioConstruction的目标仓位

        Returns:
            List[PortfolioTarget]: 风险调整后的目标仓位
        """
        # 重置风控触发记录
        self.risk_triggers = {
            'single_loss': [],
            'portfolio_loss': []
        }

        # 1. 基础风控检查（直接基于Portfolio）
        targets = self._check_basic_risk_controls(targets)

        # 输出风控触发汇总
        self._log_risk_summary()

        return targets

    def _check_basic_risk_controls(self, targets: List[PortfolioTarget]) -> List[PortfolioTarget]:
        """
        基础风控检查 - 直接基于Portfolio状态

        Args:
            targets: 当前目标仓位

        Returns:
            调整后的目标仓位
        """
        # 检查所有持仓
        invested_symbols = []
        for symbol in self.algorithm.Securities.keys():
            holding = self.algorithm.Portfolio[symbol]
            if holding.Invested:
                invested_symbols.append((symbol, holding))

        if not invested_symbols:
            return targets  # 无持仓，直接返回

        # 检查单只股票极端亏损
        for symbol, holding in invested_symbols:

            # 计算单只股票亏损率
            if holding.AveragePrice > 0:
                loss_rate = (holding.AveragePrice - holding.Price) / holding.AveragePrice

                # 多头亏损检查
                if holding.Quantity > 0 and loss_rate > self.max_single_loss:
                    self._add_liquidation_target(targets, symbol, f"多头亏损{loss_rate:.1%}")

                # 空头亏损检查（价格上涨导致亏损）
                elif holding.Quantity < 0 and loss_rate < -self.max_single_loss:
                    self._add_liquidation_target(targets, symbol, f"空头亏损{abs(loss_rate):.1%}")

        return targets

    def _add_liquidation_target(self, targets: List[PortfolioTarget], symbol: Symbol, reason: str):
        """
        添加清仓目标

        Args:
            targets: 目标仓位列表
            symbol: 要清仓的股票
            reason: 清仓原因
        """
        # 检查是否已有该股票的target
        existing_target = None
        for target in targets:
            if target.Symbol == symbol:
                existing_target = target
                break

        if existing_target:
            # 修改现有target为清仓
            existing_target.Quantity = 0
        else:
            # 添加新的清仓target
            targets.append(PortfolioTarget.Percent(self.algorithm, symbol, 0))

        # 记录风控触发
        self.risk_triggers['single_loss'].append(f"{symbol.Value}: {reason}")
        self.algorithm.Debug(f"[RiskManagement] 风控平仓: {symbol.Value} - {reason}")

    def _log_risk_summary(self):
        """输出风控触发汇总"""
        total_triggers = sum(len(v) for v in self.risk_triggers.values())

        if total_triggers > 0:
            self.algorithm.Debug(f"[RiskManagement] 风控汇总: {total_triggers}个触发")

            for risk_type, items in self.risk_triggers.items():
                if items:
                    self.algorithm.Debug(f"[RiskManagement] {risk_type}: {len(items)}个")