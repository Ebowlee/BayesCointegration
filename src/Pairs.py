# region imports
from AlgorithmImports import *
import numpy as np
from typing import Dict, Optional, Tuple
from src.TradeHistory import TradeSnapshot
# endregion


# ===== 常量定义 =====
class TradingSignal:
    """交易信号常量"""
    LONG_SPREAD = 'LONG_SPREAD'     # 做多价差(买入symbol1,卖出symbol2)
    SHORT_SPREAD = 'SHORT_SPREAD'   # 做空价差(卖出symbol1,买入symbol2)
    CLOSE = 'CLOSE'                 # 平仓信号
    STOP_LOSS = 'STOP_LOSS'         # 止损信号
    HOLD = 'HOLD'                   # 持有信号
    WAIT = 'WAIT'                   # 等待信号
    COOLDOWN = 'COOLDOWN'           # 冷却期
    NO_DATA = 'NO_DATA'             # 无数据

class PositionMode:
    """持仓模式常量(整合状态+方向)"""
    NONE = 'NONE'                    # 无持仓
    LONG_SPREAD = 'LONG_SPREAD'      # 正常做多价差 (qty1>0, qty2<0)
    SHORT_SPREAD = 'SHORT_SPREAD'    # 正常做空价差 (qty1<0, qty2>0)
    PARTIAL_LEG1 = 'PARTIAL_LEG1'    # 只有第一腿
    PARTIAL_LEG2 = 'PARTIAL_LEG2'    # 只有第二腿
    ANOMALY_SAME = 'ANOMALY_SAME'    # 异常:同向持仓

class OrderAction:
    """订单动作常量"""
    OPEN = 'OPEN'
    CLOSE = 'CLOSE'


class Pairs:
    """配对交易的核心数据对象"""

    # ===== 1. 初始化与参数管理 =====

    def __init__(self, algorithm, model_data, config):
        """
        从贝叶斯建模结果初始化
        model_data包含配对的统计参数和基础信息
        """
        # === 算法引用 ===
        self.algorithm = algorithm
        self.config = config  # 保存配置以供后续方法使用

        # === 基础信息 ===
        self.symbol1 = model_data['symbol1']
        self.symbol2 = model_data['symbol2']
        self.pair_id = (self.symbol1.Value, self.symbol2.Value)
        self.industry_group = model_data['industry_group']

        # === 统计参数(从贝叶斯建模获得) ===
        self.alpha_mean = model_data['alpha_mean']                              # 截距(对数空间)
        self.beta_mean = model_data['beta_mean']                                # 斜率(对数空间)
        self.residual_mean = model_data['residual_mean']                        # 残差均值(对数空间,理论上接近0)
        self.residual_std = model_data['residual_std']                          # 残差标准差(对数空间)
        self.sigma_mean = model_data.get('sigma_mean', self.residual_std)       # 模型误差项标准差
        self.sigma_std = model_data.get('sigma_std', 0)                         # sigma的不确定性
        self.quality_score = model_data['quality_score']

        # === 交易阈值 ===
        self.entry_threshold = config['entry_threshold']
        self.exit_threshold = config['exit_threshold']
        self.stop_threshold = config['stop_threshold']

        # === 控制设置 ===
        self.cooldown_days = config['pair_cooldown_days']
        self.max_holding_days = config['max_holding_days']

        # === 保证金参数 ===
        self.margin_long = config['margin_requirement_long']
        self.margin_short = config['margin_requirement_short']

        # === 历史追踪 ===
        self.creation_time = algorithm.Time                                    # 首次创建时间
        self.reactivation_count = 0                                            # 重新激活次数(配对消失又出现)

        # === 时间追踪 ===
        self.position_opened_time = None                                       # 开仓时间(双腿都成交的时刻)
        self.position_closed_time = None                                       # 平仓时间(双腿都成交的时刻)

        # === 持仓追踪(OrderTicket-based,避免Portfolio全局查询混淆) ===
        self.tracked_qty1 = 0                                                  # 配对专属持仓追踪(symbol1)
        self.tracked_qty2 = 0                                                  # 配对专属持仓追踪(symbol2)

        # === 成本追踪(配对专属PnL计算基础) ===
        self.entry_price1 = None                                               # symbol1开仓均价
        self.entry_price2 = None                                               # symbol2开仓均价
        self.entry_cost = 0.0                                                  # 配对总成本(abs(qty1*price1)+abs(qty2*price2))

        # === 风控追踪 ===
        self.pair_hwm = 0.0                                                    # 配对级别高水位(浮动盈亏PnL)


    def update_params(self, new_pair):
        """
        从新的Pairs对象更新统计参数(当配对重新出现时调用)
        只更新模型参数,不更新身份信息和配置
        """
        # 只更新贝叶斯模型参数
        self.alpha_mean = new_pair.alpha_mean
        self.beta_mean = new_pair.beta_mean
        self.residual_mean = new_pair.residual_mean
        self.residual_std = new_pair.residual_std
        self.sigma_mean = new_pair.sigma_mean
        self.sigma_std = new_pair.sigma_std
        self.quality_score = new_pair.quality_score

        # 记录重新激活
        self.reactivation_count += 1


    def on_position_filled(self, action: str, fill_time, tickets):
        """
        订单成交回调(由TicketsManager调用)

        触发时机: TicketsManager检测到配对的所有订单都已Filled时

        Args:
            action: OrderAction.OPEN 或 OrderAction.CLOSE
            fill_time: 最后一条腿成交的时间(确保两腿都已成交)
            tickets: List[OrderTicket] 成交的订单票据列表,用于提取实际成交数量
        """
        if action == OrderAction.OPEN:
            self.position_opened_time = fill_time

            # 从OrderTicket提取实际成交数量和均价
            for ticket in tickets:
                if ticket is not None and ticket.Status == OrderStatus.Filled:
                    if ticket.Symbol == self.symbol1:
                        self.tracked_qty1 = ticket.QuantityFilled
                        self.entry_price1 = ticket.AverageFillPrice
                    elif ticket.Symbol == self.symbol2:
                        self.tracked_qty2 = ticket.QuantityFilled
                        self.entry_price2 = ticket.AverageFillPrice

            # 计算配对总成本(用于回撤计算分母)
            if self.entry_price1 is not None and self.entry_price2 is not None:
                cost1 = abs(self.tracked_qty1 * self.entry_price1)
                cost2 = abs(self.tracked_qty2 * self.entry_price2)
                self.entry_cost = cost1 + cost2
            else:
                self.entry_cost = 0.0

            # 开仓时重置HWM为0(起点为盈亏平衡)
            self.pair_hwm = 0.0

        elif action == OrderAction.CLOSE:
            self.position_closed_time = fill_time

            # === 在清零之前捕获交易快照（传给TradeJournal） ===
            # 注意：必须在清零之前捕获，因为清零后数据丢失
            if hasattr(self.algorithm, 'trade_journal'):
                # 提取平仓价格和盈亏
                exit_price1 = None
                exit_price2 = None
                for ticket in tickets:
                    if ticket is not None and ticket.Status == OrderStatus.Filled:
                        if ticket.Symbol == self.symbol1:
                            exit_price1 = ticket.AverageFillPrice
                        elif ticket.Symbol == self.symbol2:
                            exit_price2 = ticket.AverageFillPrice

                # 计算最终盈亏
                pnl = self.get_pair_pnl() if self.has_normal_position() else 0.0

                # 从订单Tag中解析平仓原因
                # Tag格式: "('AAPL', 'MSFT')_CLOSE_STOP_LOSS_20240101_093000"
                # 或旧格式: "('AAPL', 'MSFT')_CLOSE_20240101_093000"
                close_reason = 'CLOSE'  # 默认值
                if tickets and tickets[0] is not None:
                    tag = tickets[0].Tag
                    # 解析Tag: pair_id_CLOSE_reason_timestamp
                    parts = tag.split('_')
                    if len(parts) >= 4 and parts[2] == 'CLOSE':
                        # 新格式: 包含reason
                        # parts[0-1]: pair_id, parts[2]: 'CLOSE', parts[3]: reason, parts[4-5]: timestamp
                        potential_reason = parts[3]
                        # 验证是否为有效的平仓原因
                        valid_reasons = ['CLOSE', 'STOP_LOSS', 'TIMEOUT', 'RISK_TRIGGER']
                        if potential_reason in valid_reasons:
                            close_reason = potential_reason
                    # 否则使用默认值 'CLOSE'

                # 创建快照并记录
                if exit_price1 is not None and exit_price2 is not None:
                    snapshot = TradeSnapshot.from_pair(
                        pair=self,
                        close_reason=close_reason,
                        exit_price1=exit_price1,
                        exit_price2=exit_price2,
                        pnl=pnl if pnl is not None else 0.0
                    )
                    self.algorithm.trade_journal.record(snapshot)

            # 平仓后清零所有追踪变量
            self.tracked_qty1 = 0
            self.tracked_qty2 = 0
            self.entry_price1 = None
            self.entry_price2 = None
            self.entry_cost = 0.0
            self.pair_hwm = 0.0


    # ===== 2. 交易信号生成 =====

    def get_signal(self, data):
        """
        获取交易信号
        一步到位的接口,内部自动计算所需信息
        """
        # 先检查冷却期(仅在无持仓时检查)
        if not self.has_position() and self.is_in_cooldown():
            return TradingSignal.COOLDOWN

        # 内部计算zscore
        zscore = self.get_zscore(data)
        if zscore is None:
            return TradingSignal.NO_DATA

        # 内部检查持仓
        has_position = self.has_normal_position()

        # 生成信号
        if not has_position:
            # Z-score高,spread偏高,做空
            if zscore > self.entry_threshold:
                return TradingSignal.SHORT_SPREAD

            # Z-score低,spread偏低,做多
            elif zscore < -self.entry_threshold:
                return TradingSignal.LONG_SPREAD
            else:
                return TradingSignal.WAIT
        else:
            # 有持仓时的出场信号
            if abs(zscore) > self.stop_threshold:
                return TradingSignal.STOP_LOSS

            if abs(zscore) < self.exit_threshold:
                return TradingSignal.CLOSE

            return TradingSignal.HOLD


    def get_zscore(self, data) -> Optional[float]:
        """
        计算Z-score,包含spread计算
        需要传入data来获取最新价格,返回Z-score值或None
        使用对数价格计算: log(price1) = alpha + beta * log(price2) + residual
        """
        # 获取价格
        prices = self.get_price(data)
        if prices is None:
            return None

        price1, price2 = prices

        # 计算对数空间的残差(与贝叶斯模型一致)
        log_residual = np.log(price1) - (self.alpha_mean + self.beta_mean * np.log(price2))

        # 计算Z-score
        if self.residual_std > 0:
            zscore = (log_residual - self.residual_mean) / self.residual_std
        else:
            zscore = 0

        return zscore


    def get_price(self, data):
        """
        从data slice获取最新价格
        返回: (price1, price2) 或 None

        安全检查:
        - symbol在data中存在
        - data[symbol]不为None (防止QuantConnect数据缺失)
        - data[symbol].Close有效且>0
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


    # ===== 3. 持仓查询 =====

    def get_position_info(self) -> Dict:
        """
        获取完整的持仓信息(一次获取,避免重复查询)
        使用tracked_qty避免Portfolio全局查询混淆

        返回所有持仓相关信息
        """
        portfolio = self.algorithm.Portfolio

        # 使用配对专属的tracked_qty(从OrderTicket提取的实际成交数量)
        qty1 = self.tracked_qty1
        qty2 = self.tracked_qty2

        # 市值仍需从Portfolio获取(需要当前价格)
        if qty1 != 0:
            value1 = abs(qty1 * portfolio[self.symbol1].Price)
        else:
            value1 = 0
        if qty2 != 0:
            value2 = abs(qty2 * portfolio[self.symbol2].Price)
        else:
            value2 = 0

        # 统一判断持仓模式(整合状态+方向)
        if qty1 == 0 and qty2 == 0:
            position_mode = PositionMode.NONE
        elif qty1 > 0 and qty2 < 0:
            position_mode = PositionMode.LONG_SPREAD
        elif qty1 < 0 and qty2 > 0:
            position_mode = PositionMode.SHORT_SPREAD
        elif qty1 != 0 and qty2 == 0:
            position_mode = PositionMode.PARTIAL_LEG1
            self.algorithm.Debug(f"[持仓异常] {self.pair_id} 单边持仓LEG1: qty1={qty1:+.0f}")
        elif qty1 == 0 and qty2 != 0:
            position_mode = PositionMode.PARTIAL_LEG2
            self.algorithm.Debug(f"[持仓异常] {self.pair_id} 单边持仓LEG2: qty2={qty2:+.0f}")
        else:  # 同向持仓
            position_mode = PositionMode.ANOMALY_SAME
            self.algorithm.Debug(f"[持仓异常] {self.pair_id} 同向持仓: qty1={qty1:+.0f}, qty2={qty2:+.0f}")

        return {'position_mode': position_mode, 'qty1': qty1, 'qty2': qty2, 'value1': value1, 'value2': value2}


    def has_position(self) -> bool:
        """检查是否有持仓"""
        mode = self.get_position_info()['position_mode']
        return mode != PositionMode.NONE


    def has_normal_position(self) -> bool:
        """检查是否有正常持仓"""
        mode = self.get_position_info()['position_mode']
        return mode in [PositionMode.LONG_SPREAD, PositionMode.SHORT_SPREAD]


    def has_anomaly(self) -> bool:
        """检查是否有异常持仓"""
        mode = self.get_position_info()['position_mode']
        return mode in [PositionMode.PARTIAL_LEG1, PositionMode.PARTIAL_LEG2, PositionMode.ANOMALY_SAME]


    def get_position_value(self) -> float:
        """获取当前持仓市值(包括部分持仓)"""
        info = self.get_position_info()
        return info['value1'] + info['value2']


    # ===== 4. 交易执行 =====

    def open_position(self, signal: str, margin_allocated: float, data):
        """
        开仓该配对,基于保证金分配

        参数:
            signal: "LONG_SPREAD" 或 "SHORT_SPREAD"
            margin_allocated: 分配的保证金金额
            data: 数据切片,用于获取最新价格

        返回:
            List[OrderTicket]: 订单票据列表,供TicketsManager追踪
            失败时返回None
        """
        # 计算目标市值
        value1, value2 = self.calculate_values_from_margin(margin_allocated, signal, data)

        if value1 is None or value2 is None:
            self.algorithm.Debug(f"[开仓失败] {self.pair_id} 无法计算市值")
            return None

        # 获取当前价格
        prices = self.get_price(data)
        if prices is None:
            self.algorithm.Debug(f"[开仓失败] {self.pair_id} 无法获取价格")
            return None
        price1, price2 = prices

        # 计算股数
        if signal == TradingSignal.LONG_SPREAD:
            # 做多spread = 买入symbol1,卖出symbol2
            qty1 = int(value1 / price1)
            qty2 = -int(value2 / price2)
        else:  # SHORT_SPREAD
            # 做空spread = 卖出symbol1,买入symbol2
            qty1 = -int(value1 / price1)
            qty2 = int(value2 / price2)

        # 检查计算出的数量是否有效
        if qty1 == 0 or qty2 == 0:
            self.algorithm.Debug(f"[开仓失败] {self.pair_id} 计算数量为0")
            return None

        # 创建订单Tag
        tag = self.create_order_tag(OrderAction.OPEN)

        # 执行下单并收集OrderTicket
        ticket1 = self.algorithm.MarketOrder(self.symbol1, qty1, tag=tag)
        ticket2 = self.algorithm.MarketOrder(self.symbol2, qty2, tag=tag)

        # 返回订单票据列表
        return [ticket1, ticket2]


    def close_position(self, reason='CLOSE'):
        """
        平仓该配对的所有持仓
        纯执行方法,不做任何检查

        Args:
            reason: 平仓原因 (默认 'CLOSE')
                   可选值: 'CLOSE', 'STOP_LOSS', 'TIMEOUT', 'RISK_TRIGGER'

        返回:
            List[OrderTicket]: 订单票据列表,供TicketsManager追踪
            无持仓时返回None

        设计要点:
        - reason 参数会编码到订单Tag中
        - on_position_filled() 将从Tag中解析reason并传递给TradeSnapshot
        """
        # 获取当前持仓信息
        info = self.get_position_info()
        qty1 = info['qty1']
        qty2 = info['qty2']

        # 如果没有持仓,直接返回
        if qty1 == 0 and qty2 == 0:
            return None

        # 创建平仓Tag (包含reason)
        tag = self.create_order_tag(OrderAction.CLOSE, reason)

        # 使用MarketOrder平仓(支持tag参数)
        # 收集实际提交的订单票据
        tickets = []
        if qty1 != 0:
            ticket1 = self.algorithm.MarketOrder(self.symbol1, -qty1, tag=tag)
            tickets.append(ticket1)
        if qty2 != 0:
            ticket2 = self.algorithm.MarketOrder(self.symbol2, -qty2, tag=tag)
            tickets.append(ticket2)

        # 返回订单票据列表
        return tickets if tickets else None


    # ===== 5. 保证金计算 (新架构) =====

    def calculate_values_from_margin(self, margin_allocated: float, signal: str, data):
        """
        从保证金反推AB两腿的市值,按beta数量配比

        核心公式:
        1. X + Y = margin_allocated (保证金约束)
        2. Qty_A = beta × Qty_B (数量配比约束)
           其中 Qty_A = (X/margin_rate_A)/Price_A
                Qty_B = (Y/margin_rate_B)/Price_B

        LONG_SPREAD (A做多0.5, B做空1.5):
            (X/0.5)/Price_A = beta × (Y/1.5)/Price_B
            => Y = margin × 3 × Price_B / (beta × Price_A + 3 × Price_B)

        SHORT_SPREAD (A做空1.5, B做多0.5):
            (X/1.5)/Price_A = beta × (Y/0.5)/Price_B
            => Y = margin × Price_B / (beta × 3 × Price_A + Price_B)

        参数:
            margin_allocated: 分配的保证金金额
            signal: 交易信号 (LONG_SPREAD/SHORT_SPREAD)
            data: 数据切片(用于获取当前价格)

        返回:
            (value_A, value_B): A和B的目标市值, 计算失败返回 (None, None)
        """
        # 获取当前价格
        prices = self.get_price(data)
        if prices is None:
            return None, None
        price_A, price_B = prices

        # 避免除零
        if price_A <= 0 or price_B <= 0:
            self.algorithm.Debug(f"[计算失败] {self.pair_id} 价格异常: A={price_A}, B={price_B}")
            return None, None

        beta = abs(self.beta_mean) if abs(self.beta_mean) != 0 else 1

        if signal == TradingSignal.LONG_SPREAD:
            # A做多(margin_long=0.5), B做空(margin_short=1.5)
            # Y = margin × 3 × Price_B / (beta × Price_A + 3 × Price_B)
            denominator = beta * price_A + 3 * price_B
            if denominator <= 0:
                return None, None

            Y = margin_allocated * 3 * price_B / denominator
            X = margin_allocated - Y

            # 市值 = 保证金 / 保证金率
            value_A = X / self.margin_long    # X / 0.5
            value_B = Y / self.margin_short   # Y / 1.5

        else:  # SHORT_SPREAD
            # A做空(margin_short=1.5), B做多(margin_long=0.5)
            # Y = margin × Price_B / (beta × 3 × Price_A + Price_B)
            denominator = beta * 3 * price_A + price_B
            if denominator <= 0:
                return None, None

            Y = margin_allocated * price_B / denominator
            X = margin_allocated - Y

            # 市值
            value_A = X / self.margin_short   # X / 1.5
            value_B = Y / self.margin_long    # Y / 0.5

        # 安全检查: 保证金分配合理性
        if X <= 0 or Y <= 0:
            self.algorithm.Debug(f"[计算失败] {self.pair_id} 保证金分配异常: X={X:.2f}, Y={Y:.2f}")
            return None, None

        return value_A, value_B


    # ===== 6. 风控检查 =====

    def check_position_integrity(self):
        """
        检查持仓完整性(复用get_position_info避免重复查询)

        返回:
            (is_valid: bool, error_msg: str)
        """
        # 复用现有方法获取持仓状态
        info = self.get_position_info()
        mode = info['position_mode']

        # 场景1: 无持仓 → 正常
        if mode == PositionMode.NONE:
            return True, ""

        # 场景2: 单边持仓异常
        if mode in [PositionMode.PARTIAL_LEG1, PositionMode.PARTIAL_LEG2]:
            return False, f"单边持仓: {self.symbol1}={info['qty1']}, {self.symbol2}={info['qty2']}"

        # 场景3: 同向持仓异常
        if mode == PositionMode.ANOMALY_SAME:
            return False, f"持仓方向错误: {self.symbol1}={info['qty1']}, {self.symbol2}={info['qty2']}"

        # 场景4: 正常持仓,检查比例(仅针对LONG_SPREAD/SHORT_SPREAD)
        expected_ratio = abs(self.beta_mean)
        actual_ratio = abs(info['qty2'] / info['qty1']) if info['qty1'] != 0 else 0
        ratio_error = abs(actual_ratio - expected_ratio) / expected_ratio if expected_ratio > 0 else 0

        if ratio_error > 0.20:  # 20%容差
            return False, (
                f"持仓比例异常: 期望{expected_ratio:.2f}, "
                f"实际{actual_ratio:.2f}, 误差{ratio_error:.1%}"
            )

        return True, ""


    def get_pair_holding_days(self):
        """
        获取持仓时长(天数)
        返回: 天数 或 None
        """
        if not self.has_normal_position():
            return None

        # 直接访问开仓时间属性
        entry_time = self.position_opened_time
        if entry_time is not None:
            return (self.algorithm.UtcTime - entry_time).days

        return None  # 无法获取入场时间


    def is_in_cooldown(self):
        """
        检查是否在冷却期内
        返回: True(在冷却期) / False(可以交易)
        """
        # 获取最近的退出时间(直接访问属性)
        exit_time = self.position_closed_time

        # 如果没有退出时间,不在冷却期
        if exit_time is None:
            return False

        # 计算距离上次退出的时间
        days_since_exit = (self.algorithm.UtcTime - exit_time).days

        # 判断是否在冷却期(使用config中的冷却天数)
        return days_since_exit < self.cooldown_days


    def get_pair_pnl(self) -> Optional[float]:
        """
        计算配对当前浮动盈亏(配对专属,不受其他配对干扰)

        公式:
        - 当前市值 = qty1 * current_price1 + qty2 * current_price2
        - 开仓成本 = qty1 * entry_price1 + qty2 * entry_price2
        - PnL = 当前市值 - 开仓成本

        关键设计:
        - 使用tracked_qty避免Portfolio全局查询混淆
        - 使用entry_price而非Portfolio.AveragePrice(全局均价)
        - 空头的qty为负数,自动处理方向
        - 完全配对专属计算,即使symbol出现在多个配对中也不会混淆

        返回:
            浮动盈亏(美元) 或 None(无持仓或数据不完整)
        """
        # 必须有正常持仓
        if not self.has_normal_position():
            return None

        # 检查是否有开仓价格(防御性编程)
        if self.entry_price1 is None or self.entry_price2 is None:
            return None

        # 获取当前市场价格
        portfolio = self.algorithm.Portfolio
        current_price1 = portfolio[self.symbol1].Price
        current_price2 = portfolio[self.symbol2].Price

        # 计算当前市值(考虑方向: 多头为正,空头为负)
        current_value = (self.tracked_qty1 * current_price1 +
                        self.tracked_qty2 * current_price2)

        # 计算开仓成本(考虑方向)
        entry_value = (self.tracked_qty1 * self.entry_price1 +
                      self.tracked_qty2 * self.entry_price2)

        # PnL = 当前市值 - 开仓成本
        pnl = current_value - entry_value

        return pnl


    def get_pair_drawdown(self) -> Optional[float]:
        """
        计算配对回撤比例

        回撤定义:
        - drawdown = (HWM - current_pnl) / entry_cost
        - HWM(高水位)会随着pnl上涨而更新
        - entry_cost使用开仓时的总成本作为分母(归一化)

        设计特点:
        - 自动追踪HWM(调用时自动更新)
        - 回撤基于entry_cost归一化,便于跨配对比较
        - 开仓时HWM=0,从盈亏平衡点开始追踪

        返回:
            回撤比例(0.15表示15%回撤) 或 None(无持仓或无法计算)
        """
        # 必须有正常持仓
        if not self.has_normal_position():
            return None

        # 获取当前PnL
        pnl = self.get_pair_pnl()
        if pnl is None or self.entry_cost <= 0:
            return None

        # 更新HWM(如果当前PnL更高)
        if pnl > self.pair_hwm:
            self.pair_hwm = pnl

        # 计算回撤比例
        drawdown = (self.pair_hwm - pnl) / self.entry_cost

        return drawdown


    # ===== 7. 辅助方法 =====

    def create_order_tag(self, action: str, reason: str = None):
        """
        创建标准化的订单Tag

        Args:
            action: OrderAction.OPEN 或 OrderAction.CLOSE
            reason: 平仓原因 (仅用于 CLOSE 动作)
                   可选值: 'CLOSE', 'STOP_LOSS', 'TIMEOUT', 'RISK_TRIGGER'

        返回格式:
            OPEN:  "('AAPL', 'MSFT')_OPEN_20240101_093000"
            CLOSE: "('AAPL', 'MSFT')_CLOSE_STOP_LOSS_20240101_093000"

        注意: 时间戳精确到秒,防止同一天内多次信号的Tag冲突
        """
        timestamp = self.algorithm.Time.strftime('%Y%m%d_%H%M%S')

        if action == OrderAction.CLOSE and reason:
            # 平仓时包含reason
            return f"{self.pair_id}_{action}_{reason}_{timestamp}"
        else:
            # 开仓时或没有reason时的标准格式
            return f"{self.pair_id}_{action}_{timestamp}"


    def get_planned_allocation_pct(self) -> float:
        """
        计算基于质量分数的计划分配比例
        纯计算方法,不进行任何业务逻辑判断

        Returns:
            计划分配比例 (min_position_pct 到 max_position_pct)
        """
        # 基于质量分数的线性插值计算
        min_pct = self.config['min_position_pct']
        max_pct = self.config['max_position_pct']
        return min_pct + self.quality_score * (max_pct - min_pct)