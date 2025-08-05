"""
多重风控场景测试
测试多个风控条件同时触发的情况
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


class TestRiskMultipleScenarios(unittest.TestCase):
    """测试多重风控场景"""
    
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
        self.symbols = {
            'AAPL': MockSymbol("AAPL"),
            'MSFT': MockSymbol("MSFT"),
            'GOOGL': MockSymbol("GOOGL"),
            'AMZN': MockSymbol("AMZN"),
            'TSLA': MockSymbol("TSLA"),
            'META': MockSymbol("META")
        }
        
    def test_multiple_risk_triggers_same_pair(self):
        """测试同一配对同时触发多个风控条件"""
        # 设置配对
        symbol1, symbol2 = self.symbols['AAPL'], self.symbols['MSFT']
        self.pair_registry.update_pairs([(symbol1, symbol2)])
        
        # 35天前建仓（触发超时）
        original_time = self.algorithm.Time
        self.algorithm.SetTime(original_time - timedelta(days=35))
        
        # 建仓
        self.algorithm.Portfolio[symbol1] = MockHolding(symbol1, False, 0, 0, 100)
        self.algorithm.Portfolio[symbol2] = MockHolding(symbol2, False, 0, 0, 100)
        
        order1 = self.algorithm.Transactions.CreateOrder(symbol1, 100, self.algorithm.Time)
        order2 = self.algorithm.Transactions.CreateOrder(symbol2, -50, self.algorithm.Time)
        
        self.order_tracker.on_order_event(create_filled_order_event(order1))
        self.order_tracker.on_order_event(create_filled_order_event(order2))
        
        # 回到现在
        self.algorithm.SetTime(original_time)
        
        # 设置持仓：既超时又亏损严重
        # symbol1亏损25%（触发单边止损）
        # 配对整体亏损15%（触发配对止损）
        self.algorithm.Portfolio[symbol1] = MockHolding(symbol1, True, 100, 100, 75)  # 亏损25%
        self.algorithm.Portfolio[symbol2] = MockHolding(symbol2, True, -50, 100, 90)  # 做空亏损10%
        
        # 执行风控
        targets = self.risk_manager.ManageRisk(self.algorithm, [])
        
        # 应该生成平仓指令
        self.assertEqual(len(targets), 2)
        
        # 验证风控统计 - 只应该记录一次（最先触发的）
        stats = self.risk_manager.get_statistics()
        total_triggers = stats['holding_timeout'] + stats['pair_stop_loss'] + stats['single_stop_loss']
        self.assertEqual(total_triggers, 1, "同一配对只应记录一次风控触发")
        
        # 验证是超时触发（因为在代码中是第一个检查的）
        self.assertEqual(stats['holding_timeout'], 1)
        
    def test_risk_priority_order(self):
        """测试风控检查的优先级顺序"""
        # 测试单个配对触发超时
        self.pair_registry.update_pairs([(self.symbols['AAPL'], self.symbols['MSFT'])])
        self._create_position(self.symbols['AAPL'], self.symbols['MSFT'], 35, 0.05, 0.05)
        
        targets = self.risk_manager.ManageRisk(self.algorithm, [])
        self.assertEqual(len(targets), 2)
        stats = self.risk_manager.get_statistics()
        self.assertEqual(stats['holding_timeout'], 1)
        
        # 重新初始化测试配对止损
        self.setUp()
        self.pair_registry.update_pairs([(self.symbols['GOOGL'], self.symbols['AMZN'])])
        
        # 直接设置持仓状态以确保触发止损
        # GOOGL: 做多100股，成本100，现价85（亏损15%）
        # AMZN: 做空50股，成本100，现价100（不赚不亏）
        # 总成本 = 10000 + 5000 = 15000
        # 总亏损 = -1500 + 0 = -1500
        # 回撤率 = -1500/15000 = -10%（但要超过阈值）
        
        # 先建仓
        original_time = self.algorithm.Time
        self.algorithm.SetTime(original_time - timedelta(days=10))
        
        order1 = self.algorithm.Transactions.CreateOrder(self.symbols['GOOGL'], 100, self.algorithm.Time)
        order2 = self.algorithm.Transactions.CreateOrder(self.symbols['AMZN'], -50, self.algorithm.Time)
        
        self.order_tracker.on_order_event(create_filled_order_event(order1))
        self.order_tracker.on_order_event(create_filled_order_event(order2))
        
        self.algorithm.SetTime(original_time)
        
        # 设置持仓，让配对亏损超过10%
        self.algorithm.Portfolio[self.symbols['GOOGL']] = MockHolding(
            self.symbols['GOOGL'], True, 100, 100, 84  # 亏损16%
        )
        self.algorithm.Portfolio[self.symbols['AMZN']] = MockHolding(
            self.symbols['AMZN'], True, -50, 100, 100  # 不赚不亏
        )
        
        targets = self.risk_manager.ManageRisk(self.algorithm, [])
        self.assertEqual(len(targets), 2)
        stats = self.risk_manager.get_statistics()
        self.assertEqual(stats['pair_stop_loss'], 1)
        
        # 重新初始化测试单边止损
        self.setUp()
        self.pair_registry.update_pairs([(self.symbols['TSLA'], self.symbols['META'])])
        
        # 直接设置持仓以确保触发单边止损
        # TSLA: 做多100股，亏损25%（超过20%阈值）
        # META: 做空50股，盈利15%（保证配对整体不触发配对止损）
        
        # 先建仓
        original_time = self.algorithm.Time
        self.algorithm.SetTime(original_time - timedelta(days=5))
        
        order1 = self.algorithm.Transactions.CreateOrder(self.symbols['TSLA'], 100, self.algorithm.Time)
        order2 = self.algorithm.Transactions.CreateOrder(self.symbols['META'], -50, self.algorithm.Time)
        
        self.order_tracker.on_order_event(create_filled_order_event(order1))
        self.order_tracker.on_order_event(create_filled_order_event(order2))
        
        self.algorithm.SetTime(original_time)
        
        # 设置持仓
        # TSLA: 成本100，现价75（亏损25%）
        # META: 成本100，现价70（价格下降30%，做空盈利30%）
        # 这样配对整体回撤 = (-2500 + 1500) / 15000 = -1000/15000 = -6.7% < 10%
        # MockHolding参数: symbol, invested, quantity, average_price, price
        self.algorithm.Portfolio[self.symbols['TSLA']] = MockHolding(
            self.symbols['TSLA'], True, 100, 100, 75  # 亏损25%
        )
        self.algorithm.Portfolio[self.symbols['META']] = MockHolding(
            self.symbols['META'], True, -50, 100, 70  # 做空盈利30%
        )
        
        targets = self.risk_manager.ManageRisk(self.algorithm, [])
        self.assertEqual(len(targets), 2, f"单边止损应该触发，实际返回{len(targets)}个目标")
        stats = self.risk_manager.get_statistics()
        self.assertEqual(stats['single_stop_loss'], 1)
        
    def _create_position(self, symbol1, symbol2, days_ago, symbol1_loss, symbol2_pnl):
        """辅助方法：创建特定条件的持仓"""
        original_time = self.algorithm.Time
        
        # 回到N天前
        self.algorithm.SetTime(original_time - timedelta(days=days_ago))
        
        # 建仓前空持仓
        self.algorithm.Portfolio[symbol1] = MockHolding(symbol1, False, 0, 0, 100)
        self.algorithm.Portfolio[symbol2] = MockHolding(symbol2, False, 0, 0, 100)
        
        # 建仓
        order1 = self.algorithm.Transactions.CreateOrder(symbol1, 100, self.algorithm.Time)
        order2 = self.algorithm.Transactions.CreateOrder(symbol2, -50, self.algorithm.Time)
        
        self.order_tracker.on_order_event(create_filled_order_event(order1))
        self.order_tracker.on_order_event(create_filled_order_event(order2))
        
        # 回到现在
        self.algorithm.SetTime(original_time)
        
        # 设置当前价格
        symbol1_price = 100 * (1 - symbol1_loss)
        # symbol2是做空，如果symbol2_pnl为正（盈利），价格需要下跌
        # 如果symbol2_pnl为负（亏损），价格需要上涨
        symbol2_price = 100 * (1 - symbol2_pnl)
        
        self.algorithm.Portfolio[symbol1] = MockHolding(symbol1, True, 100, 100, symbol1_price)
        self.algorithm.Portfolio[symbol2] = MockHolding(symbol2, True, -50, 100, symbol2_price)
        
    def test_concurrent_pairs_different_risks(self):
        """测试多个配对同时触发不同风控"""
        # 设置2个配对，一个正常，一个触发风控
        pairs = [
            (self.symbols['AAPL'], self.symbols['MSFT']),    # 正常
            (self.symbols['GOOGL'], self.symbols['AMZN'])    # 超时
        ]
        self.pair_registry.update_pairs(pairs)
        
        # 配对1：正常持仓（10天，小幅盈利）
        self._create_position(pairs[0][0], pairs[0][1], 10, -0.05, -0.03)
        
        # 配对2：超时（35天）
        self._create_position(pairs[1][0], pairs[1][1], 35, 0.08, 0.05)
        
        # 执行风控
        targets = self.risk_manager.ManageRisk(self.algorithm, [])
        
        # 只有一个配对被平仓
        self.assertEqual(len(targets), 2)  # 1个配对 × 2只股票
        
        # 验证目标股票
        target_symbols = {t.Symbol for t in targets}
        self.assertIn(pairs[1][0], target_symbols)
        self.assertIn(pairs[1][1], target_symbols)
        
        
    def test_risk_with_pending_targets(self):
        """测试风控与现有交易信号的交互"""
        # 设置两个配对
        pair1 = (self.symbols['AAPL'], self.symbols['MSFT'])
        pair2 = (self.symbols['GOOGL'], self.symbols['AMZN'])
        self.pair_registry.update_pairs([pair1, pair2])
        
        # 配对1：需要风控平仓（超时）
        self._create_position(pair1[0], pair1[1], 35, 0.05, 0.05)
        
        # 配对2：正常持仓
        self._create_position(pair2[0], pair2[1], 10, -0.03, -0.02)
        
        # 假设PC模块生成了新的调仓信号（不应该有，但测试冲突处理）
        original_targets = [
            MockPortfolioTarget(pair1[0], 150),  # 尝试加仓AAPL
            MockPortfolioTarget(pair1[1], -75),  # 尝试加仓MSFT
            MockPortfolioTarget(pair2[0], 110),  # 调整GOOGL
            MockPortfolioTarget(pair2[1], -55)   # 调整AMZN
        ]
        
        # 执行风控
        risk_adjusted_targets = self.risk_manager.ManageRisk(self.algorithm, original_targets)
        
        # 验证结果
        # 配对1应该被平仓（覆盖原始信号）
        # 配对2的调仓信号应该保留
        self.assertEqual(len(risk_adjusted_targets), 4)
        
        # 检查具体目标
        targets_dict = {t.Symbol: t.Quantity for t in risk_adjusted_targets}
        
        # 配对1应该是平仓（Quantity=0）
        self.assertEqual(targets_dict[pair1[0]], 0)
        self.assertEqual(targets_dict[pair1[1]], 0)
        
        # 配对2应该保留原始信号
        self.assertEqual(targets_dict[pair2[0]], 110)
        self.assertEqual(targets_dict[pair2[1]], -55)
        
    def test_same_direction_error_priority(self):
        """测试同向持仓错误的优先处理"""
        # 设置配对
        symbol1, symbol2 = self.symbols['AAPL'], self.symbols['MSFT']
        self.pair_registry.update_pairs([(symbol1, symbol2)])
        
        # 35天前建仓（同时满足超时）
        original_time = self.algorithm.Time
        self.algorithm.SetTime(original_time - timedelta(days=35))
        
        # 模拟订单
        order1 = self.algorithm.Transactions.CreateOrder(symbol1, 100, self.algorithm.Time)
        order2 = self.algorithm.Transactions.CreateOrder(symbol2, -50, self.algorithm.Time)
        
        self.order_tracker.on_order_event(create_filled_order_event(order1))
        self.order_tracker.on_order_event(create_filled_order_event(order2))
        
        # 回到现在
        self.algorithm.SetTime(original_time)
        
        # 设置同向持仓（都是做多） - 这是严重错误
        self.algorithm.Portfolio[symbol1] = MockHolding(symbol1, True, 100, 100, 90)
        self.algorithm.Portfolio[symbol2] = MockHolding(symbol2, True, 50, 100, 95)  # 注意是正数
        
        # 执行风控
        targets = self.risk_manager.ManageRisk(self.algorithm, [])
        
        # 应该立即平仓
        self.assertEqual(len(targets), 2)
        
        # 验证是因为配对异常而不是超时
        # 检查debug消息
        debug_messages = self.algorithm.debug_messages
        # 应该有提到same_direction_error
        same_direction_found = any("same_direction_error" in msg for msg in debug_messages)
        self.assertTrue(same_direction_found, "应该检测到同向持仓错误")
        
    def test_multiple_pairs_with_cooldown(self):
        """测试多配对中部分在冷却期的情况"""
        # 简化测试：只测试冷却期过滤
        pairs = [
            (self.symbols['AAPL'], self.symbols['MSFT']),    # 冷却期中
            (self.symbols['GOOGL'], self.symbols['AMZN'])    # 正常持仓
        ]
        self.pair_registry.update_pairs(pairs)
        
        # 配对1：5天前平仓（在冷却期）
        self._create_and_close_position(pairs[0][0], pairs[0][1], 15, 5)
        
        # 配对2：正常持仓
        self._create_position(pairs[1][0], pairs[1][1], 10, -0.03, -0.02)
        
        # 创建新信号
        new_targets = [
            # 尝试重新建仓配对1（应该被过滤）
            MockPortfolioTarget(pairs[0][0], 100),
            MockPortfolioTarget(pairs[0][1], -50),
            # 配对2调仓（应该保留）
            MockPortfolioTarget(pairs[1][0], 210),
            MockPortfolioTarget(pairs[1][1], -105)
        ]
        
        # 执行风控
        risk_adjusted_targets = self.risk_manager.ManageRisk(self.algorithm, new_targets)
        
        # 验证结果
        target_dict = {t.Symbol: t.Quantity for t in risk_adjusted_targets}
        
        # 配对1不应该出现（被冷却期过滤）
        self.assertNotIn(pairs[0][0], target_dict)
        self.assertNotIn(pairs[0][1], target_dict)
        
        # 配对2应该保留调仓信号
        self.assertEqual(target_dict[pairs[1][0]], 210)
        self.assertEqual(target_dict[pairs[1][1]], -105)
        
    def _create_and_close_position(self, symbol1, symbol2, entry_days_ago, exit_days_ago):
        """辅助方法：创建并平仓一个配对"""
        original_time = self.algorithm.Time
        
        # 建仓
        self.algorithm.SetTime(original_time - timedelta(days=entry_days_ago))
        
        self.algorithm.Portfolio[symbol1] = MockHolding(symbol1, False, 0, 0, 100)
        self.algorithm.Portfolio[symbol2] = MockHolding(symbol2, False, 0, 0, 100)
        
        entry_order1 = self.algorithm.Transactions.CreateOrder(symbol1, 100, self.algorithm.Time)
        entry_order2 = self.algorithm.Transactions.CreateOrder(symbol2, -50, self.algorithm.Time)
        
        self.order_tracker.on_order_event(create_filled_order_event(entry_order1))
        self.order_tracker.on_order_event(create_filled_order_event(entry_order2))
        
        # 设置持仓
        self.algorithm.Portfolio[symbol1] = MockHolding(symbol1, True, 100, 100, 100)
        self.algorithm.Portfolio[symbol2] = MockHolding(symbol2, True, -50, 100, 100)
        
        # 平仓
        self.algorithm.SetTime(original_time - timedelta(days=exit_days_ago))
        
        exit_order1 = self.algorithm.Transactions.CreateOrder(symbol1, -100, self.algorithm.Time)
        exit_order2 = self.algorithm.Transactions.CreateOrder(symbol2, 50, self.algorithm.Time)
        
        self.order_tracker.on_order_event(create_filled_order_event(exit_order1))
        self.order_tracker.on_order_event(create_filled_order_event(exit_order2))
        
        # 清空持仓
        self.algorithm.Portfolio[symbol1] = MockHolding(symbol1, False, 0, 0, 100)
        self.algorithm.Portfolio[symbol2] = MockHolding(symbol2, False, 0, 0, 100)
        
        # 回到现在
        self.algorithm.SetTime(original_time)
        

if __name__ == '__main__':
    unittest.main()