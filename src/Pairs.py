# region imports
from AlgorithmImports import *
# endregion


class Pairs:
    """配对交易的核心数据对象"""

    def __init__(self, algorithm, model_data, config):
        """
        从贝叶斯建模结果初始化
        algorithm: QuantConnect算法实例
        model_data包含配对的统计参数和基础信息
        config包含交易阈值参数
        """
        # === 算法引用 ===
        self.algorithm = algorithm

        # === 基础信息 ===
        self.symbol1 = model_data['symbol1']
        self.symbol2 = model_data['symbol2']
        self.pair_id = tuple(sorted([self.symbol1.Value, self.symbol2.Value]))
        self.sector = model_data['sector']

        # === 统计参数（从贝叶斯建模获得）===
        self.alpha_mean = model_data['alpha_mean']  # 截距
        self.beta_mean = model_data['beta_mean']    # 斜率
        self.spread_mean = model_data['spread_mean']
        self.spread_std = model_data['spread_std']
        self.quality_score = model_data['quality_score']

        # === 交易阈值 ===
        self.entry_threshold = config['entry_threshold']
        self.exit_threshold = config['exit_threshold']
        self.stop_threshold = config['stop_threshold']

        # === 冷却设置 ===
        self.cooldown_days = config['cooldown_days']  # 从config读取

        # === 风控参数 ===
        self.max_holding_days = config.get('max_holding_days', 30)  # 最大持仓天数
        self.max_concentration = config.get('max_pair_concentration', 0.25)  # 最大集中度

        # === 历史追踪 ===
        self.creation_time = algorithm.Time  # 首次创建时间
        self.reactivation_count = 0  # 重新激活次数（配对消失又出现）


    def update_params(self, model_data):
        """
        更新统计参数（当配对重新出现时调用）
        """
        # 更新统计参数
        self.alpha_mean = model_data['alpha_mean']
        self.beta_mean = model_data['beta_mean']
        self.spread_mean = model_data['spread_mean']
        self.spread_std = model_data['spread_std']
        self.quality_score = model_data['quality_score']

        # 记录重新激活
        self.reactivation_count += 1
        self.algorithm.Debug(f"[Pairs] {self.pair_id} 重新激活，第{self.reactivation_count}次")



    # ===== 1. 核心计算 =====

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


    def get_zscore(self, data):
        """
        计算Z-score，包含spread计算
        需要传入data来获取价格
        返回: Z-score值 或 None
        """
        # 获取价格
        prices = self.get_price(data)
        if prices is None:
            return None

        price1, price2 = prices

        # 计算spread
        spread = price1 - (self.beta_mean * price2 + self.alpha_mean)

        # 计算zscore
        if self.spread_std > 0:
            zscore = (spread - self.spread_mean) / self.spread_std
        else:
            zscore = 0

        return zscore



    # ===== 2. 交易信号与执行 =====

    def get_signal(self, data):
        """
        获取交易信号
        一步到位的接口，内部自动计算所需信息
        """
        # 先检查冷却期（仅在无持仓时检查）
        if self.get_position_status() != 'NORMAL' and self.is_in_cooldown():
            return "COOLDOWN"

        # 内部计算zscore
        zscore = self.get_zscore(data)
        if zscore is None:
            return "NO_DATA"

        # 内部检查持仓
        has_position = self.get_position_status() == 'NORMAL'

        # 生成信号
        if not has_position:
            if zscore > self.entry_threshold:
                return "SHORT_SPREAD"  # Z-score高，spread偏高，做空
            elif zscore < -self.entry_threshold:
                return "LONG_SPREAD"   # Z-score低，spread偏低，做多
            else:
                return "WAIT"          # 等待入场机会
        else:
            # 有持仓时的出场信号
            if abs(zscore) > self.stop_threshold:
                return "STOP_LOSS"

            if abs(zscore) < self.exit_threshold:
                return "CLOSE"

            return "HOLD"


    def create_order_tag(self, action):
        """
        创建标准化的订单Tag
        action: "OPEN" 或 "CLOSE"
        返回格式: "('AAPL', 'MSFT')_OPEN_20240101"
        """
        return f"{self.pair_id}_{action}_{self.algorithm.Time.strftime('%Y%m%d')}"


    def has_pending_orders(self):
        """
        检查哪个symbol有未完成的订单
        返回: Symbol对象 或 None
        """
        transactions = self.algorithm.Transactions

        if len(transactions.GetOpenOrders(self.symbol1)) > 0:
            return self.symbol1
        if len(transactions.GetOpenOrders(self.symbol2)) > 0:
            return self.symbol2
        return None



    # ===== 3. 持仓管理 =====

    def get_position_info(self) -> Dict:
        """
        获取完整的持仓信息（一次获取，避免重复查询）
        返回所有持仓相关信息
        """
        portfolio = self.algorithm.Portfolio

        # 一次性获取所有需要的数据
        qty1 = portfolio[self.symbol1].Quantity
        qty2 = portfolio[self.symbol2].Quantity
        invested1 = portfolio[self.symbol1].Invested
        invested2 = portfolio[self.symbol2].Invested
        value1 = abs(portfolio[self.symbol1].HoldingsValue) if invested1 else 0
        value2 = abs(portfolio[self.symbol2].HoldingsValue) if invested2 else 0

        # 判断状态
        if invested1 and invested2:
            status = 'NORMAL'
        elif invested1 and not invested2:
            status = 'PARTIAL'
        elif not invested1 and invested2:
            status = 'PARTIAL'
        else:
            status = 'NO POSITION'


        # 判断方向
        direction = None
        if qty1 > 0 and qty2 < 0:
            direction = "long_spread"
        elif qty1 < 0 and qty2 > 0:
            direction = "short_spread"
        elif (qty1 < 0 and qty2 < 0) or (qty1 > 0 and qty2 > 0):
            direction = "same_direction"

        return {
            'status': status,
            'direction': direction,
            'qty1': qty1,
            'qty2': qty2,
            'value1': value1,
            'value2': value2
        }

    def get_position_status(self):
        """
        获取持仓状态
        返回: 'NORMAL'/'PARTIAL'/'NO POSITION'
        """
        info = self.get_position_info()
        return info['status']


    def get_position_direction(self):
        """
        获取持仓方向
        返回: "long_spread", "short_spread", "same_direction" 或 None
        """
        info = self.get_position_info()
        return info['direction']


    def get_position_value(self) -> float:
        """获取当前持仓市值（包括部分持仓）"""
        info = self.get_position_info()
        return info['value1'] + info['value2']


    def get_pair_holding_days(self):
        """
        获取持仓时长（天数）
        返回: 天数 或 None
        """
        if self.get_position_status() != 'NORMAL':
            return None

        entry_time = self.get_entry_time()
        if entry_time is not None:
            return (self.algorithm.Time - entry_time).days

        return None  # 无法获取入场时间


    def reduce_position(self, reduction_ratio: float) -> bool:
        """
        按比例减少持仓
        reduction_ratio: 保留比例(0.8表示保留80%，减仓20%)
        """
        info = self.get_position_info()

        if info['status'] != 'NORMAL':
            return False

        # 计算减仓数量
        reduce_qty1 = int(info['qty1'] * (1 - reduction_ratio))
        reduce_qty2 = int(info['qty2'] * (1 - reduction_ratio))

        # 执行减仓（方向正确的操作）
        if reduce_qty1 != 0:
            self.algorithm.MarketOrder(self.symbol1, -reduce_qty1)
        if reduce_qty2 != 0:
            self.algorithm.MarketOrder(self.symbol2, -reduce_qty2)

        self.algorithm.Debug(
            f"[Pairs.reduce] {self.pair_id} {info['direction']} "
            f"减仓{(1-reduction_ratio)*100:.1f}%"
        )

        return True

    def get_position_pnl(self) -> float:
        """
        计算当前持仓的未实现盈亏
        返回: 盈亏金额 或 0
        """
        portfolio = self.algorithm.Portfolio

        # 获取两腿的未实现盈亏
        pnl1 = portfolio[self.symbol1].UnrealizedProfit if portfolio[self.symbol1].Invested else 0
        pnl2 = portfolio[self.symbol2].UnrealizedProfit if portfolio[self.symbol2].Invested else 0

        # 返回总盈亏
        return pnl1 + pnl2


    def _get_order_time(self, action_type: str):
        """
        内部方法：获取指定类型的订单时间
        action_type: "OPEN" 或 "CLOSE"
        """
        # 查询指定类型的订单
        orders = self.algorithm.Transactions.GetOrders(
            lambda x: str(self.pair_id) in x.Tag
                     and action_type in x.Tag
                     and x.Status == OrderStatus.Filled
        )

        if not orders:
            return None

        # 按时间排序，获取最近的
        orders.sort(key=lambda x: x.Time, reverse=True)

        # 确保两腿都成交，取较晚的时间
        latest_pair_time = None
        for i in range(len(orders)):
            # 获取同一天的订单（tag中日期相同）
            date = orders[i].Tag.split('_')[-1]
            same_date_orders = [o for o in orders if date in o.Tag]

            # 检查是否有两个symbol的订单
            has_symbol1 = any(o.Symbol == self.symbol1 for o in same_date_orders)
            has_symbol2 = any(o.Symbol == self.symbol2 for o in same_date_orders)

            if has_symbol1 and has_symbol2:
                # 找到这组订单的最晚时间
                latest_pair_time = max(o.Time for o in same_date_orders)
                break

        return latest_pair_time


    def get_entry_time(self):
        """获取最近的入场时间"""
        return self._get_order_time("OPEN")


    def get_exit_time(self):
        """获取最近的退出时间"""
        return self._get_order_time("CLOSE")


    def is_in_cooldown(self):
        """
        检查是否在冷却期内
        返回: True（在冷却期）/ False（可以交易）
        """
        # 获取最近的退出时间
        exit_time = self.get_exit_time()

        # 如果没有退出时间，不在冷却期
        if exit_time is None:
            return False

        # 计算距离上次退出的时间
        days_since_exit = (self.algorithm.Time - exit_time).days

        # 判断是否在冷却期（使用config中的冷却天数）
        return days_since_exit < self.cooldown_days



    # ===== 4. 风控与维护 =====

    def needs_stop_loss(self, data) -> bool:
        """检查是否需要止损（Z-score超过阈值）"""
        if self.get_position_status() != 'NORMAL':
            return False

        zscore = self.get_zscore(data)
        return zscore is not None and abs(zscore) > self.stop_threshold



    def is_above_concentration_limit(self) -> bool:
        """检查配对是否超过集中度限制"""
        position_value = self.get_position_value()
        portfolio_value = self.algorithm.Portfolio.TotalPortfolioValue

        if portfolio_value > 0:
            concentration = position_value / portfolio_value
            return concentration > self.max_concentration
        return False


    def get_sector(self) -> str:
        """获取配对所属行业"""
        return self.sector
