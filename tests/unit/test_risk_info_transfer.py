"""
OrderTracker与RiskManagement信息传递准确性测试
验证模块间信息传递的准确性和完整性
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# 设置测试环境
import tests.setup_test_env

import unittest
from datetime import datetime, timedelta
from tests.mocks.mock_quantconnect import (
    MockAlgorithm, MockSymbol, MockHolding, MockPortfolioTarget,
    create_filled_order_event
)
from src.PairRegistry import PairRegistry
from src.OrderTracker import OrderTracker
from src.RiskManagement import BayesianCointegrationRiskManagementModel

# 为测试环境设置PortfolioTarget别名
import tests.mocks.mock_quantconnect as mock_qc
mock_qc.PortfolioTarget = MockPortfolioTarget


class TestRiskInfoTransfer(unittest.TestCase):
    """测试OrderTracker与RiskManagement之间的信息传递"""
    
    def setUp(self):
        """测试前准备"""
        self.algorithm = MockAlgorithm(datetime(2024, 8, 1))
        self.pair_registry = PairRegistry(self.algorithm)
        self.order_tracker = OrderTracker(self.algorithm, self.pair_registry)
        
        # 风控配置
        self.config = {
            'max_holding_days': 30,
            'cooldown_days': 7,
            'max_pair_drawdown': 0.10,
            'max_single_drawdown': 0.20
        }
        
        self.risk_manager = BayesianCointegrationRiskManagementModel(
            self.algorithm, self.config, self.order_tracker, self.pair_registry
        )
        
        # 创建测试股票
        self.symbol1 = MockSymbol("AAPL")
        self.symbol2 = MockSymbol("MSFT")
        
    def test_order_tracker_time_accuracy(self):
        """测试OrderTracker记录的时间是否准确传递给RiskManagement"""
        # 设置配对
        self.pair_registry.update_pairs([(self.symbol1, self.symbol2)])
        
        # 保存原始时间
        original_time = self.algorithm.Time
        
        # 回到25天前建仓
        entry_time = original_time - timedelta(days=25)
        self.algorithm.SetTime(entry_time)
        
        # 创建并成交订单
        order1 = self.algorithm.Transactions.CreateOrder(self.symbol1, 100, self.algorithm.Time)
        order2 = self.algorithm.Transactions.CreateOrder(self.symbol2, -50, self.algorithm.Time)
        
        self.order_tracker.on_order_event(create_filled_order_event(order1))
        self.order_tracker.on_order_event(create_filled_order_event(order2))
        
        # 验证OrderTracker记录的时间
        recorded_entry_time = self.order_tracker.get_pair_entry_time(self.symbol1, self.symbol2)
        self.assertEqual(recorded_entry_time, entry_time)
        
        # 回到现在
        self.algorithm.SetTime(original_time)
        
        # 设置持仓
        self.algorithm.Portfolio[self.symbol1] = MockHolding(self.symbol1, True, 100, 100, 100)
        self.algorithm.Portfolio[self.symbol2] = MockHolding(self.symbol2, True, -50, 100, 100)
        
        # 通过RiskManagement获取持仓天数
        active_pairs = self.risk_manager._get_active_pairs()
        self.assertEqual(len(active_pairs), 1)
        self.assertEqual(active_pairs[0]['holding_days'], 25)
        
        # 验证OrderTracker的计算
        holding_days = self.order_tracker.get_holding_period(self.symbol1, self.symbol2)
        self.assertEqual(holding_days, 25)
        
    def test_pair_registry_consistency(self):
        """测试配对信息在各模块间的一致性"""
        # 设置配对（使用特定顺序）
        self.pair_registry.update_pairs([(self.symbol2, self.symbol1)])  # MSFT, AAPL
        
        # 验证PairRegistry存储的顺序
        pair = self.pair_registry.get_pair_for_symbol(self.symbol1)
        self.assertEqual(pair[0], self.symbol2)  # MSFT
        self.assertEqual(pair[1], self.symbol1)  # AAPL
        
        # 建仓
        self.algorithm.Portfolio[self.symbol1] = MockHolding(self.symbol1, False, 0, 0, 100)
        self.algorithm.Portfolio[self.symbol2] = MockHolding(self.symbol2, False, 0, 0, 100)
        
        order1 = self.algorithm.Transactions.CreateOrder(self.symbol1, -50, self.algorithm.Time)
        order2 = self.algorithm.Transactions.CreateOrder(self.symbol2, 100, self.algorithm.Time)
        
        self.order_tracker.on_order_event(create_filled_order_event(order1))
        self.order_tracker.on_order_event(create_filled_order_event(order2))
        
        # 设置持仓
        self.algorithm.Portfolio[self.symbol1] = MockHolding(self.symbol1, True, -50, 100, 100)
        self.algorithm.Portfolio[self.symbol2] = MockHolding(self.symbol2, True, 100, 100, 100)
        
        # RiskManagement应该使用相同的配对顺序
        active_pairs = self.risk_manager._get_active_pairs()
        self.assertEqual(len(active_pairs), 1)
        risk_pair = active_pairs[0]['pair']
        self.assertEqual(risk_pair[0], self.symbol2)  # MSFT
        self.assertEqual(risk_pair[1], self.symbol1)  # AAPL
        
    def test_holding_days_calculation_chain(self):
        """详细验证持仓天数计算的完整链路"""
        self.pair_registry.update_pairs([(self.symbol1, self.symbol2)])
        
        # 记录每个时间点
        times = []
        original_time = self.algorithm.Time
        
        # Day 0: 建仓
        self.algorithm.SetTime(original_time)
        times.append(("建仓", self.algorithm.Time))
        
        order1 = self.algorithm.Transactions.CreateOrder(self.symbol1, 100, self.algorithm.Time)
        order2 = self.algorithm.Transactions.CreateOrder(self.symbol2, -50, self.algorithm.Time)
        
        self.order_tracker.on_order_event(create_filled_order_event(order1))
        self.order_tracker.on_order_event(create_filled_order_event(order2))
        
        # 验证当天
        holding_days = self.order_tracker.get_holding_period(self.symbol1, self.symbol2)
        self.assertEqual(holding_days, 0, "建仓当天应该是0天")
        
        # Day 1
        self.algorithm.AddDays(1)
        times.append(("Day 1", self.algorithm.Time))
        holding_days = self.order_tracker.get_holding_period(self.symbol1, self.symbol2)
        self.assertEqual(holding_days, 1)
        
        # Day 10
        self.algorithm.AddDays(9)
        times.append(("Day 10", self.algorithm.Time))
        holding_days = self.order_tracker.get_holding_period(self.symbol1, self.symbol2)
        self.assertEqual(holding_days, 10)
        
        # Day 30
        self.algorithm.AddDays(20)
        times.append(("Day 30", self.algorithm.Time))
        holding_days = self.order_tracker.get_holding_period(self.symbol1, self.symbol2)
        self.assertEqual(holding_days, 30)
        
        # 设置持仓并验证RiskManagement
        self.algorithm.Portfolio[self.symbol1] = MockHolding(self.symbol1, True, 100, 100, 100)
        self.algorithm.Portfolio[self.symbol2] = MockHolding(self.symbol2, True, -50, 100, 100)
        
        active_pairs = self.risk_manager._get_active_pairs()
        self.assertEqual(active_pairs[0]['holding_days'], 30)
        
    def test_cooldown_time_transfer(self):
        """测试冷却期时间传递的准确性"""
        self.pair_registry.update_pairs([(self.symbol1, self.symbol2)])
        
        original_time = self.algorithm.Time
        
        # 10天前建仓
        self.algorithm.SetTime(original_time - timedelta(days=10))
        
        # 建仓
        self.algorithm.Portfolio[self.symbol1] = MockHolding(self.symbol1, False, 0, 0, 100)
        self.algorithm.Portfolio[self.symbol2] = MockHolding(self.symbol2, False, 0, 0, 100)
        
        entry_order1 = self.algorithm.Transactions.CreateOrder(self.symbol1, 100, self.algorithm.Time)
        entry_order2 = self.algorithm.Transactions.CreateOrder(self.symbol2, -50, self.algorithm.Time)
        
        self.order_tracker.on_order_event(create_filled_order_event(entry_order1))
        self.order_tracker.on_order_event(create_filled_order_event(entry_order2))
        
        # 设置持仓
        self.algorithm.Portfolio[self.symbol1] = MockHolding(self.symbol1, True, 100, 100, 100)
        self.algorithm.Portfolio[self.symbol2] = MockHolding(self.symbol2, True, -50, 100, 100)
        
        # 5天前平仓
        exit_time = original_time - timedelta(days=5)
        self.algorithm.SetTime(exit_time)
        
        exit_order1 = self.algorithm.Transactions.CreateOrder(self.symbol1, -100, self.algorithm.Time)
        exit_order2 = self.algorithm.Transactions.CreateOrder(self.symbol2, 50, self.algorithm.Time)
        
        self.order_tracker.on_order_event(create_filled_order_event(exit_order1))
        self.order_tracker.on_order_event(create_filled_order_event(exit_order2))
        
        # 验证平仓时间记录
        recorded_exit_time = self.order_tracker.get_pair_exit_time(self.symbol1, self.symbol2)
        self.assertEqual(recorded_exit_time, exit_time)
        
        # 回到现在，验证冷却期
        self.algorithm.SetTime(original_time)
        
        # OrderTracker应该显示在冷却期内（5天 < 7天）
        is_cooldown = self.order_tracker.is_in_cooldown(self.symbol1, self.symbol2, 7)
        self.assertTrue(is_cooldown)
        
        # 清空持仓
        self.algorithm.Portfolio[self.symbol1] = MockHolding(self.symbol1, False, 0, 0, 100)
        self.algorithm.Portfolio[self.symbol2] = MockHolding(self.symbol2, False, 0, 0, 100)
        
        # RiskManagement应该过滤新建仓信号
        new_targets = [
            MockPortfolioTarget(self.symbol1, 100),
            MockPortfolioTarget(self.symbol2, -50)
        ]
        
        filtered = self.risk_manager.ManageRisk(self.algorithm, new_targets)
        self.assertEqual(len(filtered), 0, "冷却期内的信号应该被过滤")
        
    def test_abnormal_pairs_info_transfer(self):
        """测试异常配对信息的传递"""
        self.pair_registry.update_pairs([(self.symbol1, self.symbol2)])
        
        # 创建异常订单（只有一边成交）
        order1 = self.algorithm.Transactions.CreateOrder(self.symbol1, 100, self.algorithm.Time)
        order2 = self.algorithm.Transactions.CreateOrder(self.symbol2, -50, self.algorithm.Time)
        
        # 两边都提交
        from tests.mocks.mock_quantconnect import create_submitted_order_event
        self.order_tracker.on_order_event(create_submitted_order_event(order1))
        self.order_tracker.on_order_event(create_submitted_order_event(order2))
        
        # 只有一边成交
        self.order_tracker.on_order_event(create_filled_order_event(order1))
        
        # v3.5.0: 设置Portfolio中的持仓状态，模拟symbol1成交后有持仓
        self.algorithm.Portfolio[self.symbol1] = MockHolding(self.symbol1, True, 100, 100, 100)
        
        # OrderTracker应该检测到异常
        abnormal_pairs = self.order_tracker.get_abnormal_pairs()
        self.assertEqual(len(abnormal_pairs), 1)
        self.assertEqual(abnormal_pairs[0], (self.symbol1, self.symbol2))
        
        # RiskManagement执行检查
        self.risk_manager.ManageRisk(self.algorithm, [])
        
        # 验证RiskManagement通过_check_abnormal_orders方法获取了信息
        # （通过debug信息间接验证，实际应该看到相关日志）
        # v3.5.0: 更新期望的日志消息格式
        has_abnormal_detection = any(("OrderTracker报告" in msg and "潜在异常配对" in msg) or
                                    ("确认异常配对" in msg and "有持仓" in msg)
                                    for msg in self.algorithm.debug_messages)
        self.assertTrue(has_abnormal_detection, "应该检测到异常配对")
        

if __name__ == '__main__':
    unittest.main()