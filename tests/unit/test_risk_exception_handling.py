"""
异常处理测试
测试RiskManagement模块在各种异常情况下的鲁棒性
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


class TestRiskExceptionHandling(unittest.TestCase):
    """测试风控异常处理"""
    
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
        
    def test_empty_portfolio(self):
        """测试空持仓情况"""
        # 不设置任何持仓
        targets = self.risk_manager.ManageRisk(self.algorithm, [])
        self.assertEqual(len(targets), 0)
        
        # 验证没有错误
        self.assertFalse(any("错误" in msg for msg in self.algorithm.debug_messages))
        
    def test_missing_price_data(self):
        """测试缺失价格数据的情况"""
        self.pair_registry.update_pairs([(self.symbol1, self.symbol2)])
        
        # 建仓
        order1 = self.algorithm.Transactions.CreateOrder(self.symbol1, 100, self.algorithm.Time)
        order2 = self.algorithm.Transactions.CreateOrder(self.symbol2, -50, self.algorithm.Time)
        
        self.order_tracker.on_order_event(create_filled_order_event(order1))
        self.order_tracker.on_order_event(create_filled_order_event(order2))
        
        # 设置持仓但价格为0（模拟数据缺失）
        # 当价格为0时，会计算出100%的亏损，应该触发风控
        self.algorithm.Portfolio[self.symbol1] = MockHolding(self.symbol1, True, 100, 100, 0)
        self.algorithm.Portfolio[self.symbol2] = MockHolding(self.symbol2, True, -50, 100, 100)
        
        # 应该能够正常处理
        targets = self.risk_manager.ManageRisk(self.algorithm, [])
        # 价格为0会触发风控（100%亏损）
        self.assertEqual(len(targets), 2)
        
        # 验证风控被触发
        stats = self.risk_manager.get_statistics()
        self.assertGreater(stats['single_stop_loss'] + stats['pair_stop_loss'], 0)
        
    def test_zero_cost_position(self):
        """测试零成本持仓（如赠送股票）"""
        self.pair_registry.update_pairs([(self.symbol1, self.symbol2)])
        
        # 建仓
        order1 = self.algorithm.Transactions.CreateOrder(self.symbol1, 100, self.algorithm.Time)
        order2 = self.algorithm.Transactions.CreateOrder(self.symbol2, -50, self.algorithm.Time)
        
        self.order_tracker.on_order_event(create_filled_order_event(order1))
        self.order_tracker.on_order_event(create_filled_order_event(order2))
        
        # 设置零成本持仓
        self.algorithm.Portfolio[self.symbol1] = MockHolding(self.symbol1, True, 100, 0, 100)
        self.algorithm.Portfolio[self.symbol2] = MockHolding(self.symbol2, True, -50, 100, 100)
        
        # 应该能够正常处理
        targets = self.risk_manager.ManageRisk(self.algorithm, [])
        # 不应该因为除零错误崩溃
        self.assertIsInstance(targets, list)
        
    def test_invalid_target_input(self):
        """测试无效的target输入"""
        # 测试空列表输入（RiskManagement当前不处理None）
        targets = self.risk_manager.ManageRisk(self.algorithm, [])
        self.assertEqual(len(targets), 0)
        
        # 测试正常的target列表
        valid_targets = [
            MockPortfolioTarget(self.symbol1, 100),
            MockPortfolioTarget(self.symbol2, -50)
        ]
        targets = self.risk_manager.ManageRisk(self.algorithm, valid_targets)
        # 应该返回原始targets（没有风控触发）
        self.assertEqual(len(targets), 2)
        
    def test_duplicate_symbols_in_targets(self):
        """测试目标列表中有重复股票"""
        duplicate_targets = [
            MockPortfolioTarget(self.symbol1, 100),
            MockPortfolioTarget(self.symbol1, 200),  # 重复
            MockPortfolioTarget(self.symbol2, -50)
        ]
        
        targets = self.risk_manager.ManageRisk(self.algorithm, duplicate_targets)
        # 应该能处理重复（取最后一个或合并）
        self.assertIsInstance(targets, list)
        
    def test_extreme_market_conditions(self):
        """测试极端市场条件（如涨跌停）"""
        self.pair_registry.update_pairs([(self.symbol1, self.symbol2)])
        
        # 建仓
        order1 = self.algorithm.Transactions.CreateOrder(self.symbol1, 100, self.algorithm.Time)
        order2 = self.algorithm.Transactions.CreateOrder(self.symbol2, -50, self.algorithm.Time)
        
        self.order_tracker.on_order_event(create_filled_order_event(order1))
        self.order_tracker.on_order_event(create_filled_order_event(order2))
        
        # 设置极端价格变动（跌停）
        self.algorithm.Portfolio[self.symbol1] = MockHolding(self.symbol1, True, 100, 100, 10)  # 跌90%
        self.algorithm.Portfolio[self.symbol2] = MockHolding(self.symbol2, True, -50, 100, 200)  # 涨100%（做空大亏）
        
        # 应该触发风控
        targets = self.risk_manager.ManageRisk(self.algorithm, [])
        self.assertEqual(len(targets), 2)  # 应该平仓
        
        # 验证风控统计
        stats = self.risk_manager.get_statistics()
        self.assertGreater(stats['single_stop_loss'] + stats['pair_stop_loss'], 0)
        
    def test_partial_pair_in_portfolio(self):
        """测试只有配对一边在持仓中的情况"""
        self.pair_registry.update_pairs([(self.symbol1, self.symbol2)])
        
        # 设置两边持仓，但只有一边有实际持仓
        self.algorithm.Portfolio[self.symbol1] = MockHolding(self.symbol1, True, 100, 100, 90)
        self.algorithm.Portfolio[self.symbol2] = MockHolding(self.symbol2, False, 0, 0, 100)  # 没有实际持仓
        
        # 应该能够正常处理
        targets = self.risk_manager.ManageRisk(self.algorithm, [])
        # 不应该崩溃
        self.assertIsInstance(targets, list)
        
        # 不应该有活跃配对（因为只有一边有持仓）
        active_pairs = self.risk_manager._get_active_pairs()
        self.assertEqual(len(active_pairs), 0)
        
    def test_negative_quantity_handling(self):
        """测试负数量的处理（做空）"""
        self.pair_registry.update_pairs([(self.symbol1, self.symbol2)])
        
        # 建仓 - 两边都是做空
        order1 = self.algorithm.Transactions.CreateOrder(self.symbol1, -100, self.algorithm.Time)
        order2 = self.algorithm.Transactions.CreateOrder(self.symbol2, -50, self.algorithm.Time)
        
        self.order_tracker.on_order_event(create_filled_order_event(order1))
        self.order_tracker.on_order_event(create_filled_order_event(order2))
        
        # 设置两边都做空的持仓
        self.algorithm.Portfolio[self.symbol1] = MockHolding(self.symbol1, True, -100, 100, 110)
        self.algorithm.Portfolio[self.symbol2] = MockHolding(self.symbol2, True, -50, 100, 105)
        
        # 应该检测到同向持仓错误
        targets = self.risk_manager.ManageRisk(self.algorithm, [])
        
        # 检查是否检测到配对异常
        has_abnormal = any("配对异常" in msg for msg in self.algorithm.debug_messages)
        self.assertTrue(has_abnormal)
        
    def test_order_tracker_no_records(self):
        """测试OrderTracker没有记录的情况"""
        self.pair_registry.update_pairs([(self.symbol1, self.symbol2)])
        
        # 直接设置持仓，不通过OrderTracker
        self.algorithm.Portfolio[self.symbol1] = MockHolding(self.symbol1, True, 100, 100, 90)
        self.algorithm.Portfolio[self.symbol2] = MockHolding(self.symbol2, True, -50, 100, 100)
        
        # RiskManagement应该能处理（持仓天数为0）
        active_pairs = self.risk_manager._get_active_pairs()
        self.assertEqual(len(active_pairs), 1)
        self.assertEqual(active_pairs[0]['holding_days'], 0)
        
        # 不应该触发超时
        targets = self.risk_manager.ManageRisk(self.algorithm, [])
        stats = self.risk_manager.get_statistics()
        self.assertEqual(stats['holding_timeout'], 0)
        
    def test_concurrent_risk_calls(self):
        """测试同一天多次调用风控"""
        self.pair_registry.update_pairs([(self.symbol1, self.symbol2)])
        
        # 建仓
        order1 = self.algorithm.Transactions.CreateOrder(self.symbol1, 100, self.algorithm.Time)
        order2 = self.algorithm.Transactions.CreateOrder(self.symbol2, -50, self.algorithm.Time)
        
        self.order_tracker.on_order_event(create_filled_order_event(order1))
        self.order_tracker.on_order_event(create_filled_order_event(order2))
        
        # 设置亏损持仓
        self.algorithm.Portfolio[self.symbol1] = MockHolding(self.symbol1, True, 100, 100, 75)
        self.algorithm.Portfolio[self.symbol2] = MockHolding(self.symbol2, True, -50, 100, 100)
        
        # 第一次调用
        targets1 = self.risk_manager.ManageRisk(self.algorithm, [])
        stats1 = self.risk_manager.get_statistics()
        
        # 第二次调用（同一天）
        targets2 = self.risk_manager.ManageRisk(self.algorithm, [])
        stats2 = self.risk_manager.get_statistics()
        
        # 统计数据不应该重复计算
        self.assertEqual(stats1['single_stop_loss'], stats2['single_stop_loss'])
        
        # 日常检查计数应该只增加一次
        self.assertEqual(self.risk_manager.daily_check_count, 1)
        
    def test_invalid_pair_registry_state(self):
        """测试PairRegistry状态异常"""
        # 不设置任何配对
        # 但设置持仓
        self.algorithm.Portfolio[self.symbol1] = MockHolding(self.symbol1, True, 100, 100, 90)
        self.algorithm.Portfolio[self.symbol2] = MockHolding(self.symbol2, True, -50, 100, 100)
        
        # 应该能正常处理（找不到配对信息）
        active_pairs = self.risk_manager._get_active_pairs()
        self.assertEqual(len(active_pairs), 0)
        
        targets = self.risk_manager.ManageRisk(self.algorithm, [])
        # 不应该生成任何平仓指令
        self.assertEqual(len(targets), 0)
        

if __name__ == '__main__':
    unittest.main()