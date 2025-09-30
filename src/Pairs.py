# region imports
from AlgorithmImports import *
import numpy as np
from typing import Dict, Optional, Tuple
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
        self.sector = model_data['sector']

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
        self.margin_buffer = config['margin_safety_buffer']

        # === 历史追踪 ===
        self.creation_time = algorithm.Time                                    # 首次创建时间
        self.reactivation_count = 0                                            # 重新激活次数(配对消失又出现)


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
        self.algorithm.Debug(f"[Pairs] {self.pair_id} 重新激活,第{self.reactivation_count}次", 2)


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
        """
        if self.symbol1 in data and self.symbol2 in data:
            price1 = data[self.symbol1].Close
            price2 = data[self.symbol2].Close
            return (price1, price2)
        return None


    # ===== 3. 持仓查询 =====

    def get_position_info(self) -> Dict:
        """
        获取完整的持仓信息(一次获取,避免重复查询)
        返回所有持仓相关信息
        """
        portfolio = self.algorithm.Portfolio

        # 一次性获取所有需要的数据
        qty1 = portfolio[self.symbol1].Quantity
        qty2 = portfolio[self.symbol2].Quantity
        value1 = abs(portfolio[self.symbol1].HoldingsValue) if qty1 != 0 else 0
        value2 = abs(portfolio[self.symbol2].HoldingsValue) if qty2 != 0 else 0

        # 统一判断持仓模式(整合状态+方向)
        if qty1 == 0 and qty2 == 0:
            position_mode = PositionMode.NONE
        elif qty1 > 0 and qty2 < 0:
            position_mode = PositionMode.LONG_SPREAD
        elif qty1 < 0 and qty2 > 0:
            position_mode = PositionMode.SHORT_SPREAD
        elif qty1 != 0 and qty2 == 0:
            position_mode = PositionMode.PARTIAL_LEG1
            self.algorithm.Debug(f"[Pairs.WARNING] {self.pair_id} 单边持仓LEG1: qty1={qty1:+.0f}", 1)
        elif qty1 == 0 and qty2 != 0:
            position_mode = PositionMode.PARTIAL_LEG2
            self.algorithm.Debug(f"[Pairs.WARNING] {self.pair_id} 单边持仓LEG2: qty2={qty2:+.0f}", 1)
        else:  # 同向持仓
            position_mode = PositionMode.ANOMALY_SAME
            self.algorithm.Debug(
                f"[Pairs.WARNING] {self.pair_id} 同向交易异常! "
                f"qty1={qty1:+.0f}, qty2={qty2:+.0f}", 1
            )

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

    def open_position(self, signal: str, value1: float, value2: float, data):
        """
        开仓该配对
        纯执行方法,不做任何检查

        参数:
            signal: "LONG_SPREAD" 或 "SHORT_SPREAD"
            value1: symbol1 的目标市值
            value2: symbol2 的目标市值
            data: 数据切片,用于获取最新价格
        """
        # 获取当前价格
        prices = self.get_price(data)
        if prices is None:
            self.algorithm.Debug(f"[Pairs.open] {self.pair_id} 无法获取价格,跳过开仓", 1)
            return
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
            self.algorithm.Debug(f"[Pairs.open] {self.pair_id} 计算数量为0,跳过开仓", 1)
            return

        # 创建订单Tag
        tag = self.create_order_tag(OrderAction.OPEN)

        # 执行下单
        self.algorithm.MarketOrder(self.symbol1, qty1, tag=tag)
        self.algorithm.MarketOrder(self.symbol2, qty2, tag=tag)

        # 记录开仓信息
        self.algorithm.Debug(
            f"[Pairs.open] {self.pair_id} {signal} "
            f"市值:({value1:.0f}/{value2:.0f}) "
            f"数量:({qty1:+d}/{qty2:+d}) "
            f"Beta:{self.beta_mean:.3f}", 1
        )


    def close_position(self):
        """
        平仓该配对的所有持仓
        纯执行方法,不做任何检查
        """
        # 获取当前持仓信息
        info = self.get_position_info()
        qty1 = info['qty1']
        qty2 = info['qty2']

        # 如果没有持仓,直接返回
        if qty1 == 0 and qty2 == 0:
            self.algorithm.Debug(f"[Pairs.close] {self.pair_id} 无持仓,跳过平仓", 1)
            return

        # 创建平仓Tag
        tag = self.create_order_tag(OrderAction.CLOSE)

        # 使用MarketOrder平仓(支持tag参数)
        # Liquidate不支持tag参数,必须用MarketOrder
        if qty1 != 0:
            self.algorithm.MarketOrder(self.symbol1, -qty1, tag=tag)
        if qty2 != 0:
            self.algorithm.MarketOrder(self.symbol2, -qty2, tag=tag)

        # 记录平仓信息
        self.algorithm.Debug(
            f"[Pairs.close] {self.pair_id} 执行平仓 "
            f"持仓:({qty1:.0f}/{qty2:.0f})", 1
        )


    def adjust_position(self, target_ratio: float) -> bool:
        """
        调整持仓到目标比例
        target_ratio: 目标持仓比例 (1.0=保持不变, 0.5=减半, 2.0=翻倍)
        """
        # 参数验证
        if target_ratio <= 0:
            self.algorithm.Debug(f"[Pairs.adjust] {self.pair_id} 无效的目标比例: {target_ratio}", 1)
            return False

        info = self.get_position_info()

        # 只有正常持仓才能调整
        if info['position_mode'] not in [PositionMode.LONG_SPREAD, PositionMode.SHORT_SPREAD]:
            return False

        # 计算调整数量 (target_ratio - 1 表示变化比例)
        # 正数表示加仓,负数表示减仓
        adjust_qty1 = int(info['qty1'] * (target_ratio - 1))
        adjust_qty2 = int(info['qty2'] * (target_ratio - 1))

        # 执行调整
        if adjust_qty1 != 0:
            self.algorithm.MarketOrder(self.symbol1, adjust_qty1)
        if adjust_qty2 != 0:
            self.algorithm.MarketOrder(self.symbol2, adjust_qty2)

        # 改进的Debug信息
        if target_ratio < 1:
            action = f"减仓{(1-target_ratio)*100:.1f}%"
        elif target_ratio > 1:
            action = f"加仓{(target_ratio-1)*100:.1f}%"
        else:
            action = "保持不变"

        # 获取方向描述
        direction_desc = "做多价差" if info['position_mode'] == PositionMode.LONG_SPREAD else "做空价差"
        self.algorithm.Debug(f"[Pairs.adjust] {self.pair_id} {direction_desc} {action}", 1)

        return True


    # ===== 5. 保证金计算 (新架构) =====

    def calculate_values_from_margin(self, margin_allocated: float, signal: str):
        """
        从保证金占用反推AB两腿的市值

        参数:
            margin_allocated: 分配的保证金金额
            signal: 交易信号 (LONG_SPREAD/SHORT_SPREAD)

        返回:
            (value_A, value_B): A和B的市值
        """
        beta = abs(self.beta_mean) if abs(self.beta_mean) != 0 else 1

        if signal == TradingSignal.LONG_SPREAD:
            # A做多(margin_long), B做空(margin_short)
            # value_A × margin_long + value_B × margin_short = margin
            # beta × value_B × margin_long + value_B × margin_short = margin
            value_B = margin_allocated / (self.margin_long * beta + self.margin_short)
            value_A = beta * value_B
        else:  # SHORT_SPREAD
            # A做空(margin_short), B做多(margin_long)
            # value_A × margin_short + value_B × margin_long = margin
            # beta × value_B × margin_short + value_B × margin_long = margin
            value_B = margin_allocated / (self.margin_short * beta + self.margin_long)
            value_A = beta * value_B

        return value_A, value_B


    # ===== 6. 风控检查 =====

    def check_position_integrity(self):
        """
        检查持仓完整性

        返回:
            (is_valid: bool, error_msg: str)
        """
        pos1 = self.algorithm.Portfolio[self.symbol1].Quantity
        pos2 = self.algorithm.Portfolio[self.symbol2].Quantity

        # 无持仓,正常
        if pos1 == 0 and pos2 == 0:
            return True, ""

        # 单边持仓
        if (pos1 != 0 and pos2 == 0) or (pos1 == 0 and pos2 != 0):
            return False, f"单边持仓: {self.symbol1}={pos1}, {self.symbol2}={pos2}"

        # 同方向持仓 (应该一多一空)
        if pos1 * pos2 > 0:
            return False, f"持仓方向错误: {self.symbol1}={pos1}, {self.symbol2}={pos2}"

        # 检查持仓比例
        expected_ratio = abs(self.beta_mean)
        actual_ratio = abs(pos2 / pos1) if pos1 != 0 else 0
        ratio_error = abs(actual_ratio - expected_ratio) / expected_ratio

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

        entry_time = self.get_entry_time()
        if entry_time is not None:
            return (self.algorithm.UtcTime - entry_time).days

        return None  # 无法获取入场时间


    def is_in_cooldown(self):
        """
        检查是否在冷却期内
        返回: True(在冷却期) / False(可以交易)
        """
        # 获取最近的退出时间
        exit_time = self.get_exit_time()

        # 如果没有退出时间,不在冷却期
        if exit_time is None:
            return False

        # 计算距离上次退出的时间
        days_since_exit = (self.algorithm.UtcTime - exit_time).days

        # 判断是否在冷却期(使用config中的冷却天数)
        return days_since_exit < self.cooldown_days


    # ===== 7. 时间追踪 =====

    def get_entry_time(self):
        """获取最近的入场时间"""
        return self._get_order_time(OrderAction.OPEN)


    def get_exit_time(self):
        """获取最近的退出时间"""
        return self._get_order_time(OrderAction.CLOSE)


    def _get_order_time(self, action_type: str):
        """
        内部方法:获取指定类型的订单时间
        action_type: OrderAction.OPEN 或 OrderAction.CLOSE
        """
        # 查询指定类型的订单
        orders = self.algorithm.Transactions.GetOrders(
            lambda x: str(self.pair_id) in x.Tag
                     and action_type in x.Tag
                     and x.Status == OrderStatus.Filled
        )

        if not orders:
            return None

        # 转换为Python列表后再排序
        orders = list(orders)
        orders.sort(key=lambda x: x.Time, reverse=True)

        # 确保两腿都成交,取较晚的时间
        latest_pair_time = None

        # 步骤1: 按日期分组订单,提高查找效率
        orders_by_date = {}
        for order in orders:
            # 从Tag中提取日期部分: "pair_id_ACTION_20250101" -> "20250101"
            date = order.Tag.split('_')[-1]
            if date not in orders_by_date:
                orders_by_date[date] = []

            # orders_by_date = {'20250101': [order1, order2, order3],  # 该日期的所有订单对象列表
                               #'20250102': ......
            orders_by_date[date].append(order)

        # 步骤2: 按日期倒序处理(最新日期优先)
        for date in sorted(orders_by_date.keys(), reverse=True):
            # 获取该日期的所有订单
            same_date_orders = orders_by_date[date]

            # 检查该日期是否同时有两个symbol的订单
            has_symbol1 = any(o.Symbol == self.symbol1 for o in same_date_orders)
            has_symbol2 = any(o.Symbol == self.symbol2 for o in same_date_orders)

            if has_symbol1 and has_symbol2:
                # 两腿都有订单,找到这组订单的最晚时间
                latest_pair_time = max(o.Time for o in same_date_orders)
                break

        return latest_pair_time


    # ===== 8. 辅助方法 =====

    def create_order_tag(self, action: str):
        """
        创建标准化的订单Tag
        action: OrderAction.OPEN 或 OrderAction.CLOSE
        返回格式: "('AAPL', 'MSFT')_OPEN_20240101"
        """
        return f"{self.pair_id}_{action}_{self.algorithm.Time.strftime('%Y%m%d')}"


    def get_quality_score(self) -> float:
        """获取配对的质量分数"""
        return self.quality_score


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


    def get_sector(self) -> str:
        """获取配对所属行业"""
        return self.sector