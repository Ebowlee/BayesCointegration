"""
OrderTracker 单元测试
测试订单跟踪器的核心功能
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# 设置测试环境
import tests.setup_test_env

import unittest
from datetime import datetime, timedelta
from tests.mocks.mock_quantconnect import (
    MockAlgorithm, MockSymbol, MockOrder, MockOrderEvent, 
    OrderStatus, create_filled_order_event, create_submitted_order_event
)
from src.PairRegistry import PairRegistry
from src.OrderTracker import OrderTracker


class TestOrderTracker(unittest.TestCase):
    """OrderTracker 单元测试类"""
    
    def setUp(self):
        """测试前准备"""
        self.algorithm = MockAlgorithm()
        self.pair_registry = PairRegistry(self.algorithm)
        self.order_tracker = OrderTracker(self.algorithm, self.pair_registry)
        
        # 创建测试用的股票
        self.symbol1 = MockSymbol("AAPL")
        self.symbol2 = MockSymbol("MSFT")
        self.symbol3 = MockSymbol("GOOGL")
        self.symbol4 = MockSymbol("AMZN")
        
    def test_initialization(self):
        """测试初始化"""
        self.assertIsNotNone(self.order_tracker.algorithm)
        self.assertIsNotNone(self.order_tracker.pair_registry)
        self.assertEqual(len(self.order_tracker.orders), 0)
        self.assertEqual(len(self.order_tracker.pair_orders), 0)
        
    def test_order_creation_and_update(self):
        """测试订单创建和更新"""
        # 设置配对
        self.pair_registry.update_pairs([(self.symbol1, self.symbol2)])
        
        # 创建订单
        order = self.algorithm.Transactions.CreateOrder(self.symbol1, 100, self.algorithm.Time)
        
        # 提交订单
        submit_event = create_submitted_order_event(order)
        self.order_tracker.on_order_event(submit_event)
        
        # 验证订单被记录
        self.assertEqual(len(self.order_tracker.orders), 1)
        order_info = self.order_tracker.orders[order.OrderId]
        self.assertEqual(order_info.status, OrderStatus.Submitted)
        self.assertTrue(order_info.is_pending)
        self.assertFalse(order_info.is_filled)
        
        # 订单成交
        fill_event = create_filled_order_event(order)
        self.order_tracker.on_order_event(fill_event)
        
        # 验证状态更新
        self.assertEqual(order_info.status, OrderStatus.Filled)
        self.assertTrue(order_info.is_filled)
        self.assertFalse(order_info.is_pending)
        self.assertIsNotNone(order_info.fill_time)
        
    def test_pair_identification(self):
        """测试配对识别"""
        # 设置配对
        self.pair_registry.update_pairs([(self.symbol1, self.symbol2)])
        
        # 创建配对订单
        order1 = self.algorithm.Transactions.CreateOrder(self.symbol1, 100, self.algorithm.Time)
        order2 = self.algorithm.Transactions.CreateOrder(self.symbol2, -50, self.algorithm.Time)
        
        # 处理订单事件
        self.order_tracker.on_order_event(create_submitted_order_event(order1))
        self.order_tracker.on_order_event(create_submitted_order_event(order2))
        
        # 验证配对被识别
        self.assertEqual(len(self.order_tracker.pair_orders), 1)
        pair_id = "AAPL&MSFT"
        self.assertIn(pair_id, self.order_tracker.pair_orders)
        
        pair_info = self.order_tracker.pair_orders[pair_id]
        self.assertEqual(len(pair_info.orders), 2)
        
    def test_entry_exit_detection(self):
        """测试建仓/平仓检测"""
        self.pair_registry.update_pairs([(self.symbol1, self.symbol2)])
        
        # 模拟无持仓状态
        self.algorithm.Portfolio[self.symbol1].Quantity = 0
        
        # 建仓订单
        entry_order = self.algorithm.Transactions.CreateOrder(self.symbol1, 100, self.algorithm.Time)
        self.order_tracker.on_order_event(create_submitted_order_event(entry_order))
        
        order_info = self.order_tracker.orders[entry_order.OrderId]
        self.assertEqual(order_info.order_type, 'entry')
        
        # 模拟有持仓状态
        self.algorithm.Portfolio[self.symbol1].Quantity = 100
        
        # 平仓订单
        exit_order = self.algorithm.Transactions.CreateOrder(self.symbol1, -100, self.algorithm.Time)
        self.order_tracker.on_order_event(create_submitted_order_event(exit_order))
        
        order_info = self.order_tracker.orders[exit_order.OrderId]
        self.assertEqual(order_info.order_type, 'exit')
        
    def test_pair_normal_status(self):
        """测试配对正常状态判断"""
        self.pair_registry.update_pairs([(self.symbol1, self.symbol2)])
        
        # 创建配对订单
        order1 = self.algorithm.Transactions.CreateOrder(self.symbol1, 100, self.algorithm.Time)
        order2 = self.algorithm.Transactions.CreateOrder(self.symbol2, -50, self.algorithm.Time)
        
        # 两边都提交 - 应该是正常
        self.order_tracker.on_order_event(create_submitted_order_event(order1))
        self.order_tracker.on_order_event(create_submitted_order_event(order2))
        
        pair_info = list(self.order_tracker.pair_orders.values())[0]
        self.assertTrue(pair_info.is_normal())
        
        # 两边都成交 - 应该是正常
        self.order_tracker.on_order_event(create_filled_order_event(order1))
        self.order_tracker.on_order_event(create_filled_order_event(order2))
        
        self.assertTrue(pair_info.is_normal())
        
    def test_pair_abnormal_status(self):
        """测试配对异常状态判断"""
        self.pair_registry.update_pairs([(self.symbol1, self.symbol2)])
        
        # 创建配对订单
        order1 = self.algorithm.Transactions.CreateOrder(self.symbol1, 100, self.algorithm.Time)
        order2 = self.algorithm.Transactions.CreateOrder(self.symbol2, -50, self.algorithm.Time)
        
        # 只有一边提交 - 应该是异常
        self.order_tracker.on_order_event(create_submitted_order_event(order1))
        
        # 只有一边成交 - 应该是异常
        self.order_tracker.on_order_event(create_filled_order_event(order1))
        self.order_tracker.on_order_event(create_submitted_order_event(order2))
        
        abnormal_pairs = self.order_tracker.get_abnormal_pairs()
        self.assertEqual(len(abnormal_pairs), 1)
        self.assertEqual(abnormal_pairs[0], (self.symbol1, self.symbol2))
        
    def test_entry_time_recording(self):
        """测试建仓时间记录"""
        self.pair_registry.update_pairs([(self.symbol1, self.symbol2)])
        
        # 创建建仓订单
        entry_time = self.algorithm.Time
        order1 = self.algorithm.Transactions.CreateOrder(self.symbol1, 100, entry_time)
        order2 = self.algorithm.Transactions.CreateOrder(self.symbol2, -50, entry_time)
        
        # 提交订单
        self.order_tracker.on_order_event(create_submitted_order_event(order1))
        self.order_tracker.on_order_event(create_submitted_order_event(order2))
        
        # 此时还没有记录时间（未成交）
        self.assertIsNone(self.order_tracker.get_pair_entry_time(self.symbol1, self.symbol2))
        
        # 第一个订单成交
        fill_time1 = entry_time + timedelta(minutes=1)
        self.order_tracker.on_order_event(create_filled_order_event(order1, fill_time1))
        
        # 仍然没有记录时间（只有一边成交）
        self.assertIsNone(self.order_tracker.get_pair_entry_time(self.symbol1, self.symbol2))
        
        # 第二个订单成交
        fill_time2 = entry_time + timedelta(minutes=2)
        self.order_tracker.on_order_event(create_filled_order_event(order2, fill_time2))
        
        # 现在应该记录时间（取较晚的时间）
        recorded_time = self.order_tracker.get_pair_entry_time(self.symbol1, self.symbol2)
        self.assertEqual(recorded_time, fill_time2)
        
    def test_holding_period_calculation(self):
        """测试持仓天数计算"""
        self.pair_registry.update_pairs([(self.symbol1, self.symbol2)])
        
        # 创建并成交订单
        entry_time = self.algorithm.Time
        order1 = self.algorithm.Transactions.CreateOrder(self.symbol1, 100, entry_time)
        order2 = self.algorithm.Transactions.CreateOrder(self.symbol2, -50, entry_time)
        
        self.order_tracker.on_order_event(create_submitted_order_event(order1))
        self.order_tracker.on_order_event(create_submitted_order_event(order2))
        self.order_tracker.on_order_event(create_filled_order_event(order1, entry_time))
        self.order_tracker.on_order_event(create_filled_order_event(order2, entry_time))
        
        # 当天持仓天数应该是0
        holding_days = self.order_tracker.get_holding_period(self.symbol1, self.symbol2)
        self.assertEqual(holding_days, 0)
        
        # 5天后
        self.algorithm.AddDays(5)
        holding_days = self.order_tracker.get_holding_period(self.symbol1, self.symbol2)
        self.assertEqual(holding_days, 5)
        
        # 30天后
        self.algorithm.AddDays(25)
        holding_days = self.order_tracker.get_holding_period(self.symbol1, self.symbol2)
        self.assertEqual(holding_days, 30)
        
    def test_cooldown_check(self):
        """测试冷却期检查"""
        self.pair_registry.update_pairs([(self.symbol1, self.symbol2)])
        
        # 创建建仓和平仓订单
        self.algorithm.Portfolio[self.symbol1].Quantity = 100
        self.algorithm.Portfolio[self.symbol2].Quantity = -50
        
        exit_time = self.algorithm.Time
        exit_order1 = self.algorithm.Transactions.CreateOrder(self.symbol1, -100, exit_time)
        exit_order2 = self.algorithm.Transactions.CreateOrder(self.symbol2, 50, exit_time)
        
        # 处理平仓
        self.order_tracker.on_order_event(create_submitted_order_event(exit_order1))
        self.order_tracker.on_order_event(create_submitted_order_event(exit_order2))
        self.order_tracker.on_order_event(create_filled_order_event(exit_order1, exit_time))
        self.order_tracker.on_order_event(create_filled_order_event(exit_order2, exit_time))
        
        # 刚平仓，应该在冷却期
        self.assertTrue(self.order_tracker.is_in_cooldown(self.symbol1, self.symbol2, 7))
        
        # 5天后，仍在冷却期
        self.algorithm.AddDays(5)
        self.assertTrue(self.order_tracker.is_in_cooldown(self.symbol1, self.symbol2, 7))
        
        # 8天后，不在冷却期
        self.algorithm.AddDays(3)
        self.assertFalse(self.order_tracker.is_in_cooldown(self.symbol1, self.symbol2, 7))
        
    def test_pending_orders_check(self):
        """测试待成交订单检查"""
        # 创建订单
        order = self.algorithm.Transactions.CreateOrder(self.symbol1, 100, self.algorithm.Time)
        
        # 提交订单
        self.order_tracker.on_order_event(create_submitted_order_event(order))
        
        # 应该有待成交订单
        self.assertTrue(self.order_tracker.has_pending_orders(self.symbol1))
        self.assertFalse(self.order_tracker.has_pending_orders(self.symbol2))
        
        # 订单成交
        self.order_tracker.on_order_event(create_filled_order_event(order))
        
        # 不应该有待成交订单
        self.assertFalse(self.order_tracker.has_pending_orders(self.symbol1))
        
    def test_old_records_cleanup(self):
        """测试旧记录清理"""
        self.pair_registry.update_pairs([(self.symbol1, self.symbol2)])
        
        # 创建旧订单
        old_time = self.algorithm.Time - timedelta(days=100)
        old_order = MockOrder(999, self.symbol1, 100, old_time)
        self.algorithm.Transactions.orders[999] = old_order
        
        # 处理旧订单
        self.order_tracker.on_order_event(create_submitted_order_event(old_order))
        
        # 验证订单存在
        self.assertIn(999, self.order_tracker.orders)
        
        # 清理旧记录
        self.order_tracker.clear_old_records(days_to_keep=65)
        
        # 验证旧订单被清理
        self.assertNotIn(999, self.order_tracker.orders)


if __name__ == '__main__':
    unittest.main()