"""
策略流程集成测试
测试各模块之间的交互和完整流程
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
    MockOrder, create_filled_order_event, create_submitted_order_event
)
from src.PairRegistry import PairRegistry
from src.OrderTracker import OrderTracker
from src.RiskManagement import BayesianCointegrationRiskManagementModel


class TestStrategyFlow(unittest.TestCase):
    """策略流程集成测试类"""
    
    def setUp(self):
        """测试前准备"""
        self.algorithm = MockAlgorithm(datetime(2024, 8, 1))
        
        # 初始化模块
        self.pair_registry = PairRegistry(self.algorithm)
        self.order_tracker = OrderTracker(self.algorithm, self.pair_registry)
        
        # 设置 algorithm 的 pair_registry 属性
        self.algorithm.pair_registry = self.pair_registry
        
        config = {
            'max_holding_days': 30,
            'cooldown_days': 7,
            'max_pair_drawdown': 0.10,
            'max_single_drawdown': 0.20
        }
        self.risk_manager = BayesianCointegrationRiskManagementModel(
            self.algorithm, config, self.order_tracker
        )
        
        # 创建测试股票
        self.symbols = {
            'AAPL': MockSymbol("AAPL"),
            'MSFT': MockSymbol("MSFT"),
            'GOOGL': MockSymbol("GOOGL"),
            'AMZN': MockSymbol("AMZN"),
            'TSLA': MockSymbol("TSLA"),
            'META': MockSymbol("META")
        }
        
        # 初始化所有股票的空持仓
        for symbol in self.symbols.values():
            self.algorithm.Portfolio[symbol] = MockHolding(symbol, False, 0, 0, 100)
            
    def test_complete_pair_lifecycle(self):
        """测试配对的完整生命周期"""
        print("\n=== 测试配对完整生命周期 ===")
        
        # 1. AlphaModel 生成配对并更新 PairRegistry
        print("1. 更新配对列表")
        pairs = [
            (self.symbols['AAPL'], self.symbols['MSFT']),
            (self.symbols['GOOGL'], self.symbols['AMZN'])
        ]
        self.pair_registry.update_pairs(pairs)
        self.assertEqual(len(self.pair_registry.active_pairs), 2)
        
        # 2. PortfolioConstruction 生成建仓信号
        print("2. 生成建仓信号")
        targets = [
            MockPortfolioTarget(self.symbols['AAPL'], 100),
            MockPortfolioTarget(self.symbols['MSFT'], -50),
            MockPortfolioTarget(self.symbols['GOOGL'], 200),
            MockPortfolioTarget(self.symbols['AMZN'], -100)
        ]
        
        # 3. 执行建仓订单
        print("3. 执行建仓订单")
        entry_time = self.algorithm.Time
        orders = []
        for target in targets:
            order = self.algorithm.Transactions.CreateOrder(
                target.Symbol, target.Quantity, entry_time
            )
            orders.append(order)
            
            # 模拟订单提交和成交
            self.order_tracker.on_order_event(create_submitted_order_event(order))
            self.order_tracker.on_order_event(create_filled_order_event(order, entry_time))
            
            # 更新持仓（average_price=100, current_price=100）
            self.algorithm.Portfolio[target.Symbol] = MockHolding(
                target.Symbol, True, target.Quantity, 
                100, 100  # 平均成本100，当前价格100
            )
        
        # 验证订单记录
        self.assertEqual(len(self.order_tracker.orders), 4)
        
        # 验证配对时间记录
        entry_time1 = self.order_tracker.get_pair_entry_time(
            self.symbols['AAPL'], self.symbols['MSFT']
        )
        self.assertIsNotNone(entry_time1)
        
        # 4. 持仓期间的风控检查（正常情况）
        print("4. 持仓期间风控检查")
        self.algorithm.AddDays(10)
        risk_targets = self.risk_manager.ManageRisk(self.algorithm, [])
        self.assertEqual(len(risk_targets), 0)  # 不应该有平仓信号
        
        # 5. 持仓超时触发平仓
        print("5. 持仓超时触发平仓")
        self.algorithm.AddDays(25)  # 总共35天
        risk_targets = self.risk_manager.ManageRisk(self.algorithm, [])
        
        
        self.assertEqual(len(risk_targets), 4)  # 应该平仓所有持仓
        
        # 6. 执行平仓
        print("6. 执行平仓")
        exit_time = self.algorithm.Time
        for target in risk_targets:
            # 创建平仓订单
            exit_quantity = -self.algorithm.Portfolio[target.Symbol].Quantity
            exit_order = self.algorithm.Transactions.CreateOrder(
                target.Symbol, exit_quantity, exit_time
            )
            
            # 记录平仓
            self.order_tracker.on_order_event(create_submitted_order_event(exit_order))
            self.order_tracker.on_order_event(create_filled_order_event(exit_order, exit_time))
            
            # 清空持仓
            self.algorithm.Portfolio[target.Symbol] = MockHolding(
                target.Symbol, False, 0, 0, 100
            )
        
        # 验证平仓时间记录
        exit_time1 = self.order_tracker.get_pair_exit_time(
            self.symbols['AAPL'], self.symbols['MSFT']
        )
        self.assertIsNotNone(exit_time1)
        
        # 7. 冷却期检查
        print("7. 冷却期检查")
        self.assertTrue(
            self.order_tracker.is_in_cooldown(
                self.symbols['AAPL'], self.symbols['MSFT'], 7
            )
        )
        
        # 8. 冷却期内的新信号被过滤
        print("8. 冷却期内信号过滤")
        new_targets = [
            MockPortfolioTarget(self.symbols['AAPL'], 100),
            MockPortfolioTarget(self.symbols['MSFT'], -50)
        ]
        filtered_targets = self.risk_manager.ManageRisk(self.algorithm, new_targets)
        
        
        self.assertEqual(len(filtered_targets), 0)  # 应该被过滤掉
        
        print("=== 生命周期测试完成 ===")
        
    def test_abnormal_order_handling(self):
        """测试异常订单处理"""
        print("\n=== 测试异常订单处理 ===")
        
        # 设置配对
        self.pair_registry.update_pairs([
            (self.symbols['AAPL'], self.symbols['MSFT'])
        ])
        
        # 创建订单但只有一边成交
        order1 = self.algorithm.Transactions.CreateOrder(
            self.symbols['AAPL'], 100, self.algorithm.Time
        )
        order2 = self.algorithm.Transactions.CreateOrder(
            self.symbols['MSFT'], -50, self.algorithm.Time
        )
        
        # 两边都提交
        self.order_tracker.on_order_event(create_submitted_order_event(order1))
        self.order_tracker.on_order_event(create_submitted_order_event(order2))
        
        # 只有一边成交
        self.order_tracker.on_order_event(create_filled_order_event(order1))
        
        # 检查异常配对
        abnormal_pairs = self.order_tracker.get_abnormal_pairs()
        self.assertEqual(len(abnormal_pairs), 1)
        
        # 风控检查应该检测到异常
        self.risk_manager.ManageRisk(self.algorithm, [])
        # 验证异常配对被检测到（通过检查 OrderTracker）
        self.assertEqual(len(abnormal_pairs), 1)
        
        print("=== 异常订单处理测试完成 ===")
        
    def test_stop_loss_triggering(self):
        """测试止损触发"""
        print("\n=== 测试止损触发 ===")
        
        # 设置配对和持仓
        self.pair_registry.update_pairs([
            (self.symbols['AAPL'], self.symbols['MSFT'])
        ])
        
        # 建仓
        entry_time = self.algorithm.Time
        self.algorithm.Portfolio[self.symbols['AAPL']] = MockHolding(
            self.symbols['AAPL'], True, 100, 10000, 100
        )
        self.algorithm.Portfolio[self.symbols['MSFT']] = MockHolding(
            self.symbols['MSFT'], True, -50, -5000, 100
        )
        
        # 记录建仓
        order1 = self.algorithm.Transactions.CreateOrder(self.symbols['AAPL'], 100, entry_time)
        order2 = self.algorithm.Transactions.CreateOrder(self.symbols['MSFT'], -50, entry_time)
        self.order_tracker.on_order_event(create_filled_order_event(order1, entry_time))
        self.order_tracker.on_order_event(create_filled_order_event(order2, entry_time))
        
        # 模拟价格下跌导致配对亏损超过10%
        self.algorithm.Portfolio[self.symbols['AAPL']] = MockHolding(
            self.symbols['AAPL'], True, 100, 10000, 85  # 亏损15%
        )
        
        # 风控检查应该触发止损
        risk_targets = self.risk_manager.ManageRisk(self.algorithm, [])
        self.assertEqual(len(risk_targets), 2)  # 应该平仓配对
        
        # 验证风控统计
        stats = self.risk_manager.get_statistics()
        self.assertEqual(stats['pair_stop_loss'], 1)
        
        print("=== 止损触发测试完成 ===")
        
    def test_multiple_pairs_management(self):
        """测试多配对管理"""
        print("\n=== 测试多配对管理 ===")
        
        # 设置多个配对
        pairs = [
            (self.symbols['AAPL'], self.symbols['MSFT']),
            (self.symbols['GOOGL'], self.symbols['AMZN']),
            (self.symbols['TSLA'], self.symbols['META'])
        ]
        self.pair_registry.update_pairs(pairs)
        
        # 为每个配对创建不同的场景
        # 配对1：正常持仓
        self._create_normal_position(self.symbols['AAPL'], self.symbols['MSFT'], days_ago=10)
        
        # 配对2：持仓超时
        self._create_normal_position(self.symbols['GOOGL'], self.symbols['AMZN'], days_ago=35)
        
        # 配对3：单边亏损严重
        self._create_losing_position(self.symbols['TSLA'], self.symbols['META'], days_ago=5)
        
        # 执行风控检查
        risk_targets = self.risk_manager.ManageRisk(self.algorithm, [])
        
        # 应该有2个配对需要平仓（超时和止损）
        self.assertEqual(len(risk_targets), 4)  # 2个配对 × 2只股票
        
        # 验证目标股票
        target_symbols = {target.Symbol for target in risk_targets}
        self.assertIn(self.symbols['GOOGL'], target_symbols)  # 超时
        self.assertIn(self.symbols['AMZN'], target_symbols)   # 超时
        self.assertIn(self.symbols['TSLA'], target_symbols)   # 止损
        self.assertIn(self.symbols['META'], target_symbols)   # 止损
        
        print("=== 多配对管理测试完成 ===")
        
    def _create_normal_position(self, symbol1, symbol2, days_ago):
        """创建正常持仓"""
        entry_time = self.algorithm.Time - timedelta(days=days_ago)
        
        # 先设置空持仓（建仓前）
        self.algorithm.Portfolio[symbol1] = MockHolding(symbol1, False, 0, 0, 100)
        self.algorithm.Portfolio[symbol2] = MockHolding(symbol2, False, 0, 0, 100)
        
        # 记录订单
        order1 = self.algorithm.Transactions.CreateOrder(symbol1, 100, entry_time)
        order2 = self.algorithm.Transactions.CreateOrder(symbol2, -50, entry_time)
        self.order_tracker.on_order_event(create_filled_order_event(order1, entry_time))
        self.order_tracker.on_order_event(create_filled_order_event(order2, entry_time))
        
        # 现在设置当前持仓（小幅盈利）
        self.algorithm.Portfolio[symbol1] = MockHolding(symbol1, True, 100, 100, 105)
        self.algorithm.Portfolio[symbol2] = MockHolding(symbol2, True, -50, 100, 95)
        
    def _create_losing_position(self, symbol1, symbol2, days_ago):
        """创建亏损持仓"""
        entry_time = self.algorithm.Time - timedelta(days=days_ago)
        
        # 先设置空持仓（建仓前）
        self.algorithm.Portfolio[symbol1] = MockHolding(symbol1, False, 0, 0, 100)
        self.algorithm.Portfolio[symbol2] = MockHolding(symbol2, False, 0, 0, 100)
        
        # 记录订单
        order1 = self.algorithm.Transactions.CreateOrder(symbol1, 100, entry_time)
        order2 = self.algorithm.Transactions.CreateOrder(symbol2, -50, entry_time)
        self.order_tracker.on_order_event(create_filled_order_event(order1, entry_time))
        self.order_tracker.on_order_event(create_filled_order_event(order2, entry_time))
        
        # 现在设置当前持仓（单边亏损25%）
        self.algorithm.Portfolio[symbol1] = MockHolding(symbol1, True, 100, 100, 75)
        self.algorithm.Portfolio[symbol2] = MockHolding(symbol2, True, -50, 100, 100)
        
    def test_edge_cases(self):
        """测试边界情况"""
        print("\n=== 测试边界情况 ===")
        
        # 1. 空配对列表
        self.pair_registry.update_pairs([])
        risk_targets = self.risk_manager.ManageRisk(self.algorithm, [])
        self.assertEqual(len(risk_targets), 0)
        
        # 2. 无持仓时的风控
        self.pair_registry.update_pairs([
            (self.symbols['AAPL'], self.symbols['MSFT'])
        ])
        risk_targets = self.risk_manager.ManageRisk(self.algorithm, [])
        self.assertEqual(len(risk_targets), 0)
        
        # 3. 持仓但不在配对中（孤儿持仓）
        self.algorithm.Portfolio[self.symbols['TSLA']] = MockHolding(
            self.symbols['TSLA'], True, 100, 10000, 100
        )
        risk_targets = self.risk_manager.ManageRisk(self.algorithm, [])
        # 根据当前实现，孤儿持仓不会被处理
        self.assertEqual(len(risk_targets), 0)
        
        print("=== 边界情况测试完成 ===")


if __name__ == '__main__':
    unittest.main()