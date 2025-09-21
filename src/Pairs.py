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

        # === 交易状态 ===
        self.entry_zscore = None
        self.spread_history = []  # 初始化spread历史列表

        # === 冷却设置 ===
        self.cooldown_days = config['cooldown_days']  # 从config读取

        # === 历史追踪 ===
        self.creation_time = algorithm.Time  # 首次创建时间
        self.reactivation_count = 0  # 重新激活次数（配对消失又出现）
        self.trade_history = []  # 每次交易的记录
        self.total_pnl = 0  # 累计盈亏



    # ===== 核心计算方法 =====

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



    # ===== 主要业务方法 =====

    def get_signal(self, data):
        """
        获取交易信号
        一步到位的接口，内部自动计算所需信息
        """
        # 先检查冷却期（仅在无持仓时检查）
        if self.get_position_status()['status'] != 'NORMAL' and self.is_in_cooldown():
            return "COOLDOWN"

        # 内部计算zscore
        zscore = self.get_zscore(data)
        if zscore is None:
            return "NO_DATA"

        # 内部检查持仓
        has_position = self.get_position_status()['status'] == 'NORMAL'

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



    # ===== 持仓状态查询 =====

    def get_position_status(self):
        # 获取持仓状态的完整信息
        portfolio = self.algorithm.Portfolio
        invested1 = portfolio[self.symbol1].Invested
        invested2 = portfolio[self.symbol2].Invested

        if invested1 and invested2:
            return {
                'status': 'NORMAL',
            }
        elif invested1 and not invested2:
            return {
                'status': 'PARTIAL',
                'invested': self.symbol1,
                'missing': self.symbol2
            }
        elif not invested1 and invested2:
            return {
                'status': 'PARTIAL',
                'invested': self.symbol2,
                'missing': self.symbol1
            }
        else:
            return {
                'status': 'NONE'
            }


    def get_position_direction(self):
        """
        获取持仓方向
        返回: "long_spread", "short_spread", "same_direction" 或 None
        """
        if self.get_position_status()['status'] != 'NORMAL':
            return None

        qty1 = self.algorithm.Portfolio[self.symbol1].Quantity
        qty2 = self.algorithm.Portfolio[self.symbol2].Quantity

        if qty1 > 0 and qty2 < 0:
            return "long_spread"  # 做多spread (买symbol1卖symbol2)
        elif qty1 < 0 and qty2 > 0:
            return "short_spread"  # 做空spread (卖symbol1买symbol2)
        elif (qty1 < 0 and qty2 < 0) or (qty1 > 0 and qty2 > 0):
            return "same_direction"
        else:
            return None


    def get_position_age(self):
        """
        获取持仓时长（天数）
        返回: 天数 或 None
        """
        if self.get_position_status()['status'] != 'NORMAL':
            return None

        entry_time = self.get_entry_time()
        if entry_time is not None:
            return (self.algorithm.Time - entry_time).days

        return None  # 无法获取入场时间



    # ===== 异常检测方法 =====

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



    # ===== 生命周期管理 =====

    def get_entry_time(self):
        # 从订单历史获取最近的入场时间
        open_orders = self.algorithm.Transactions.GetOrders(
            lambda x: str(self.pair_id) in x.Tag
                     and "OPEN" in x.Tag
                     and x.Status == OrderStatus.Filled
        )

        if not open_orders:
            return None

        # 按时间排序，获取最近的
        open_orders.sort(key=lambda x: x.Time, reverse=True)

        # 确保两腿都成交，取较晚的时间
        # 查找最近一组的两个symbol订单
        latest_pair_time = None
        for i in range(len(open_orders)):
            # 获取同一天的订单（tag中日期相同）
            date = open_orders[i].Tag.split('_')[-1]
            same_date_orders = [o for o in open_orders if date in o.Tag]

            # 检查是否有两个symbol的订单
            has_symbol1 = any(o.Symbol == self.symbol1 for o in same_date_orders)
            has_symbol2 = any(o.Symbol == self.symbol2 for o in same_date_orders)

            if has_symbol1 and has_symbol2:
                # 找到这组订单的最晚时间
                latest_pair_time = max(o.Time for o in same_date_orders)
                break

        return latest_pair_time

    def get_exit_time(self):
        # 从订单历史获取最近的退出时间
        close_orders = self.algorithm.Transactions.GetOrders(
            lambda x: str(self.pair_id) in x.Tag
                     and "CLOSE" in x.Tag
                     and x.Status == OrderStatus.Filled
        )

        if not close_orders:
            return None

        # 最近的平仓时间
        close_orders.sort(key=lambda x: x.Time, reverse=True)
        return close_orders[0].Time


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



    # ===== 参数维护 =====

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



    # ===== 工具方法 =====

    def create_order_tag(self, action):
        # 创建标准化的订单Tag
        # action: "OPEN" 或 "CLOSE"
        # 返回格式: "('AAPL', 'MSFT')_OPEN_20240101"
        return f"{self.pair_id}_{action}_{self.algorithm.Time.strftime('%Y%m%d')}"