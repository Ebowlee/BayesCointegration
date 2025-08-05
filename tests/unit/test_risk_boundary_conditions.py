"""
风控边界条件测试
测试各种风控阈值的边界情况
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


class TestRiskBoundaryConditions(unittest.TestCase):
    """测试风控边界条件"""
    
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
        
    def _setup_fresh_test(self):
        """重新初始化测试环境"""
        # 重新创建模块实例
        self.algorithm = MockAlgorithm(datetime(2024, 8, 1))
        self.pair_registry = PairRegistry(self.algorithm)
        self.order_tracker = OrderTracker(self.algorithm, self.pair_registry)
        
        self.risk_manager = BayesianCointegrationRiskManagementModel(
            self.algorithm, self.config, self.order_tracker, self.pair_registry
        )
        
        # 设置配对
        self.pair_registry.update_pairs([(self.symbol1, self.symbol2)])
        
    def test_holding_timeout_boundary(self):
        """测试持仓超时的边界条件（29天、30天、31天）"""
        # 测试29天 - 不应该触发
        self._setup_fresh_test()
        self._test_holding_days(29, should_trigger=False)
        
        # 测试30天 - 不应该触发（等于阈值）
        self._setup_fresh_test()
        self._test_holding_days(30, should_trigger=False)
        
        # 测试31天 - 应该触发
        self._setup_fresh_test()
        self._test_holding_days(31, should_trigger=True)
        
    def _test_holding_days(self, days, should_trigger):
        """辅助方法：测试特定天数的持仓"""
        original_time = self.algorithm.Time
        
        # 回到N天前建仓
        self.algorithm.SetTime(original_time - timedelta(days=days))
        
        # 建仓
        self.algorithm.Portfolio[self.symbol1] = MockHolding(self.symbol1, False, 0, 0, 100)
        self.algorithm.Portfolio[self.symbol2] = MockHolding(self.symbol2, False, 0, 0, 100)
        
        order1 = self.algorithm.Transactions.CreateOrder(self.symbol1, 100, self.algorithm.Time)
        order2 = self.algorithm.Transactions.CreateOrder(self.symbol2, -50, self.algorithm.Time)
        
        self.order_tracker.on_order_event(create_filled_order_event(order1))
        self.order_tracker.on_order_event(create_filled_order_event(order2))
        
        # 回到现在
        self.algorithm.SetTime(original_time)
        
        # 设置持仓
        self.algorithm.Portfolio[self.symbol1] = MockHolding(self.symbol1, True, 100, 100, 100)
        self.algorithm.Portfolio[self.symbol2] = MockHolding(self.symbol2, True, -50, 100, 100)
        
        # 执行风控
        targets = self.risk_manager.ManageRisk(self.algorithm, [])
        
        if should_trigger:
            self.assertEqual(len(targets), 2, f"{days}天应该触发超时平仓")
            self.assertEqual(self.risk_manager.risk_triggers['holding_timeout'], 1)
        else:
            self.assertEqual(len(targets), 0, f"{days}天不应该触发超时平仓")
            self.assertEqual(self.risk_manager.risk_triggers['holding_timeout'], 0)
        
        # 清理（恢复初始状态）
        self.algorithm.Portfolio[self.symbol1] = MockHolding(self.symbol1, False, 0, 0, 100)
        self.algorithm.Portfolio[self.symbol2] = MockHolding(self.symbol2, False, 0, 0, 100)
        
    def test_pair_drawdown_boundary(self):
        """测试配对回撤的边界条件（9.9%、10%、10.1%）"""
        self.pair_registry.update_pairs([(self.symbol1, self.symbol2)])
        
        # 建仓
        order1 = self.algorithm.Transactions.CreateOrder(self.symbol1, 100, self.algorithm.Time)
        order2 = self.algorithm.Transactions.CreateOrder(self.symbol2, -50, self.algorithm.Time)
        
        self.order_tracker.on_order_event(create_filled_order_event(order1))
        self.order_tracker.on_order_event(create_filled_order_event(order2))
        
        # 测试9.9%回撤 - 不应该触发
        self._test_pair_drawdown(0.099, should_trigger=False)
        
        # 测试10.0%回撤 - 不应该触发（等于阈值）
        self._test_pair_drawdown(0.100, should_trigger=False)
        
        # 测试10.1%回撤 - 应该触发
        self._test_pair_drawdown(0.101, should_trigger=True)
        
    def _test_pair_drawdown(self, drawdown_rate, should_trigger):
        """辅助方法：测试特定回撤率"""
        # 计算目标价格
        # 假设symbol1成本100，数量100，symbol2成本100，数量-50
        # 总成本 = 10000 + 5000 = 15000
        # 目标亏损 = 15000 * drawdown_rate
        # 为简化，让symbol2价格不变，只调整symbol1价格
        target_loss = 15000 * drawdown_rate
        symbol1_new_price = 100 - (target_loss / 100)
        
        # 设置持仓
        self.algorithm.Portfolio[self.symbol1] = MockHolding(
            self.symbol1, True, 100, 100, symbol1_new_price
        )
        self.algorithm.Portfolio[self.symbol2] = MockHolding(
            self.symbol2, True, -50, 100, 100  # 价格不变
        )
        
        # 重置风控统计
        self.risk_manager.risk_triggers['pair_stop_loss'] = 0
        
        # 执行风控
        targets = self.risk_manager.ManageRisk(self.algorithm, [])
        
        if should_trigger:
            self.assertEqual(len(targets), 2, f"{drawdown_rate*100:.1f}%回撤应该触发止损")
            self.assertEqual(self.risk_manager.risk_triggers['pair_stop_loss'], 1)
        else:
            self.assertEqual(len(targets), 0, f"{drawdown_rate*100:.1f}%回撤不应该触发止损")
            self.assertEqual(self.risk_manager.risk_triggers['pair_stop_loss'], 0)
        
    def test_single_drawdown_boundary(self):
        """测试单边回撤的边界条件（19.9%、20%、20.1%）"""
        self.pair_registry.update_pairs([(self.symbol1, self.symbol2)])
        
        # 建仓
        order1 = self.algorithm.Transactions.CreateOrder(self.symbol1, 100, self.algorithm.Time)
        order2 = self.algorithm.Transactions.CreateOrder(self.symbol2, -50, self.algorithm.Time)
        
        self.order_tracker.on_order_event(create_filled_order_event(order1))
        self.order_tracker.on_order_event(create_filled_order_event(order2))
        
        # 测试19.9%单边回撤 - 不应该触发
        self._test_single_drawdown(0.199, should_trigger=False)
        
        # 测试20.0%单边回撤 - 不应该触发（等于阈值）
        self._test_single_drawdown(0.200, should_trigger=False)
        
        # 测试20.1%单边回撤 - 应该触发
        self._test_single_drawdown(0.201, should_trigger=True)
        
    def _test_single_drawdown(self, drawdown_rate, should_trigger):
        """辅助方法：测试特定单边回撤率"""
        # 设置symbol1亏损，symbol2小幅盈利（确保配对整体亏损<10%）
        symbol1_new_price = 100 * (1 - drawdown_rate)
        
        # 计算symbol2需要的盈利以保持配对亏损<10%
        # symbol1亏损 = 100 * drawdown_rate * 100
        # 要保持配对亏损<10%，symbol2需要盈利
        # 配对亏损 = (symbol1亏损 - symbol2盈利) / 15000 < 0.1
        # symbol2盈利 > symbol1亏损 - 1500
        symbol2_profit_needed = (100 * drawdown_rate * 100) - 1400  # 留一点余量
        symbol2_new_price = 100 - (symbol2_profit_needed / 50)  # 做空盈利
        
        # 设置持仓
        self.algorithm.Portfolio[self.symbol1] = MockHolding(
            self.symbol1, True, 100, 100, symbol1_new_price
        )
        self.algorithm.Portfolio[self.symbol2] = MockHolding(
            self.symbol2, True, -50, 100, symbol2_new_price
        )
        
        # 重置风控统计
        self.risk_manager.risk_triggers['single_stop_loss'] = 0
        
        # 执行风控
        targets = self.risk_manager.ManageRisk(self.algorithm, [])
        
        if should_trigger:
            self.assertEqual(len(targets), 2, f"{drawdown_rate*100:.1f}%单边回撤应该触发止损")
            self.assertEqual(self.risk_manager.risk_triggers['single_stop_loss'], 1)
        else:
            self.assertEqual(len(targets), 0, f"{drawdown_rate*100:.1f}%单边回撤不应该触发止损")
            self.assertEqual(self.risk_manager.risk_triggers['single_stop_loss'], 0)
        
    def test_cooldown_boundary(self):
        """测试冷却期的边界条件（第6天、第7天、第8天）"""
        # 测试平仓后6天 - 应该在冷却期
        self._setup_fresh_test()
        self._test_cooldown_days_simple(6, should_be_cooldown=True)
        
        # 测试平仓后7天 - 不应该在冷却期（使用 < 判断）
        self._setup_fresh_test()
        self._test_cooldown_days_simple(7, should_be_cooldown=False)
        
        # 测试平仓后8天 - 不应该在冷却期
        self._setup_fresh_test()
        self._test_cooldown_days_simple(8, should_be_cooldown=False)
        
    def _test_cooldown_days_simple(self, days_after_exit, should_be_cooldown):
        """辅助方法：简化的冷却期测试"""
        original_time = self.algorithm.Time
        
        # 20天前建仓
        self.algorithm.SetTime(original_time - timedelta(days=20))
        
        # 建仓
        self.algorithm.Portfolio[self.symbol1] = MockHolding(self.symbol1, False, 0, 0, 100)
        self.algorithm.Portfolio[self.symbol2] = MockHolding(self.symbol2, False, 0, 0, 100)
        
        order1 = self.algorithm.Transactions.CreateOrder(self.symbol1, 100, self.algorithm.Time)
        order2 = self.algorithm.Transactions.CreateOrder(self.symbol2, -50, self.algorithm.Time)
        
        self.order_tracker.on_order_event(create_filled_order_event(order1))
        self.order_tracker.on_order_event(create_filled_order_event(order2))
        
        # 设置持仓
        self.algorithm.Portfolio[self.symbol1] = MockHolding(self.symbol1, True, 100, 100, 100)
        self.algorithm.Portfolio[self.symbol2] = MockHolding(self.symbol2, True, -50, 100, 100)
        
        # days_after_exit天前平仓
        self.algorithm.SetTime(original_time - timedelta(days=days_after_exit))
        
        exit_order1 = self.algorithm.Transactions.CreateOrder(self.symbol1, -100, self.algorithm.Time)
        exit_order2 = self.algorithm.Transactions.CreateOrder(self.symbol2, 50, self.algorithm.Time)
        
        self.order_tracker.on_order_event(create_filled_order_event(exit_order1))
        self.order_tracker.on_order_event(create_filled_order_event(exit_order2))
        
        # 清空持仓
        self.algorithm.Portfolio[self.symbol1] = MockHolding(self.symbol1, False, 0, 0, 100)
        self.algorithm.Portfolio[self.symbol2] = MockHolding(self.symbol2, False, 0, 0, 100)
        
        # 回到现在
        self.algorithm.SetTime(original_time)
        
        # 检查冷却期状态
        is_cooldown = self.order_tracker.is_in_cooldown(self.symbol1, self.symbol2, 7)
        
        if should_be_cooldown:
            self.assertTrue(is_cooldown, f"平仓后第{days_after_exit}天应该在冷却期")
        else:
            self.assertFalse(is_cooldown, f"平仓后第{days_after_exit}天不应该在冷却期")
        
    def test_exact_threshold_combinations(self):
        """测试多个阈值同时达到边界的情况"""
        self.pair_registry.update_pairs([(self.symbol1, self.symbol2)])
        
        # 设置恰好30天前建仓
        original_time = self.algorithm.Time
        self.algorithm.SetTime(original_time - timedelta(days=30))
        
        # 建仓
        self.algorithm.Portfolio[self.symbol1] = MockHolding(self.symbol1, False, 0, 0, 100)
        self.algorithm.Portfolio[self.symbol2] = MockHolding(self.symbol2, False, 0, 0, 100)
        
        order1 = self.algorithm.Transactions.CreateOrder(self.symbol1, 100, self.algorithm.Time)
        order2 = self.algorithm.Transactions.CreateOrder(self.symbol2, -50, self.algorithm.Time)
        
        self.order_tracker.on_order_event(create_filled_order_event(order1))
        self.order_tracker.on_order_event(create_filled_order_event(order2))
        
        # 回到现在
        self.algorithm.SetTime(original_time)
        
        # 设置恰好10%的配对回撤
        # 总成本15000，亏损1500，symbol1亏损1500
        self.algorithm.Portfolio[self.symbol1] = MockHolding(
            self.symbol1, True, 100, 100, 85  # 亏损15元/股 = 1500总亏损
        )
        self.algorithm.Portfolio[self.symbol2] = MockHolding(
            self.symbol2, True, -50, 100, 100  # 不赚不亏
        )
        
        # 执行风控 - 两个阈值都刚好在边界上，都不应该触发
        targets = self.risk_manager.ManageRisk(self.algorithm, [])
        self.assertEqual(len(targets), 0, "边界值不应该触发风控")
        
        stats = self.risk_manager.get_statistics()
        self.assertEqual(stats['holding_timeout'], 0)
        self.assertEqual(stats['pair_stop_loss'], 0)
        

if __name__ == '__main__':
    unittest.main()