# region imports
from AlgorithmImports import *
from typing import Generator
from datetime import timedelta
# endregion


class PortfolioLevelRiskManager:
    """组合层面风控管理器"""

    def __init__(self, algorithm):
        self.algorithm = algorithm
        self.config = algorithm.config
        self.portfolio_high_water_mark = self.config.main['cash']  # 历史最高净值

    def manage_portfolio_risks(self) -> bool:
        """
        执行所有Portfolio风控管理
        返回: True = 需要立即结束OnData, False = 可以继续
        """
        # 1. 爆仓风控（最高优先级）
        if self._check_account_blowup():
            return True  # 立即结束

        # 2. 回撤风控
        if self._check_max_drawdown():
            return True  # 立即结束

        # 3. 市场波动（只设冷却，不结束）
        self._check_market_volatility()

        # 4. 行业集中度（只减仓，不结束）
        self._check_sector_concentration()

        return False  # 可以继续

    def _check_account_blowup(self) -> bool:
        """
        检查账户爆仓风险 - 基于初始资金的亏损比例
        返回: True=触发爆仓线, False=安全
        """
        portfolio_value = self.algorithm.Portfolio.TotalPortfolioValue
        initial_capital = self.config.main['cash']

        # 计算亏损比例（而不是剩余比例）
        loss_ratio = (initial_capital - portfolio_value) / initial_capital

        # 从配置读取阈值
        BLOWUP_THRESHOLD = self.config.risk_management['blowup_threshold']

        if loss_ratio > BLOWUP_THRESHOLD:  # 如果亏损超过阈值
            self.algorithm.Debug(
                f"[爆仓风控] 触发！"
                f"当前:{portfolio_value:,.0f} "
                f"初始:{initial_capital:,.0f} "
                f"亏损:{loss_ratio:.1%}", 1  # 显示亏损比例
            )
            # 清仓所有持仓
            self.algorithm.Liquidate()
            # 设置永久冷却
            cooldown_days = self.config.risk_management['blowup_cooldown_days']
            self.algorithm.strategy_cooldown_until = self.algorithm.Time + timedelta(days=cooldown_days)
            self.algorithm.Debug(f"[爆仓风控] 策略将冷却{cooldown_days}天至{self.algorithm.strategy_cooldown_until.date()}", 1)
            return True

        return False

    def _check_max_drawdown(self) -> bool:
        """
        检查最大回撤 - 基于历史最高点
        返回: True=触发回撤线, False=正常
        """
        portfolio_value = self.algorithm.Portfolio.TotalPortfolioValue

        # 更新历史最高点
        self.portfolio_high_water_mark = max(self.portfolio_high_water_mark, portfolio_value)

        # 计算回撤
        drawdown = 0
        if self.portfolio_high_water_mark > 0:
            drawdown = (self.portfolio_high_water_mark - portfolio_value) / self.portfolio_high_water_mark

        # 从配置读取阈值
        MAX_DRAWDOWN_THRESHOLD = self.config.risk_management['drawdown_threshold']

        if drawdown > MAX_DRAWDOWN_THRESHOLD:
            self.algorithm.Debug(
                f"[回撤风控] 触发！"
                f"最高点:{self.portfolio_high_water_mark:,.0f} "
                f"当前:{portfolio_value:,.0f} "
                f"回撤:{drawdown:.1%}", 1
            )
            # 清仓所有持仓
            self.algorithm.Liquidate()
            # 设置冷却期
            cooldown_days = self.config.risk_management['drawdown_cooldown_days']
            self.algorithm.strategy_cooldown_until = self.algorithm.Time + timedelta(days=cooldown_days)
            self.algorithm.Debug(f"[回撤风控] 策略将冷却{cooldown_days}天至{self.algorithm.strategy_cooldown_until.date()}", 1)
            return True

        return False

    def _check_market_volatility(self):
        """
        检查市场剧烈波动
        只设置冷却，不return True
        """
        # 获取SPY的当日数据
        if not self.algorithm.Securities.ContainsKey(self.algorithm.market_benchmark):
            return

        spy = self.algorithm.Securities[self.algorithm.market_benchmark]

        # 计算日内波动率 (High-Low)/Open
        if spy.Open == 0:
            return

        daily_volatility = (spy.High - spy.Low) / spy.Open

        # 从配置读取阈值
        MARKET_SEVERE_THRESHOLD = self.config.risk_management['market_severe_threshold']

        if daily_volatility > MARKET_SEVERE_THRESHOLD:
            self.algorithm.Debug(
                f"[市场波动风控] 触发！"
                f"SPY日内波动:{daily_volatility:.2%} > 阈值:{MARKET_SEVERE_THRESHOLD:.2%}", 1
            )
            # 设置冷却期
            cooldown_days = self.config.risk_management['market_cooldown_days']
            self.algorithm.strategy_cooldown_until = self.algorithm.Time + timedelta(days=cooldown_days)
            self.algorithm.Debug(f"[市场波动风控] 冷却新开仓至{self.algorithm.strategy_cooldown_until.date()}", 1)
            # 注意：不return True，继续执行

    def _check_sector_concentration(self):
        """
        检查行业集中度
        只减仓，不return True
        """
        sector_concentrations = self.algorithm.pairs_manager.get_sector_concentration()
        threshold = self.config.risk_management['sector_exposure_threshold']
        target = self.config.risk_management['sector_target_exposure']

        for sector, info in sector_concentrations.items():
            if info['concentration'] > threshold:
                self.algorithm.Debug(
                    f"[行业集中度风控] {sector}行业超限! "
                    f"集中度:{info['concentration']:.1%} > {threshold:.0%}", 1
                )

                # 计算减仓比例
                reduction_ratio = target / info['concentration']

                # 对该行业所有配对同比例减仓
                for pair in info['pairs']:
                    pair.reduce_position(reduction_ratio)

                self.algorithm.Debug(
                    f"[行业集中度风控] {sector}行业{info['pair_count']}个配对 "
                    f"同比例减仓至{target:.0%}", 1
                )


class PairLevelRiskManager:
    """配对层面风控管理器"""

    def __init__(self, algorithm):
        self.algorithm = algorithm
        self.pair_high_water_marks = {}  # 配对历史最高净值
        self.config = algorithm.config.risk_management

    def manage_position_risks(self, pairs_with_position) -> Generator:
        """
        生成器：管理有持仓配对的风险，yield安全配对
        只处理有持仓的配对，进行风控检查
        风控检查顺序：持仓超期 -> 异常持仓 -> 配对回撤

        Args:
            pairs_with_position: 可以是字典的values()或列表
        """
        for pair in pairs_with_position:
            # 1. 持仓超期检查
            if self._check_holding_timeout(pair):
                continue  # 已在内部清仓

            # 2. 异常持仓检查（单边 + 方向相同）
            if self._check_position_anomaly(pair):
                continue  # 已在内部清仓

            # 3. 配对回撤检查
            if self._check_pair_drawdown(pair):
                continue  # 已在内部清仓

            # 通过所有风控检查
            yield pair

    def _check_holding_timeout(self, pair) -> bool:
        """检查持仓超期"""
        holding_days = pair.get_pair_holding_days()
        if holding_days is not None and holding_days > pair.max_holding_days:
            self.algorithm.Debug(
                f"[配对风控] {pair.pair_id} 持仓{holding_days}天"
                f"超过限制{pair.max_holding_days}天，执行清仓", 1
            )
            # 直接使用Liquidate
            self.algorithm.Liquidate(pair.symbol1)
            self.algorithm.Liquidate(pair.symbol2)
            return True
        return False

    def _check_position_anomaly(self, pair) -> bool:
        """检查异常持仓"""
        position_info = pair.get_position_info()

        is_partial = position_info['status'] == 'PARTIAL'
        is_same_direction = position_info['direction'] == 'same_direction'

        if is_partial or is_same_direction:
            if is_partial:
                reason = "单边持仓"
            else:
                reason = f"两腿方向相同({position_info['qty1']:+.0f}/{position_info['qty2']:+.0f})"

            self.algorithm.Debug(f"[配对风控] {pair.pair_id} 发现{reason}，执行清仓", 1)
            # 直接使用Liquidate
            self.algorithm.Liquidate(pair.symbol1)
            self.algorithm.Liquidate(pair.symbol2)
            return True
        return False

    def _check_pair_drawdown(self, pair) -> bool:
        """检查配对回撤"""
        if pair.get_position_status() != 'NORMAL':
            return False

        pair_value = pair.get_position_value()
        pair_id = pair.pair_id

        # 更新或初始化high_water_mark
        if pair_id not in self.pair_high_water_marks:
            self.pair_high_water_marks[pair_id] = pair_value
        else:
            self.pair_high_water_marks[pair_id] = max(
                self.pair_high_water_marks[pair_id],
                pair_value
            )

        # 计算回撤
        hwm = self.pair_high_water_marks[pair_id]
        if hwm > 0:
            drawdown = (hwm - pair_value) / hwm
            MAX_PAIR_DD = self.config['max_pair_drawdown']

            if drawdown > MAX_PAIR_DD:
                self.algorithm.Debug(
                    f"[配对风控] {pair.pair_id} 回撤{drawdown:.1%}"
                    f"超过限制{MAX_PAIR_DD:.0%}（HWM:{hwm:.0f}→{pair_value:.0f}），执行清仓", 1
                )
                # 直接使用Liquidate
                self.algorithm.Liquidate(pair.symbol1)
                self.algorithm.Liquidate(pair.symbol2)
                # 清理记录
                del self.pair_high_water_marks[pair_id]
                return True
        return False