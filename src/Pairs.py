# region imports
from AlgorithmImports import *
import numpy as np
from typing import Dict, Optional, Tuple
from src.execution import OpenIntent, CloseIntent
# v7.10.6: 常量已移至config.constants统一管理，不再需要constants.py
# endregion


class PositionMode:
    """持仓模式常量 - 避免魔法字符串"""
    NONE = 'NONE'                    # 无持仓
    LONG_SPREAD = 'LONG_SPREAD'      # 正常多头价差
    SHORT_SPREAD = 'SHORT_SPREAD'    # 正常空头价差
    PARTIAL_LEG1 = 'PARTIAL_LEG1'    # 异常: 仅LEG1持仓
    PARTIAL_LEG2 = 'PARTIAL_LEG2'    # 异常: 仅LEG2持仓
    ANOMALY_SAME = 'ANOMALY_SAME'    # 异常: 同向持仓


class Pairs:
    """
    配对交易的核心数据对象

    核心职责:
    - 数据提供者: 持仓追踪、价格计算、成本计算
    - 信号生成器: Z-score计算、交易信号生成
    - 意图生成器: 开仓/平仓意图编码
    - 历史追踪者: 交易统计、Z-score追踪(信号/开仓/平仓三阶段)

    不负责: 风险检查、资金分配、订单执行(由RiskManager/ExecutionManager/OrderExecutor负责)
    """

    # ===== 1. 初始化与参数管理 =====

    @classmethod
    def from_model_result(cls, algorithm, model_result: Dict, config) -> 'Pairs':
        """
        工厂方法：从贝叶斯建模结果创建 Pairs 对象

        技术细节：
        - cls 是 Pairs 类本身（Python 自动传递）
        - cls(...) 调用构造函数 __init__，创建并返回 Pairs 实例对象
        - 返回值是 Pairs 实例，可直接调用实例方法（get_signal, open_position 等）

        Args:
            algorithm: QCAlgorithm 实例
            model_result: BayesianModeler 输出的单个建模结果
                格式: {
                    'symbol1': Symbol, 'symbol2': Symbol,
                    'alpha_mean': float, 'beta_mean': float,
                    'residual_mean': float, 'residual_std': float,
                    'quality_score': float, 'industry_group': str
                }
            config: 配对交易配置对象（PairsTradingConfig dataclass from src/config.py）

        Returns:
            Pairs: 新创建的 Pairs 实例对象

        调用: main.py
        """
        # 创建Pairs对象
        pair = cls(algorithm, model_result, config)

        return pair


    def __init__(self, algorithm, model_data, config):
        """
        从贝叶斯建模结果初始化
        model_data包含配对的统计参数和基础信息
        """
        # === 算法引用 ===
        self.algorithm = algorithm
        self.config = config

        # === 基础信息 ===
        self.symbol1 = model_data['symbol1']
        self.symbol2 = model_data['symbol2']
        self.pair_id = (self.symbol1.Value, self.symbol2.Value)
        self.industry_code = int(model_data['industry_code'])                  # v7.40.8: 统一使用整数格式 (删除冗余industry_group字段)

        # === 统计参数(从贝叶斯建模获得) ===
        self.alpha_mean = model_data['alpha_mean']                              # 截距(对数空间)
        self.beta_mean = model_data['beta_mean']                                # 斜率(对数空间)
        self.residual_mean = model_data['residual_mean']                        # 残差均值(对数空间,理论上接近0)
        self.residual_std = model_data['residual_std']                          # 残差标准差(对数空间)
        self.quality_score = model_data['quality_score']                        # 配对质量分数
        self.half_life = model_data.get('half_life')                            # v7.11.0: 半衰期天数(供自适应持仓超时使用)
        self.half_life_std = model_data.get('half_life_std', 0)                 # v7.13.0: 半衰期不确定性(标准差)

        # === 交易阈值 (改良C方案 - 从pairs_trading统一读取) ===
        self.entry_threshold_lower = config.entry_threshold_lower               # 1.2σ
        self.entry_threshold_upper = config.entry_threshold_upper               # 1.8σ
        self.exit_threshold = config.exit_threshold                             # 0.3σ
        self.stop_loss_threshold = config.stop_loss_threshold                   # 2.3σ

        # === 保证金参数 ===
        self.margin_long = config.margin_requirement_long
        self.margin_short = config.margin_requirement_short

        # === 历史追踪 ===
        self.creation_time = algorithm.Time                                    # 首次创建时间

        # === 交易历史统计 (已平仓交易 - 加权平均累计) ===
        self.trade_count = 0                                                   # 历史总交易次数
        self.win_count = 0                                                     # 历史盈利次数
        self.pair_realized_pnl = 0.0                                           # 已实现PnL (已平仓交易累计,加权平均分子)
        self.pair_past_invested_capital = 0.0                                   # 已平仓累计投入资本 (加权平均分母)

        # === 时间追踪 ===
        self.pair_opened_time = None                                           # 配对开仓时间(双腿都成交的时刻)
        self.pair_closed_time = None                                           # 配对平仓时间(双腿都成交的时刻)
        self.last_close_reason = None                                          # 最后平仓原因 (参见 config.constants['close_reasons'])

        # === 交易质量追踪 (三阶段Z-score用于事后分析) ===
        self.entry_zscore = None                                               # 信号触发时Z-score(分析决策质量)
        self.fill_zscore_open = None                                           # 开仓成交时Z-score(分析执行滑点)
        self.fill_zscore_close = None                                          # 平仓成交时Z-score(分析退出质量)

        # === 持仓追踪(OrderTicket-based,避免Portfolio全局查询混淆) ===
        self.tracked_qty1 = 0                                                  # 配对专属持仓追踪(symbol1)
        self.tracked_qty2 = 0                                                  # 配对专属持仓追踪(symbol2)

        # === 成本追踪(配对专属PnL计算基础) ===
        self.entry_price1 = None                                               # symbol1开仓均价
        self.entry_price2 = None                                               # symbol2开仓均价
        self.exit_price1 = None                                                # symbol1平仓价(None=持仓中, 有值=已平仓)
        self.exit_price2 = None                                                # symbol2平仓价(None=持仓中, 有值=已平仓)


    def update_params(self, new_pair):
        """
        从新的Pairs对象更新统计参数 (v7.53.2: 单一职责重构)

        调用位置:
            - PairsManager.update_pairs() (PairsManager.py)
            - 触发时机: 每月选股后,配对ID已存在于all_pairs字典时
            - 调用链路: OnSecuritiesChanged → _run_analysis_pipeline →
                       步骤6-7 → PairsManager.update_pairs() → update_params()

        单一职责原则 (v7.53.2):
            - 本方法只负责更新参数
            - 持仓检查由调用方 (PairsManager.update_pairs) 在外部处理
            - 这样做的好处: 调用方可以根据持仓状态做额外处理(如日志预警)

        设计理念:
            - 持仓期间参数冻结,避免"参数漂移"导致信号混乱
            - 信号系统(Entry/Exit/Stop)已经能够处理beta变化风险
            - "让信号说话" - 不通过频繁调参来干预系统

        Args:
            new_pair: 新创建的Pairs对象(含最新建模结果)

        Note:
            v8.x.x当前状态: 步骤6-7待恢复,调用路径尚未激活
        """
        # 更新所有贝叶斯模型参数
        self.alpha_mean = new_pair.alpha_mean
        self.beta_mean = new_pair.beta_mean
        self.residual_mean = new_pair.residual_mean
        self.residual_std = new_pair.residual_std
        self.quality_score = new_pair.quality_score


    # ===== 2. 纯计算层 (Pure Computation) =====
    # 特征: @staticmethod, 无self依赖, 纯函数, 可独立单元测试

    @staticmethod
    def _calculate_zscore_pure(price1: float, price2: float,
                                alpha: float, beta: float,
                                residual_mean: float, residual_std: float) -> Optional[float]:
        """
        纯计算: Z-score (v7.50.0 分层重构)

        公式:
            log_residual = ln(price1) - (alpha + beta × ln(price2))
            zscore = (log_residual - residual_mean) / residual_std

        Args:
            price1: symbol1的价格
            price2: symbol2的价格
            alpha: 截距 (对数空间)
            beta: 斜率 (对数空间)
            residual_mean: 残差均值
            residual_std: 残差标准差

        Returns:
            Z-score值 或 None (计算失败时)

        设计原则:
            - 无self依赖: 所有参数显式传入
            - 可测试性: 可用简单assert测试, 无需mock QuantConnect
        """
        # 参数校验
        if price1 <= 0 or price2 <= 0 or residual_std <= 0:
            return None

        try:
            log_residual = np.log(price1) - (alpha + beta * np.log(price2))
            zscore = (log_residual - residual_mean) / residual_std
            return zscore
        except (ValueError, ZeroDivisionError, OverflowError):
            return None

    @staticmethod
    def _calculate_leg_values_pure(
        allocated_amount: float,
        signal: str,
        beta: float,
        margin_long: float,
        margin_short: float
    ) -> Tuple[Optional[float], Optional[float]]:
        """
        纯计算: Beta对冲两腿市值 (v7.50.0 分层重构)

        核心公式 (v7.42.0):
            LONG_SPREAD: γ₁ = β·(m_S-1)/m_L
            SHORT_SPREAD: γ₂ = β·m_L/(m_S-1)

        Args:
            allocated_amount: 分配资金 (A)
            signal: 交易信号 ('LONG_SPREAD' 或 'SHORT_SPREAD')
            beta: Beta系数绝对值 (用于对冲比例计算)
            margin_long: 做多保证金率 (如0.5)
            margin_short: 做空初始保证金率 (如1.5)

        Returns:
            (value_1, value_2): 两腿目标市值, 失败返回(None, None)

        设计原则:
            - 无self依赖: 所有参数显式传入
            - 无日志输出: 错误由上层处理
            - 可测试性: 纯数学计算, 可独立单元测试
        """
        # 参数校验
        if allocated_amount <= 0 or beta <= 0:
            return None, None
        if margin_long <= 0 or margin_short <= 1:
            return None, None

        k_S = margin_short - 1.0  # 做空实际本金占用系数

        if signal == 'LONG_SPREAD':
            gamma = beta * k_S / margin_long
            x1 = allocated_amount / (1 + gamma)
            x2 = allocated_amount * gamma / (1 + gamma)
            value_1 = x1 / margin_long
            value_2 = x2 / k_S

        elif signal == 'SHORT_SPREAD':
            gamma = beta * margin_long / k_S
            x1 = allocated_amount / (1 + gamma)
            x2 = allocated_amount * gamma / (1 + gamma)
            value_1 = x1 / k_S
            value_2 = x2 / margin_long

        else:
            return None, None

        # 安全检查: 资金分配合理性
        if x1 <= 0 or x2 <= 0:
            return None, None

        return value_1, value_2

    @staticmethod
    def _calculate_invested_capital_pure(
        qty1: float, qty2: float,
        entry_price1: float, entry_price2: float
    ) -> Optional[float]:
        """
        纯计算: 配对投入资本 (v7.51.0 术语规范化)

        行业标准公式：
            invested_capital = 0.5 × (market_value1 + market_value2)

        Args:
            qty1: symbol1持仓数量
            qty2: symbol2持仓数量
            entry_price1: symbol1入场价
            entry_price2: symbol2入场价

        Returns:
            投入资本 或 None (参数无效时)

        设计原则:
            - 无self依赖: 所有参数显式传入
            - 可测试性: 纯数学计算, 可独立单元测试
        """
        # 参数校验
        if entry_price1 is None or entry_price2 is None:
            return None
        if entry_price1 <= 0 or entry_price2 <= 0:
            return None

        market_value1 = abs(qty1 * entry_price1)
        market_value2 = abs(qty2 * entry_price2)

        return 0.5 * (market_value1 + market_value2)


    # ===== 3. 数据访问层 (Data Access) =====

    # 3A. 实时数据查询

    def get_price_from_bar(self, data):
        """
        从TradeBar获取Close价格

        用途:
        - 信号生成: 基于bar收盘价计算Z-score
        - 意图生成: 基于bar收盘价计算开仓数量
        - Beta对冲: 基于bar收盘价计算腿位价值

        价格源区分:
        - TradeBar.Close: 用于决策(本方法) ← 当前bar的收盘价
        - Portfolio[].Price: 用于状态查询 ← 实时市场价格
        """
        # 增强检查: symbol存在且data不为None
        if (self.symbol1 in data and self.symbol2 in data and
            data[self.symbol1] is not None and data[self.symbol2] is not None):

            # 获取Close价格
            price1 = data[self.symbol1].Close
            price2 = data[self.symbol2].Close

            # 价格有效性检查(避免负价或零价)
            if price1 > 0 and price2 > 0:
                return (price1, price2)

        return None


    @property
    def position_mode(self):
        """
        获取当前持仓模式 (属性调用 - v7.40.11终极简化版)

        PositionMode 常量之一:
        - NONE: 无持仓
        - LONG_SPREAD / SHORT_SPREAD: 正常持仓
        - PARTIAL_LEG1 / PARTIAL_LEG2 / ANOMALY_SAME: 异常持仓
        """
        # 使用配对专属的tracked_qty(从OrderTicket提取的实际成交数量)
        qty1 = self.tracked_qty1
        qty2 = self.tracked_qty2

        # 统一判断持仓模式(整合状态+方向)
        if qty1 == 0 and qty2 == 0:
            return PositionMode.NONE
        elif qty1 > 0 and qty2 < 0:
            return PositionMode.LONG_SPREAD
        elif qty1 < 0 and qty2 > 0:
            return PositionMode.SHORT_SPREAD
        elif qty1 != 0 and qty2 == 0:
            self.algorithm.Debug(f"[持仓异常] {self.pair_id} 单边持仓LEG1: qty1={qty1:+.0f}")
            return PositionMode.PARTIAL_LEG1
        elif qty1 == 0 and qty2 != 0:
            self.algorithm.Debug(f"[持仓异常] {self.pair_id} 单边持仓LEG2: qty2={qty2:+.0f}")
            return PositionMode.PARTIAL_LEG2
        else:  # 同向持仓
            self.algorithm.Debug(f"[持仓异常] {self.pair_id} 同向持仓: qty1={qty1:+.0f}, qty2={qty2:+.0f}")
            return PositionMode.ANOMALY_SAME


    # 2B. 持仓状态查询 (依赖 position_mode)

    def has_position(self) -> bool:
        """检查是否有持仓（任何类型）"""
        return self.position_mode != PositionMode.NONE


    def has_normal_position(self) -> bool:
        """检查是否有正常持仓（LONG_SPREAD 或 SHORT_SPREAD）"""
        return self.position_mode in [PositionMode.LONG_SPREAD, PositionMode.SHORT_SPREAD]


    def has_anomaly_position(self) -> bool:
        """检查是否有异常持仓（单边或同向）"""
        return self.position_mode in [PositionMode.PARTIAL_LEG1, PositionMode.PARTIAL_LEG2, PositionMode.ANOMALY_SAME]


    # 2C. 财务计算

    def get_pair_unrealized_pnl(self) -> Optional[float]:
        """
        获取配对浮动盈亏 (Unrealized PnL)

        计算公式:
        - 浮动PnL = (当前市值 - 开仓成本)
        - 当前市值 = qty1×price1 + qty2×price2  (考虑多空方向)
        - 开仓成本 = qty1×entry_price1 + qty2×entry_price2
        """
        # 支持所有持仓类型(包括异常持仓)
        if not self.has_position():
            return None

        # 检查是否有开仓价格(防御性编程)
        if self.entry_price1 is None or self.entry_price2 is None:
            return None

        # 使用实时价格
        portfolio = self.algorithm.Portfolio
        price1 = portfolio[self.symbol1].Price
        price2 = portfolio[self.symbol2].Price

        # 计算当前市值(考虑方向: 多头为正,空头为负)
        current_value = (self.tracked_qty1 * price1 + self.tracked_qty2 * price2)

        # 计算开仓成本
        entry_value = (self.tracked_qty1 * self.entry_price1 + self.tracked_qty2 * self.entry_price2)

        # PnL = 当前市值 - 开仓成本
        pnl = current_value - entry_value

        return pnl


    def get_pair_current_invested_capital(self) -> Optional[float]:
        """
        数据访问层: 获取配对当前投入资本 (持仓中)

        职责: 读取self属性, 委托给纯计算层

        行业标准公式：
            invested_capital = 0.5 × (market_value1 + market_value2)

        物理含义：
            - 表示实际投入的自有资金 (配对交易的成本基础)
            - 用于ROI计算分母: ROI = PnL / invested_capital
            - 不区分多空方向，只关心总市值规模

        调用方：
            - _update_trade_stats(): 计算ROI分母
            - _log_close_completion(): 显示当前收益率
            - PairDrawdownRule.check(): 计算回撤率分母
            - PairCumulativeLoss.check(): 计算累计亏损率分母
            - PairsManager._aggregate_current_invested_capital(): 行业聚合

        设计演进:
            v7.40.7: 修正为行业标准公式
            v7.50.0: 计算逻辑提取到 _calculate_pair_cost_pure()
            v7.51.0: 重命名 get_pair_cost → get_pair_invested_capital
            v7.56.2: 重命名 get_pair_invested_capital → get_pair_current_invested_capital
        """
        # 支持所有持仓类型(包括异常持仓)
        if not self.has_position():
            return None

        return self._calculate_invested_capital_pure(
            self.tracked_qty1, self.tracked_qty2,
            self.entry_price1, self.entry_price2
        )


    def get_pair_realized_pnl(self) -> float:
        """
        获取已实现盈亏（历史累计）- v7.40.6

        职责：
            - 返回所有已平仓交易的累计PnL（美元）
            - 每次平仓时在 _update_trade_stats() 中累加
            - 使用平仓价格计算（exit_price1/exit_price2）
        """
        return self.pair_realized_pnl


    def get_net_exposure(self) -> Optional[float]:
        """
        计算净敞口 (Dollar Net Exposure) - v7.40.0

        公式:
            Net Exposure = value1 + value2
            - 正值: 净多头敞口 (市场涨我赚)
            - 负值: 净空头敞口 (市场跌我赚)
            - 0: 完美对冲
        """
        # 支持所有持仓类型(包括异常持仓)
        if not self.has_position():
            return None

        portfolio = self.algorithm.Portfolio
        price1 = portfolio[self.symbol1].Price
        price2 = portfolio[self.symbol2].Price

        # 计算两腿市值 (带符号)
        val1 = self.tracked_qty1 * price1
        val2 = self.tracked_qty2 * price2

        # 净敞口 = 两腿市值之和
        return val1 + val2


    def get_gross_exposure(self) -> Optional[float]:
        """
        计算总敞口 (Dollar Gross Exposure) - v7.40.0

        公式:
            Gross Exposure = |value1| + |value2|

        物理含义:
            - 衡量配对的总市值规模 (不考虑方向)
            - 作为漂移率计算的分母
        """
        # 支持所有持仓类型(包括异常持仓)
        if not self.has_position():
            return None

        # 使用实时价格 (v7.40.0修正: 移除exit_price逻辑)
        portfolio = self.algorithm.Portfolio
        price1 = portfolio[self.symbol1].Price
        price2 = portfolio[self.symbol2].Price

        # 计算两腿市值 (带符号)
        val1 = self.tracked_qty1 * price1
        val2 = self.tracked_qty2 * price2

        # 总敞口 = 两腿市值绝对值之和
        return abs(val1) + abs(val2)


    # ===== 4. 业务逻辑层 (Business Logic) =====
    # 特征: 组合数据访问层方法, 包含条件判断, 实现复杂计算

    def get_hedge_drift(self) -> Optional[float]:
        """
        计算对冲漂移率 - 衡量持仓偏离完美对冲的程度 (v7.40.1)

        物理含义:
            衡量当前持仓市值偏离"Dollar Neutral"的程度
            Drift% = (Net Exposure / Gross Exposure) × 100

        应用场景:
            仅用于持仓中实时监控对冲质量

        Returns:
            对冲漂移率(%) 或 None(无持仓/数据异常)

        数值解读:
            - 0%: 完美对冲 (净敞口为0)
            - 15%: 警戒区 (开始暴露于Beta风险)
            - 30%+: 危险区 (类似单边持仓)
            - 正值: 净多头敞口 (大盘涨我赚)
            - 负值: 净空头敞口 (大盘跌我赚)
        """
        # 直接调用已有方法 
        net_exp = self.get_net_exposure()
        gross_exp = self.get_gross_exposure()

        # 防御性检查
        if net_exp is None or gross_exp is None or gross_exp == 0:
            return None

        # 计算漂移率 (百分比)
        return (net_exp / gross_exp) * 100


    def get_leg_values(self, allocated_amount: float, signal: str, data):
        """
        数据访问层: 获取Beta对冲两腿市值 (v7.50.0 分层重构)

        职责: 读取self属性, 委托给纯计算层
        重命名: calculate_leg_values → get_leg_values (符合数据访问层命名规范)

        Args:
            allocated_amount: 分配资金 (A)
            signal: 交易信号 ('LONG_SPREAD' 或 'SHORT_SPREAD')
            data: 数据切片 (获取当前价格 - 用于验证)

        Returns:
            (value_1, value_2): 两腿目标市值, 失败返回(None, None)

        设计演进:
            v7.42.0: 通用化公式, 支持任意保证金率配置
            v7.50.0: 计算逻辑提取到 _calculate_leg_values_pure()
        """
        # 获取当前价格 (用于验证)
        prices = self.get_price_from_bar(data)
        if prices is None:
            return None, None
        price_1, price_2 = prices

        # 价格有效性检查
        if price_1 <= 0 or price_2 <= 0:
            self.algorithm.Debug(f"[计算失败] {self.pair_id} 价格异常: Symbol1={price_1}, Symbol2={price_2}")
            return None, None

        # 准备参数并调用纯计算层
        beta = abs(self.beta_mean) if abs(self.beta_mean) != 0 else 1

        result = self._calculate_leg_values_pure(
            allocated_amount, signal, beta,
            self.margin_long, self.margin_short
        )

        # 处理纯计算层返回的错误
        if result[0] is None:
            self.algorithm.Debug(f"[计算失败] {self.pair_id} 信号={signal}, 资金={allocated_amount:.2f}")

        return result


    # 3C. 时间查询

    def get_pair_holding_days(self) -> Optional[int]:
        """
        获取持仓时长(天数) - 从开仓到现在

        Returns:
            持仓天数 或 None(无持仓或无开仓时间)
        """
        if not self.has_normal_position():
            return None

        # 直接访问开仓时间属性
        entry_time = self.pair_opened_time
        if entry_time is not None:
            return (self.algorithm.UtcTime - entry_time).days

        return None


    def get_max_holding_days(self) -> Optional[float]:
        """
        计算理论最大持仓天数 (v7.38.2: 基于指数衰减公式)

        公式来源:
        - 均值回归路径: Z(t) = Z_entry × (0.5)^(t/half_life)
        - 求解半衰期数: n = ln(exit_threshold/entry_zscore) / ln(0.5)
        - 最大持有天数: max_days = n × half_life

        使用场景:
        - 平仓日志输出 (显示动态超时阈值)
        - 风控规则诊断 (PairHoldingTimeoutRule已内联此公式)

        Returns:
            理论最大持仓天数 或 None(数据不完整)
        """
        # 检查必需数据
        if self.entry_zscore is None or self.half_life is None:
            return None

        # 从config读取出场阈值
        exit_threshold = self.algorithm.config.pairs_trading.exit_threshold  # 0.3
        entry_zscore = abs(self.entry_zscore)  # 取绝对值,如-1.9σ → 1.9

        # 计算所需半衰期数
        import math
        n = math.log(exit_threshold / entry_zscore) / math.log(0.5)

        # 计算最大持有天数
        max_days = n * self.half_life

        return max_days


    def get_cooldown_elapsed_days(self) -> Optional[int]:
        """
        获取冷却期已过天数 - 从平仓到现在 (v7.43.0重命名)

        与 get_pair_holding_days() 对称设计:
        - get_pair_holding_days(): 持仓天数 (从开仓到现在)
        - get_cooldown_elapsed_days(): 冷却已过天数 (从平仓到现在)

        术语说明 (v7.43.0):
        - elapsed days: 已经过去的天数 (时间管理标准术语)
        - 配合 PairsManager.get_cooldown_required_days() 使用 (v7.44.0迁移)

        Returns:
            已过天数 或 None(从未平仓)
        """
        if self.pair_closed_time is None:
            return None  # 从未平仓

        return (self.algorithm.UtcTime - self.pair_closed_time).days


    # ===== 5. 外部接口层 (Public API) =====
    # 特征: 对外暴露的核心接口, 整合各层实现完整功能

    def get_zscore(self, price1: float, price2: float) -> Optional[float]:
        """
        数据访问层: 获取Z-score (v7.50.0 分层重构)

        职责: 读取self属性, 委托给纯计算层

        参数:
            price1: symbol1的价格
            price2: symbol2的价格

        返回:
            Z-score值 或 None (计算失败时)

        使用场景:
            # 场景1: 信号生成时
            prices = self.get_price_from_bar(data)
            if prices:
                zscore = self.get_zscore(prices[0], prices[1])

            # 场景2: 订单成交时(分析执行滑点)
            fill_zscore = self.get_zscore(ticket1.AverageFillPrice, ticket2.AverageFillPrice)

        设计演进:
            v7.50.0: 计算逻辑提取到 _calculate_zscore_pure(), 此方法仅负责读取参数并调用
        """
        return self._calculate_zscore_pure(
            price1, price2,
            self.alpha_mean, self.beta_mean,
            self.residual_mean, self.residual_std
        )


    def get_signal(self, data):
        """
        获取交易信号 (cooldown检查在ExecutionManager中进行)
        一步到位的接口,内部自动计算所需信息

        信号类型:
        - 无持仓: LONG_SPREAD / SHORT_SPREAD / WAIT / NO_DATA
        - 有持仓: CLOSE / PAIR_BREAK / HOLD / NO_DATA

        改良C方案阈值:
        - 入场区间: [1.2σ, 1.8σ]
        - 出场阈值: 0.3σ
        - 止损阈值: 2.3σ (方向感知)
        """
        # 获取价格
        prices = self.get_price_from_bar(data)
        if prices is None:
            return 'NO_DATA'

        price1, price2 = prices

        # 计算zscore
        zscore = self.get_zscore(price1, price2)
        if zscore is None:
            return 'NO_DATA'

        # 获取持仓模式
        position_mode = self.position_mode

        # === 无持仓: 入场信号 ===
        if position_mode == PositionMode.NONE:
            abs_zscore = abs(zscore)

            # 检查是否在有效区间内 (改良C方案: 1.2-1.8σ)
            if self.entry_threshold_lower <= abs_zscore <= self.entry_threshold_upper:
                self.entry_zscore = zscore  # 信号触发时记录
                # Z-score高 → spread偏高 → 做空spread
                # Z-score低 → spread偏低 → 做多spread
                return 'SHORT_SPREAD' if zscore > 0 else 'LONG_SPREAD'
            else:
                # 区间外: |zscore| < 1.2σ (信号弱) 或 > 1.8σ (留缓冲给止损)
                return 'WAIT'

        # === 有持仓: 出场信号 ===
        # 方向感知止损 (v7.47.0): 只在亏损方向触发
        # - LONG_SPREAD: 入场时 zscore < 0, 亏损方向是更负
        # - SHORT_SPREAD: 入场时 zscore > 0, 亏损方向是更正
        if position_mode == PositionMode.LONG_SPREAD and zscore < -self.stop_loss_threshold:
            return 'PAIR_BREAK'  # LONG_SPREAD亏损止损
        elif position_mode == PositionMode.SHORT_SPREAD and zscore > self.stop_loss_threshold:
            return 'PAIR_BREAK'  # SHORT_SPREAD亏损止损

        # 正常出场: Z-score回归到均值附近
        if abs(zscore) < self.exit_threshold:
            return 'CLOSE'

        return 'HOLD'


    def get_open_intent(self, amount_allocated: float, data):
        """
        生成开仓意图（意图生成与执行分离）

        设计理念:
        - 内部调用get_signal()自动检测开仓信号
        - 返回OpenIntent对象,交给OrderExecutor执行
        - 如果无开仓信号或数据不足,返回None

        执行流程:
        1. 调用get_signal()检测信号类型
        2. 如果不是LONG_SPREAD或SHORT_SPREAD,返回None
        3. 计算目标市值(调用get_leg_values)
        4. 获取当前价格
        5. 计算目标数量(整数股)
        6. 构建OpenIntent对象并返回

        Args:
            amount_allocated: 分配的资金金额
            data: 数据切片,用于获取价格和计算信号

        Returns:
            OpenIntent对象 或 None(无开仓信号或数据不足)

        使用示例(在ExecutionManager中):
            intent = pair.get_open_intent(amount_allocated, data)
            if intent:
                tickets = order_executor.execute_open(intent)
                tickets_manager.register_tickets(pair.pair_id, tickets, OrderAction.OPEN)
        """
        # 自动检测信号
        signal = self.get_signal(data)

        if signal not in ['LONG_SPREAD', 'SHORT_SPREAD']:
            return None  # 无开仓信号

        # 计算目标市值
        value1, value2 = self.get_leg_values(amount_allocated, signal, data)
        if value1 is None or value2 is None:
            return None  # 市值计算失败

        # 获取价格
        prices = self.get_price_from_bar(data)
        if prices is None:
            return None  # 价格获取失败
        price1, price2 = prices

        # 计算数量
        if signal == 'LONG_SPREAD':
            # 做多spread = 买入symbol1,卖出symbol2
            qty1 = int(value1 / price1)
            qty2 = -int(value2 / price2)
        else:  # SHORT_SPREAD
            # 做空spread = 卖出symbol1,买入symbol2
            qty1 = -int(value1 / price1)
            qty2 = int(value2 / price2)

        # 检查数量有效性
        if qty1 == 0 or qty2 == 0:
            return None  # 数量为0,无法开仓

        # 三阶段Z-score记录时机见类属性注释(__init__:77-81)

        # 构建意图对象
        return OpenIntent(
            pair_id=self.pair_id,
            symbol1=self.symbol1,
            symbol2=self.symbol2,
            qty1=qty1,
            qty2=qty2,
            signal=signal,
            tag=self.create_order_tag('OPEN')
        )


    def get_close_intent(self, reason='CLOSE'):
        """
        生成平仓意图（意图生成与执行分离）

        设计理念:
        - 直接访问tracked_qty获取持仓数量 (v7.40.9优化: 避免字典创建开销)
        - 返回CloseIntent对象,交给OrderExecutor执行
        - 如果无持仓,返回None

        Args:
            reason: 平仓原因 (参见 config.constants['close_reasons'], 默认='CLOSE')

        Returns:
            CloseIntent对象 或 None(无持仓)

        使用示例(在ExecutionManager中):
            intent = pair.get_close_intent(reason='STOP_LOSS')
            if intent:
                tickets = order_executor.execute_close(intent)
                tickets_manager.register_tickets(pair.pair_id, tickets, OrderAction.CLOSE

        设计说明:
            - reason参数会编码到tag中(便于日志追踪和统计分析)
            - 支持单边持仓(qty1或qty2为0时,executor会自动跳过)
            - v7.40.9: 改用直接属性访问,与on_position_filled()风格统一
        """
        # 直接访问tracked_qty (v7.40.9: 无需字典查询)
        if self.tracked_qty1 == 0 and self.tracked_qty2 == 0:
            return None  # 无持仓

        # 构建意图对象
        return CloseIntent(
            pair_id=self.pair_id,
            symbol1=self.symbol1,
            symbol2=self.symbol2,
            qty1=self.tracked_qty1,  # 直接访问实例属性
            qty2=self.tracked_qty2,  # 直接访问实例属性
            reason=reason,
            tag=self.create_order_tag('CLOSE', reason)
        )


    def create_order_tag(self, action: str, reason: str = None):
        """
        创建标准化的订单Tag (v7.48.0: 移除timestamp)

        Args:
            action: OrderAction.OPEN 或 OrderAction.CLOSE
            reason: 平仓原因 (仅用于 CLOSE 动作)
                   可选值: 'CLOSE', 'STOP_LOSS', 'TIMEOUT', 'RISK_TRIGGER'

        返回格式:
            OPEN:  "('AAPL', 'MSFT')_OPEN"
            CLOSE: "('AAPL', 'MSFT')_CLOSE_STOP_LOSS"

        注意: 订单追踪通过TicketsManager的OrderId→PairId映射实现,
              Tag仅作为人类可读标识符,无需timestamp区分
        """
        if action == 'CLOSE' and reason:
            return f"{self.pair_id}_{action}_{reason}"
        else:
            return f"{self.pair_id}_{action}"


    # ===== 6. 生命周期回调 (Lifecycle Callbacks) =====
    # 特征: 由外部系统调用的回调方法, 处理状态更新

    def on_position_filled(self, action: str, fill_time, tickets, reason: str = None):
        """
        生命周期回调 - 订单成交后的状态更新 (由TicketsManager外部触发)

        触发时机: TicketsManager检测到配对的所有订单都已Filled时

        职责:
        - OPEN: 记录开仓价格、数量、fill_zscore_open
        - CLOSE: 记录平仓价格、平仓原因、fill_zscore_close、更新交易统计

        Args:
            action: OrderAction.OPEN 或 OrderAction.CLOSE
            fill_time: 最后一条腿成交的时间(确保两腿都已成交)
            tickets: List[OrderTicket] 成交的订单票据列表,用于提取实际成交数量
            reason: 平仓原因 (仅CLOSE时有效, 参见 config.constants['close_reasons'])

        技术说明:
            - OrderTicket: QuantConnect SDK 订单票据类
            - OrderStatus: QuantConnect SDK 订单状态枚举 (来自 AlgorithmImports)
              包括: Filled, Canceled, Invalid, PartiallyFilled 等
            - ticket.Status: 订单当前状态 (OrderStatus 枚举值)
            - ticket.QuantityFilled: 实际成交数量
            - ticket.AverageFillPrice: 平均成交价格
        """
        if action == 'OPEN':
            self.pair_opened_time = fill_time

            # 提取成交价格(用于计算fill_zscore)
            fill_price1 = None
            fill_price2 = None

            # 从OrderTicket提取实际成交数量和均价
            for ticket in tickets:
                if ticket is not None and ticket.Status == OrderStatus.Filled:
                    if ticket.Symbol == self.symbol1:
                        self.tracked_qty1 = ticket.QuantityFilled
                        self.entry_price1 = ticket.AverageFillPrice
                        fill_price1 = ticket.AverageFillPrice  # 用于计算fill_zscore
                    elif ticket.Symbol == self.symbol2:
                        self.tracked_qty2 = ticket.QuantityFilled
                        self.entry_price2 = ticket.AverageFillPrice
                        fill_price2 = ticket.AverageFillPrice  # 用于计算fill_zscore

            # 计算开仓成交时的Z-score(用于滑点分析)
            if fill_price1 and fill_price2:
                self.fill_zscore_open = self.get_zscore(fill_price1, fill_price2)

        elif action == 'CLOSE':
            self.pair_closed_time = fill_time
            self.last_close_reason = reason  # 存储平仓原因(用于动态冷却期判断)

            # 提取成交价格(用于计算fill_zscore)
            fill_price1 = None
            fill_price2 = None

            # 记录平仓价格(用于后续PnL计算)
            for ticket in tickets:
                if ticket is not None and ticket.Status == OrderStatus.Filled:
                    if ticket.Symbol == self.symbol1:
                        self.exit_price1 = ticket.AverageFillPrice
                        fill_price1 = ticket.AverageFillPrice  # 用于计算fill_zscore
                    elif ticket.Symbol == self.symbol2:
                        self.exit_price2 = ticket.AverageFillPrice
                        fill_price2 = ticket.AverageFillPrice  # 用于计算fill_zscore

            # 计算平仓成交时的Z-score(用于滑点分析)
            if fill_price1 and fill_price2:
                self.fill_zscore_close = self.get_zscore(fill_price1, fill_price2)

            # 更新交易历史统计(先更新状态)
            self._update_trade_stats()

            # 输出平仓日志(读取已更新的realized_pnl/cost)
            self._log_close_completion(reason)

            # 清零所有追踪变量
            self.tracked_qty1 = 0
            self.tracked_qty2 = 0
            self.entry_price1 = None
            self.entry_price2 = None
            self.exit_price1 = None
            self.exit_price2 = None


    def _update_trade_stats(self):
        """
        更新交易历史统计 (加权平均累计) - v7.40.6 修复

        在平仓时调用，使用平仓价格计算已实现PnL

        关键修复:
        - 使用 exit_price1/exit_price2 计算 PnL（而非实时价格）
        - 调用时机：exit_price 已记录，tracked_qty 尚未清零

        计算逻辑:
        - 本次交易PnL = (平仓市值 - 开仓成本)
        - 平仓市值 = qty1×exit_price1 + qty2×exit_price2
        - 开仓成本 = qty1×entry_price1 + qty2×entry_price2
        - 累计PnL += 本次PnL (加权平均分子)
        - 累计成本 += 本次成本 (加权平均分母)

        调用时机:
        on_position_filled(CLOSE) 中，在清零追踪变量之前调用
        此时 exit_price 已记录，tracked_qty 尚未清零
        """
        # === 步骤1：数据完整性检查 ===
        if self.entry_price1 is None or self.entry_price2 is None:
            self.algorithm.Debug(f"[统计错误] {self.pair_id} 缺少开仓价格", 1)
            return

        if self.exit_price1 is None or self.exit_price2 is None:
            self.algorithm.Debug(f"[统计错误] {self.pair_id} 缺少平仓价格", 1)
            return

        # === 步骤2：使用平仓价格计算已实现PnL ===
        # 平仓市值（考虑方向：多头为正，空头为负）
        exit_value = (self.tracked_qty1 * self.exit_price1 +
                      self.tracked_qty2 * self.exit_price2)

        # 开仓成本
        entry_value = (self.tracked_qty1 * self.entry_price1 +
                       self.tracked_qty2 * self.entry_price2)

        # 已实现PnL = 平仓市值 - 开仓成本
        pnl = exit_value - entry_value

        # === 步骤3：计算投入资本（与旧代码一致）===
        invested_capital = self.get_pair_current_invested_capital()  # 投入资本（开仓时固定）

        if invested_capital is None or invested_capital <= 0:
            self.algorithm.Debug(f"[统计错误] {self.pair_id} 投入资本异常: {invested_capital}", 1)
            return

        # === 步骤4：累加到历史统计 ===
        self.pair_realized_pnl += pnl   # 分子：已实现PnL（使用平仓价格）
        self.pair_past_invested_capital += invested_capital  # 分母：已平仓累计投入资本

        # === 步骤5：更新计数统计 ===
        self.trade_count += 1
        if pnl > 0:
            self.win_count += 1


    def _log_close_completion(self, reason: str):
        """
        输出平仓完成日志

        调用时机: on_position_filled(CLOSE) 中，在 fill_zscore_close 计算完成后

        职责:
        - 计算本次交易PnL和累计收益率
        - 格式化日志输出(包含Z-score轨迹)
        - 根据平仓原因输出不同消息("Z-score回归" vs "Z-score超限")

        Args:
            reason: 平仓原因 (v7.12.0统一: NORMAL_EXIT/DRAWDOWN/ANOMALY/PORTFOLIO_DRAWDOWN/ACCOUNT_BLOWUP)
        """
        # 计算本次交易PnL
        current_pnl = self.get_pair_unrealized_pnl()
        current_invested = self.get_pair_current_invested_capital()
        current_pnl_pct = (current_pnl / current_invested * 100) if (current_pnl and current_invested and current_invested > 0) else 0

        # 计算累计收益率 (直接读取已更新的pair_realized_pnl/pair_past_invested_capital)
        total_pnl_pct = (self.pair_realized_pnl / self.pair_past_invested_capital * 100) if self.pair_past_invested_capital > 0 else 0

        # 交易序号(此时 trade_count 已在 _update_trade_stats 中递增)
        trade_num = self.trade_count

        # 提取Z-score数据
        entry_z = self.entry_zscore if self.entry_zscore is not None else 0.0
        close_z = self.fill_zscore_close if self.fill_zscore_close is not None else 0.0

        # 从config.constants动态读取显示文本
        close_reasons = self.algorithm.config.constants['close_reasons']
        reason_text = close_reasons.get(reason, {}).get('display', '未知原因')

        # v7.44.0: 调用PairsManager统一配置查询
        cooldown_days = self.algorithm.pairs_manager.get_cooldown_required_days(reason)

        # 计算持有天数
        holding_days = self.get_pair_holding_days()

        # v7.38.2: 计算理论最大持仓天数
        max_days = self.get_max_holding_days()
        max_days_str = f"{max_days:.0f}" if max_days is not None else "N/A"

        # v7.37.1: 获取行业名称用于日志输出
        industry_names = self.algorithm.config.constants['industry_names']
        industry_name = industry_names.get(int(self.industry_code), '未知') if self.industry_code else '未知'

        self.algorithm.Debug(
            f"[平仓] {self.pair_id} | {industry_name} | {reason_text} | 持有{holding_days}天,最大{max_days_str}天 | "
            f"PnL=${current_pnl:.2f} ({current_pnl_pct:+.1f}%) | "
            f"累计{total_pnl_pct:+.1f}% | "
            f"{entry_z:+.2f}σ → {close_z:+.2f}σ | "
            f"第{trade_num}次交易 | "
            f"冷却{cooldown_days}天",
            level=0
        )

        # v7.28.2: 增强诊断 - 价格变化明细
        if self.exit_price1 and self.exit_price2 and self.entry_price1 and self.entry_price2:
            leg1_pnl = self.tracked_qty1 * (self.exit_price1 - self.entry_price1)
            leg2_pnl = self.tracked_qty2 * (self.exit_price2 - self.entry_price2)

            self.algorithm.Debug(
                f"[PnL明细] {self.pair_id} "
                f"| 开仓价=({self.entry_price1:.4f}, {self.entry_price2:.4f}) "
                f"| 平仓价=({self.exit_price1:.4f}, {self.exit_price2:.4f}) "
                f"| 数量=({self.tracked_qty1:+.0f}, {self.tracked_qty2:+.0f}) "
                f"| leg1_pnl=${leg1_pnl:+.2f} "
                f"| leg2_pnl=${leg2_pnl:+.2f}",
                level=1
            )

            # v7.39.0: 对冲漂移诊断 - 分析亏损原因(Alpha风险 vs Beta风险)
            # v7.40.1: 简化后此功能不再输出(平仓后exposure=None)
            hedge_drift = self.get_hedge_drift()
            if hedge_drift is not None:
                self.algorithm.Debug(
                    f"[对冲诊断] {self.pair_id} | 平仓时漂移={hedge_drift:+.2f}%",
                    level=1
                )
