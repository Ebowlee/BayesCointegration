# region imports
from AlgorithmImports import *
from typing import List
# endregion

class BayesianCointegrationRiskManagementModel(RiskManagementModel):
    """
    【模块: RiskManagement - 贝叶斯协整风险管理模型】

    职责：
    - 监控和管理由投资组合构建模块生成的投资组合目标的风险。
    - 根据预设的组合最大回撤参数调整或否决目标。
    - 根据预设的最大持仓天数对超期持仓进行平仓。
    - 确保策略在可接受的风险范围内运行。

    调用流程：
    - QuantConnect 框架在 PortfolioConstructionModel 生成目标后调用 `ManageRisk` 方法。
    - `ManageRisk` 内部协调调用处理最大回撤和最大持仓时间的风险逻辑。
    """

    def __init__(self,algorithm: QCAlgorithm):
        """
        Attributes:
            self.algorithm (QCAlgorithm): QCAlgorithm 的引用。
            self.portfolio_max_drawdown_pct (float): 最大回撤阈值。
            self.single_asset_drawdown_pct (float): 单个资产最大回撤阈值。
            self.trailing_stop_threshold (float): 回撤触发阈值。
            self.portfolio_peak_value (float): 用于计算回撤的投资组合历史峰值。
            self.current_drawdown (float): 当前计算的组合回撤百分比。
        """
        super().__init__()
        self.algorithm = algorithm
        self.portfolio_max_drawdown_pct = 0.15
        self.single_asset_drawdown_pct = 0.25
        self.trailing_stop_threshold = 0.15 
        self.portfolio_peak_value: float = 0.0 
        self.current_drawdown: float = 0.0
        self.trailing_highs = {} 
        self.algorithm.Debug("[RiskManagement] 初始化完成")



    def ManageRisk(self, algorithm: QCAlgorithm, targets: List[PortfolioTarget]) -> List[PortfolioTarget]:
        """
        - 此方法由 QuantConnect 框架自动调用，用于评估和调整投资组合目标。
        - 协调最大回撤风险检查和最大持仓时间风险检查逻辑。
        """
        self.algorithm.Debug(f"[RiskManagement] -- [ManageRisk] 接收到 {len(targets)} 个 PortfolioTarget")

        # 0. targers 配对
        pair_targets = self._pair_targets(targets)
        self.algorithm.Debug(f"[RiskManagement] -- [ManageRisk] 配对后得到 {len(pair_targets)} 个协整对")
        
        # 1. 检查最大回撤
        pair_targets = self._force_liquidate_if_portfolio_max_drawdown_reached(algorithm, pair_targets)
        
        # 2. 过滤无效观望目标
        drop_passive_flat_targets = self._filter_passive_flat_targets(algorithm, pair_targets)
        self.algorithm.Debug(f"[RiskManagement] -- [ManageRisk] 过滤无效观望目标后得到 {len(drop_passive_flat_targets)} 个协整对")

        # 3. 检查协整对中是否有超跌资产
        drop_pair_crash_targets = self._force_liquidate_if_pair_crash(algorithm, drop_passive_flat_targets)
        self.algorithm.Debug(f"[RiskManagement] -- [ManageRisk] 检查协整对中是否有超跌资产后得到 {len(drop_pair_crash_targets)} 个协整对")

        # 4. 检查协整对中是否有超跌资产
        drop_trailing_stop_targets = self._force_liquidate_if_trailing_stop_triggered(algorithm, drop_pair_crash_targets)
        self.algorithm.Debug(f"[RiskManagement] -- [ManageRisk] 检查协整对中是否有超跌资产后得到 {len(drop_trailing_stop_targets)} 个协整对")

        # 5. 拆解配对返回 QCAlgorithm 的 targets
        unpaired_targets = [t for pair_target in drop_trailing_stop_targets for t in pair_target]
        self.algorithm.Debug(f"[RiskManagement] -- [ManageRisk] 拆解配对后返回 {len(unpaired_targets)} 个目标")
        
        return unpaired_targets  



    def _pair_targets(self, targets: List[PortfolioTarget]) -> List[Tuple[PortfolioTarget, PortfolioTarget]]:
        """
        将目标列表按顺序两两配对，用于处理协整对风控。
        若目标数量为奇数，则打印错误并返回空列表。
        """
        if len(targets) % 2 != 0:
            self.algorithm.Debug("[RiskManagement] -- [PairTargets] 目标数为奇数，配对失败！")
            return []

        pair_targets = []
        temp_pair = []

        for target in targets:
            if target.Quantity is None:
                continue

            temp_pair.append(target)
            if len(temp_pair) == 2:
                pair_targets.append((temp_pair[0], temp_pair[1]))
                temp_pair = []

        return pair_targets



    def _force_liquidate_if_portfolio_max_drawdown_reached(self, algorithm: QCAlgorithm, pair_targets: List[Tuple[PortfolioTarget, PortfolioTarget]]) -> List[Tuple[PortfolioTarget, PortfolioTarget]]:
        """
        若组合当前回撤超过最大允许阈值，触发全仓强制平仓，并清除相关 insights。
        """
        if not self.algorithm.Portfolio.Invested:
            return pair_targets

        current_value = algorithm.Portfolio.TotalPortfolioValue

        if current_value >= self.portfolio_peak_value:
            self.portfolio_peak_value = current_value
            self.current_drawdown = 0.0
        else:
            if self.portfolio_peak_value > 0:
                self.current_drawdown = (self.portfolio_peak_value - current_value) / self.portfolio_peak_value
            else:
                self.current_drawdown = 0.0

        self.algorithm.Debug(f"[RiskManagement] -- [max_drawdown] 当前市值: {current_value:.0f}, 峰值: {self.portfolio_peak_value:.0f}, 回撤: {self.current_drawdown:.1%}")

        if self.current_drawdown >= self.portfolio_max_drawdown_pct:
            self.algorithm.Liquidate()
            self.algorithm.Debug(f"[RiskManagement] -- [max_drawdown] 触发最大回撤: {self.current_drawdown:.1%} > {self.portfolio_max_drawdown_pct:.1%}，全部清仓！！！")
            return []

        return pair_targets




    def _filter_passive_flat_targets(self, algorithm: QCAlgorithm, pair_targets: List[Tuple[PortfolioTarget, PortfolioTarget]]) -> List[Tuple[PortfolioTarget, PortfolioTarget]]:
        """
        拦截那些目标值为 0 且当前仓位也为 0 的观望信号（无意义清仓指令），避免下达无效 SetHoldings。
        """
        filtered_pair_targets = []

        for pair_target in pair_targets:
            t1, t2 = pair_target
            if (t1.Quantity == 0 and not algorithm.Portfolio[t1.Symbol].Invested) or (t2.Quantity == 0 and not algorithm.Portfolio[t2.Symbol].Invested):
                algorithm.Debug(f"[RiskManagement] -- [FilterPassiveFlat] : [{t1.Symbol.Value},{t2.Symbol.Value}] 当前无持仓，跳过清仓指令")
                continue
            filtered_pair_targets.append(pair_target)

        return filtered_pair_targets



    def _force_liquidate_if_pair_crash(self, algorithm: QCAlgorithm, pair_targets: List[Tuple[PortfolioTarget, PortfolioTarget]]) -> List[Tuple[PortfolioTarget, PortfolioTarget]]:
        """
        若协整对中任一资产价格较其平均建仓成本下跌超过 50%，则强制平仓整个协整对。
        该函数假设每轮 targets 仍以协整对为单位（两个 symbol 成对出现）。
        """
        force_liquidate_targets = []

        for pair_target in pair_targets:
            t1, t2 = pair_target
            symbol1, symbol2 = t1.Symbol, t2.Symbol
            holding1, holding2 = algorithm.Portfolio[symbol1], algorithm.Portfolio[symbol2]
            avg_price1, avg_price2 = holding1.AveragePrice, holding2.AveragePrice

            if avg_price1 == 0 or avg_price2 == 0:
                continue  # 避免除零

            current_price1, current_price2 = algorithm.Securities[symbol1].Price, algorithm.Securities[symbol2].Price
            drawdown_pct1, drawdown_pct2 = (avg_price1 - current_price1) / avg_price1, (avg_price2 - current_price2) / avg_price2

            if drawdown_pct1 >= self.single_asset_drawdown_pct or drawdown_pct2 >= self.single_asset_drawdown_pct:
                algorithm.Debug(f"[RiskManagement] -- [pair_crash] {symbol1.Value} 下跌 {drawdown_pct1:.1%} , {symbol2.Value} 下跌 {drawdown_pct2:.1%}，协整对强制平仓！！！")
                algorithm.insights.cancel([t1.Symbol, t2.Symbol])
                algorithm.Liquidate(t1.Symbol, tag="PairCrashDrawdown>50%")
                algorithm.Liquidate(t2.Symbol, tag="PairCrashDrawdown>50%")
                force_liquidate_targets.append(pair_target)

        # 从 pair_targets 中移除 force_liquidate_targets
        filtered_pair_targets = [t for t in pair_targets if t not in force_liquidate_targets]

        return filtered_pair_targets



    def _force_liquidate_if_trailing_stop_triggered(self, algorithm: QCAlgorithm, pair_targets: List[Tuple[PortfolioTarget, PortfolioTarget]]) -> List[Tuple[PortfolioTarget, PortfolioTarget]]:
        """
        若协整对中任一资产价格自持仓期间最高点回撤超过 threshold(如 15%)，则强制平仓整个协整对。
        """
        filtered_pair_targets = []

        for t1, t2 in pair_targets:
            symbols = [t1.Symbol, t2.Symbol]
            triggered = False

            for symbol in symbols:
                price = algorithm.Securities[symbol].Price
                peak = self.trailing_highs.get(symbol, price)
                self.trailing_highs[symbol] = max(peak, price)

                drawdown = (self.trailing_highs[symbol] - price) / self.trailing_highs[symbol]

                if drawdown >= self.trailing_stop_threshold:
                    algorithm.Debug(f"[RiskManagement] -- [TrailingStopLoss] {symbol.Value} 从 {self.trailing_highs[symbol]:.0f} 回撤至 {drawdown:.1%} 超过 {self.trailing_stop_threshold:.0%}，触发 Trailing Stop，协整对强制平仓！！！")
                    for s in symbols:
                        algorithm.Liquidate(s, tag="TrailingStop")
                        self.trailing_highs.pop(s, None)
                    algorithm.insights.cancel(symbols)
                    triggered = True
                    break

            if not triggered:
                filtered_pair_targets.append((t1, t2))

        return filtered_pair_targets

