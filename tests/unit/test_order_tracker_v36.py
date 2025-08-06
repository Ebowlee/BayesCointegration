"""
测试OrderTracker v3.6.0的持仓时间计算修复
"""
import unittest
from datetime import datetime, timedelta
from unittest.mock import Mock, MagicMock
from src.OrderTracker import OrderTracker, OrderInfo, PairOrderInfo
from src.PairRegistry import PairRegistry

# 模拟OrderStatus
class OrderStatus:
    Submitted = 0
    PartiallyFilled = 1
    Filled = 2
    Canceled = 3
    UpdateSubmitted = 4

# 将OrderStatus注入到OrderInfo模块
import src.OrderTracker
src.OrderTracker.OrderStatus = OrderStatus


class TestOrderTrackerV36(unittest.TestCase):
    """测试v3.6.0修复：确保持仓时间分段独立计算"""
    
    def setUp(self):
        """设置测试环境"""
        self.algorithm = Mock()
        self.algorithm.Debug = Mock()
        self.algorithm.Time = datetime(2024, 9, 1)
        self.algorithm.Portfolio = {}
        self.algorithm.Transactions.GetOrderById = Mock()
        
        # 创建PairRegistry和OrderTracker
        self.pair_registry = PairRegistry(self.algorithm)
        self.order_tracker = OrderTracker(self.algorithm, self.pair_registry)
        
        # 创建测试用的Symbol
        self.symbol1 = self._create_symbol("AAPL")
        self.symbol2 = self._create_symbol("MSFT")
        
        # 设置Portfolio
        self._setup_portfolio()
    
    def _create_symbol(self, ticker):
        """创建Symbol对象"""
        symbol = Mock()
        symbol.Value = ticker
        symbol.__str__ = lambda self: ticker
        return symbol
    
    def _setup_portfolio(self):
        """设置Portfolio"""
        # AAPL持仓
        holding1 = Mock()
        holding1.Invested = False
        holding1.Quantity = 0
        self.algorithm.Portfolio[self.symbol1] = holding1
        
        # MSFT持仓
        holding2 = Mock()
        holding2.Invested = False
        holding2.Quantity = 0
        self.algorithm.Portfolio[self.symbol2] = holding2
    
    def test_multiple_holding_periods(self):
        """测试同一配对多次建仓平仓的持仓时间计算"""
        # 更新配对列表
        self.pair_registry.update_pairs([(self.symbol1, self.symbol2)])
        
        # ========== 第一段持仓：7月1日-7月10日 ==========
        # 7月1日建仓
        self.algorithm.Time = datetime(2024, 7, 1)
        
        # 创建配对信息
        pair_id = "AAPL&MSFT"
        pair_info = PairOrderInfo(pair_id, self.symbol1, self.symbol2)
        
        # 添加第一段的entry订单
        entry1_s1 = OrderInfo(1, self.symbol1, 100, datetime(2024, 7, 1), 'entry')
        entry1_s1.status = OrderStatus.Filled
        entry1_s1.fill_time = datetime(2024, 7, 1)
        
        entry1_s2 = OrderInfo(2, self.symbol2, -100, datetime(2024, 7, 1), 'entry')
        entry1_s2.status = OrderStatus.Filled
        entry1_s2.fill_time = datetime(2024, 7, 1)
        
        pair_info.orders.extend([entry1_s1, entry1_s2])
        pair_info.update_times()
        
        self.order_tracker.pair_orders[pair_id] = pair_info
        
        # 设置持仓状态
        self.algorithm.Portfolio[self.symbol1].Invested = True
        self.algorithm.Portfolio[self.symbol2].Invested = True
        
        # 7月5日检查持仓时间 - 应该是4天
        self.algorithm.Time = datetime(2024, 7, 5)
        holding_days = self.order_tracker.get_holding_period(self.symbol1, self.symbol2)
        self.assertEqual(holding_days, 4, "第一段持仓第5天应该显示4天")
        
        # 7月10日平仓
        self.algorithm.Time = datetime(2024, 7, 10)
        
        exit1_s1 = OrderInfo(3, self.symbol1, -100, datetime(2024, 7, 10), 'exit')
        exit1_s1.status = OrderStatus.Filled
        exit1_s1.fill_time = datetime(2024, 7, 10)
        
        exit1_s2 = OrderInfo(4, self.symbol2, 100, datetime(2024, 7, 10), 'exit')
        exit1_s2.status = OrderStatus.Filled
        exit1_s2.fill_time = datetime(2024, 7, 10)
        
        pair_info.orders.extend([exit1_s1, exit1_s2])
        pair_info.update_times()
        
        # 清除持仓状态
        self.algorithm.Portfolio[self.symbol1].Invested = False
        self.algorithm.Portfolio[self.symbol2].Invested = False
        
        # 验证平仓后没有持仓时间
        holding_days = self.order_tracker.get_holding_period(self.symbol1, self.symbol2)
        self.assertIsNone(holding_days, "平仓后应该没有持仓时间")
        
        # ========== 第二段持仓：7月20日-7月25日 ==========
        # 7月20日重新建仓
        self.algorithm.Time = datetime(2024, 7, 20)
        
        entry2_s1 = OrderInfo(5, self.symbol1, 100, datetime(2024, 7, 20), 'entry')
        entry2_s1.status = OrderStatus.Filled
        entry2_s1.fill_time = datetime(2024, 7, 20)
        
        entry2_s2 = OrderInfo(6, self.symbol2, -100, datetime(2024, 7, 20), 'entry')
        entry2_s2.status = OrderStatus.Filled
        entry2_s2.fill_time = datetime(2024, 7, 20)
        
        pair_info.orders.extend([entry2_s1, entry2_s2])
        pair_info.update_times()  # 这应该更新entry_time为7月20日
        
        # 重新设置持仓状态
        self.algorithm.Portfolio[self.symbol1].Invested = True
        self.algorithm.Portfolio[self.symbol2].Invested = True
        
        # 7月23日检查持仓时间 - 应该是3天，而不是22天！
        self.algorithm.Time = datetime(2024, 7, 23)
        holding_days = self.order_tracker.get_holding_period(self.symbol1, self.symbol2)
        self.assertEqual(holding_days, 3, "第二段持仓第4天应该显示3天，不应该累计第一段")
        
        # 验证内部状态
        self.assertEqual(pair_info.exit_time, datetime(2024, 7, 10), "exit_time应该是7月10日")
        self.assertEqual(pair_info.entry_time, datetime(2024, 7, 20), "entry_time应该是7月20日")
    
    def test_entry_time_reset_after_exit(self):
        """测试平仓后entry_time正确重置"""
        # 设置配对
        self.pair_registry.update_pairs([(self.symbol1, self.symbol2)])
        
        pair_id = "AAPL&MSFT"
        pair_info = PairOrderInfo(pair_id, self.symbol1, self.symbol2)
        
        # 第一次建仓
        entry1 = OrderInfo(1, self.symbol1, 100, datetime(2024, 7, 1), 'entry')
        entry1.status = OrderStatus.Filled
        entry1.fill_time = datetime(2024, 7, 1)
        
        entry2 = OrderInfo(2, self.symbol2, -100, datetime(2024, 7, 1), 'entry')
        entry2.status = OrderStatus.Filled
        entry2.fill_time = datetime(2024, 7, 1)
        
        pair_info.orders.extend([entry1, entry2])
        pair_info.update_times()
        
        self.assertEqual(pair_info.entry_time, datetime(2024, 7, 1))
        self.assertIsNone(pair_info.exit_time)
        
        # 平仓
        exit1 = OrderInfo(3, self.symbol1, -100, datetime(2024, 7, 10), 'exit')
        exit1.status = OrderStatus.Filled
        exit1.fill_time = datetime(2024, 7, 10)
        
        exit2 = OrderInfo(4, self.symbol2, 100, datetime(2024, 7, 10), 'exit')
        exit2.status = OrderStatus.Filled
        exit2.fill_time = datetime(2024, 7, 10)
        
        pair_info.orders.extend([exit1, exit2])
        pair_info.update_times()
        
        # v3.6.0修复前：entry_time会被重置为None
        # v3.6.0修复后：entry_time应该保持为None，直到新的entry订单
        self.assertIsNone(pair_info.entry_time, "平仓后entry_time应该被重置为None")
        self.assertEqual(pair_info.exit_time, datetime(2024, 7, 10))
        
        # 第二次建仓
        entry3 = OrderInfo(5, self.symbol1, 100, datetime(2024, 7, 20), 'entry')
        entry3.status = OrderStatus.Filled
        entry3.fill_time = datetime(2024, 7, 20)
        
        entry4 = OrderInfo(6, self.symbol2, -100, datetime(2024, 7, 20), 'entry')
        entry4.status = OrderStatus.Filled
        entry4.fill_time = datetime(2024, 7, 20)
        
        pair_info.orders.extend([entry3, entry4])
        pair_info.update_times()
        
        # 新的entry_time应该是7月20日
        self.assertEqual(pair_info.entry_time, datetime(2024, 7, 20), 
                        "第二次建仓的entry_time应该是7月20日")
        self.assertEqual(pair_info.exit_time, datetime(2024, 7, 10),
                        "exit_time应该保持为上次平仓时间")
    
    def test_holding_period_with_no_position(self):
        """测试没有持仓时返回None"""
        # 设置配对但不设置持仓
        self.pair_registry.update_pairs([(self.symbol1, self.symbol2)])
        
        # Portfolio显示没有持仓
        self.algorithm.Portfolio[self.symbol1].Invested = False
        self.algorithm.Portfolio[self.symbol2].Invested = False
        
        holding_days = self.order_tracker.get_holding_period(self.symbol1, self.symbol2)
        self.assertIsNone(holding_days, "没有持仓时应该返回None")
    
    def test_abnormal_time_records(self):
        """测试异常时间记录的处理"""
        # 设置配对和持仓
        self.pair_registry.update_pairs([(self.symbol1, self.symbol2)])
        self.algorithm.Portfolio[self.symbol1].Invested = True
        self.algorithm.Portfolio[self.symbol2].Invested = True
        
        pair_id = "AAPL&MSFT"
        pair_info = PairOrderInfo(pair_id, self.symbol1, self.symbol2)
        
        # 设置异常的时间：entry_time在exit_time之前
        pair_info.entry_time = datetime(2024, 7, 1)
        pair_info.exit_time = datetime(2024, 7, 10)  # exit在entry之后，这是异常的
        
        self.order_tracker.pair_orders[pair_id] = pair_info
        self.algorithm.Time = datetime(2024, 7, 15)
        
        # 应该检测到异常并返回None
        holding_days = self.order_tracker.get_holding_period(self.symbol1, self.symbol2)
        self.assertIsNone(holding_days, "检测到时间异常时应该返回None")
        
        # 验证是否记录了警告日志
        self.algorithm.Debug.assert_called()


if __name__ == '__main__':
    unittest.main()