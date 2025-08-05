"""
RiskManagement 核心方法单元测试
测试风险管理模块的关键功能
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
    MockOrder, create_filled_order_event
)
from src.PairRegistry import PairRegistry
from src.OrderTracker import OrderTracker
from src.RiskManagement import BayesianCointegrationRiskManagementModel

# 为测试环境设置PortfolioTarget别名
import tests.mocks.mock_quantconnect as mock_qc
mock_qc.PortfolioTarget = MockPortfolioTarget


class TestRiskManagement(unittest.TestCase):
    """RiskManagement 核心方法测试类"""
    
    def setUp(self):
        """测试前准备"""
        self.algorithm = MockAlgorithm()
        self.pair_registry = PairRegistry(self.algorithm)
        self.order_tracker = OrderTracker(self.algorithm, self.pair_registry)
        
        # 设置 algorithm 的 pair_registry 属性
        self.algorithm.pair_registry = self.pair_registry
        
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
        
        # 创建测试用的股票
        self.symbol1 = MockSymbol("AAPL")
        self.symbol2 = MockSymbol("MSFT")
        self.symbol3 = MockSymbol("GOOGL")
        self.symbol4 = MockSymbol("AMZN")
        
    def test_initialization(self):
        """测试初始化"""
        self.assertEqual(self.risk_manager.max_holding_days, 30)
        self.assertEqual(self.risk_manager.cooldown_days, 7)
        self.assertEqual(self.risk_manager.max_pair_drawdown, 0.10)
        self.assertEqual(self.risk_manager.max_single_drawdown, 0.20)
        
    def test_calculate_pair_drawdown(self):
        """测试配对回撤计算"""
        # 设置持仓
        self.algorithm.Portfolio[self.symbol1] = MockHolding(
            self.symbol1, True, 100, 100, 95  # 亏损5%
        )
        self.algorithm.Portfolio[self.symbol2] = MockHolding(
            self.symbol2, True, -50, 50, 48  # 盈利4%（做空）
        )
        
        # 计算配对回撤
        drawdown = self.risk_manager._calculate_pair_drawdown(self.symbol1, self.symbol2)
        
        # 总成本 = 10000 + 2500 = 12500
        # 总盈亏 = -500 + 100 = -400
        # 回撤率 = -400 / 12500 = -0.032
        self.assertAlmostEqual(drawdown, -0.032, places=3)
        
    def test_calculate_single_drawdowns(self):
        """测试单边回撤计算"""
        # 设置持仓（做多亏损）
        self.algorithm.Portfolio[self.symbol1] = MockHolding(
            self.symbol1, True, 100, 100, 80  # 下跌20%
        )
        # 设置持仓（做空盈利）
        self.algorithm.Portfolio[self.symbol2] = MockHolding(
            self.symbol2, True, -50, 50, 40  # 下跌20%，做空盈利
        )
        
        # 计算单边回撤
        drawdowns = self.risk_manager._calculate_single_drawdowns(self.symbol1, self.symbol2)
        
        # symbol1: 做多，(80-100)/100 = -0.2
        self.assertAlmostEqual(drawdowns[self.symbol1], -0.2, places=3)
        
        # symbol2: 做空，(50-40)/50 = 0.2
        self.assertAlmostEqual(drawdowns[self.symbol2], 0.2, places=3)
        
    def test_check_pair_integrity(self):
        """测试配对完整性检查"""
        # 测试1：都没持仓
        self.algorithm.Portfolio[self.symbol1] = MockHolding(self.symbol1, False, 0, 0, 100)
        self.algorithm.Portfolio[self.symbol2] = MockHolding(self.symbol2, False, 0, 0, 100)
        status = self.risk_manager._check_pair_integrity(self.symbol1, self.symbol2)
        self.assertEqual(status, "no_position")
        
        # 测试2：正常配对（方向相反）
        self.algorithm.Portfolio[self.symbol1] = MockHolding(self.symbol1, True, 100, 100, 100)
        self.algorithm.Portfolio[self.symbol2] = MockHolding(self.symbol2, True, -50, 50, 100)
        status = self.risk_manager._check_pair_integrity(self.symbol1, self.symbol2)
        self.assertEqual(status, "normal")
        
        # 测试3：同向持仓错误
        self.algorithm.Portfolio[self.symbol1] = MockHolding(self.symbol1, True, 100, 100, 100)
        self.algorithm.Portfolio[self.symbol2] = MockHolding(self.symbol2, True, 50, 50, 100)
        status = self.risk_manager._check_pair_integrity(self.symbol1, self.symbol2)
        self.assertEqual(status, "same_direction_error")
        
        # 测试4：单边持仓
        self.algorithm.Portfolio[self.symbol1] = MockHolding(self.symbol1, True, 100, 100, 100)
        self.algorithm.Portfolio[self.symbol2] = MockHolding(self.symbol2, False, 0, 0, 100)
        status = self.risk_manager._check_pair_integrity(self.symbol1, self.symbol2)
        self.assertEqual(status, "single_side_only")
        
    def test_holding_timeout_liquidation(self):
        """测试持仓超时平仓"""
        # 设置配对
        self.pair_registry.update_pairs([(self.symbol1, self.symbol2)])
        
        # 保存原始时间
        original_time = self.algorithm.Time
        
        # 回到35天前
        self.algorithm.SetTime(original_time - timedelta(days=35))
        
        # 初始化空持仓（建仓前）
        self.algorithm.Portfolio[self.symbol1] = MockHolding(self.symbol1, False, 0, 0, 100)
        self.algorithm.Portfolio[self.symbol2] = MockHolding(self.symbol2, False, 0, 0, 100)
        
        # 在“35天前”创建订单
        order1 = self.algorithm.Transactions.CreateOrder(self.symbol1, 100, self.algorithm.Time)
        order2 = self.algorithm.Transactions.CreateOrder(self.symbol2, -50, self.algorithm.Time)
        
        # 记录订单
        self.order_tracker.on_order_event(create_filled_order_event(order1))
        self.order_tracker.on_order_event(create_filled_order_event(order2))
        
        # 时间推进到现在
        self.algorithm.SetTime(original_time)
        
        # 设置当前持仓状态
        self.algorithm.Portfolio[self.symbol1] = MockHolding(self.symbol1, True, 100, 100, 100)
        self.algorithm.Portfolio[self.symbol2] = MockHolding(self.symbol2, True, -50, 100, 100)
        
        # 执行风控检查
        targets = []
        risk_adjusted_targets = self.risk_manager.ManageRisk(self.algorithm, targets)
        
        # 应该生成平仓指令
        self.assertEqual(len(risk_adjusted_targets), 2)
        self.assertEqual(risk_adjusted_targets[0].Quantity, 0)
        self.assertEqual(risk_adjusted_targets[1].Quantity, 0)
        
        # 验证风控统计
        stats = self.risk_manager.get_statistics()
        self.assertEqual(stats['holding_timeout'], 1)
        
    def test_pair_stop_loss(self):
        """测试配对止损"""
        # 设置配对和持仓（配对整体亏损15%）
        self.pair_registry.update_pairs([(self.symbol1, self.symbol2)])
        self.algorithm.Portfolio[self.symbol1] = MockHolding(
            self.symbol1, True, 100, 100, 85  # 亏损15%
        )
        self.algorithm.Portfolio[self.symbol2] = MockHolding(
            self.symbol2, True, -50, 50, 50  # 不赚不亏
        )
        
        # 模拟建仓
        entry_time = self.algorithm.Time
        order1 = self.algorithm.Transactions.CreateOrder(self.symbol1, 100, entry_time)
        order2 = self.algorithm.Transactions.CreateOrder(self.symbol2, -50, entry_time)
        self.order_tracker.on_order_event(create_filled_order_event(order1, entry_time))
        self.order_tracker.on_order_event(create_filled_order_event(order2, entry_time))
        
        # 执行风控检查
        targets = []
        risk_adjusted_targets = self.risk_manager.ManageRisk(self.algorithm, targets)
        
        # 应该触发止损
        self.assertEqual(len(risk_adjusted_targets), 2)
        
        # 验证风控统计
        stats = self.risk_manager.get_statistics()
        self.assertEqual(stats['pair_stop_loss'], 1)
        
    def test_single_stop_loss(self):
        """测试单边止损"""
        # 设置配对
        self.pair_registry.update_pairs([(self.symbol1, self.symbol2)])
        
        # 初始化空持仓
        self.algorithm.Portfolio[self.symbol1] = MockHolding(self.symbol1, False, 0, 0, 100)
        self.algorithm.Portfolio[self.symbol2] = MockHolding(self.symbol2, False, 0, 0, 100)
        
        # 模拟建仓
        entry_time = self.algorithm.Time
        order1 = self.algorithm.Transactions.CreateOrder(self.symbol1, 100, entry_time)
        order2 = self.algorithm.Transactions.CreateOrder(self.symbol2, -50, entry_time)
        self.order_tracker.on_order_event(create_filled_order_event(order1, entry_time))
        self.order_tracker.on_order_event(create_filled_order_event(order2, entry_time))
        
        # 设置当前持仓
        # symbol1: 做多亏损25%
        self.algorithm.Portfolio[self.symbol1] = MockHolding(
            self.symbol1, True, 100, 100, 75  # 亏损25%
        )
        # symbol2: 做空盈利15% (价格下跌对做空有利)
        # 这样配对整体亏损 = (100*(75-100) + 50*(100-85)) / 15000 = -1750/15000 = -11.7%
        # 但我们需要配对亏损 < 10%，所以让symbol2盈利更多
        self.algorithm.Portfolio[self.symbol2] = MockHolding(
            self.symbol2, True, -50, 100, 80  # 价格下跌20%，做空盈利20%
        )
        
        # 执行风控检查
        targets = []
        risk_adjusted_targets = self.risk_manager.ManageRisk(self.algorithm, targets)
        
        
        # 应该触发止损
        self.assertEqual(len(risk_adjusted_targets), 2)
        
        # 验证风控统计
        stats = self.risk_manager.get_statistics()
        self.assertEqual(stats['single_stop_loss'], 1)
        
    def test_cooldown_filter(self):
        """测试冷却期过滤"""
        # 设置配对
        self.pair_registry.update_pairs([(self.symbol1, self.symbol2)])
        
        # 保存原始时间
        original_time = self.algorithm.Time
        
        # 回到10天前，先建仓
        self.algorithm.SetTime(original_time - timedelta(days=10))
        
        # 建仓前空持仓
        self.algorithm.Portfolio[self.symbol1] = MockHolding(self.symbol1, False, 0, 0, 100)
        self.algorithm.Portfolio[self.symbol2] = MockHolding(self.symbol2, False, 0, 0, 100)
        
        # 建仓订单
        entry_order1 = self.algorithm.Transactions.CreateOrder(self.symbol1, 100, self.algorithm.Time)
        entry_order2 = self.algorithm.Transactions.CreateOrder(self.symbol2, -50, self.algorithm.Time)
        self.order_tracker.on_order_event(create_filled_order_event(entry_order1))
        self.order_tracker.on_order_event(create_filled_order_event(entry_order2))
        
        # 设置持仓状态
        self.algorithm.Portfolio[self.symbol1] = MockHolding(self.symbol1, True, 100, 100, 100)
        self.algorithm.Portfolio[self.symbol2] = MockHolding(self.symbol2, True, -50, 100, 100)
        
        # 推进到3天前，平仓
        self.algorithm.SetTime(original_time - timedelta(days=3))
        
        exit_order1 = self.algorithm.Transactions.CreateOrder(self.symbol1, -100, self.algorithm.Time)
        exit_order2 = self.algorithm.Transactions.CreateOrder(self.symbol2, 50, self.algorithm.Time)
        self.order_tracker.on_order_event(create_filled_order_event(exit_order1))
        self.order_tracker.on_order_event(create_filled_order_event(exit_order2))
        
        # 清空持仓
        self.algorithm.Portfolio[self.symbol1] = MockHolding(self.symbol1, False, 0, 0, 100)
        self.algorithm.Portfolio[self.symbol2] = MockHolding(self.symbol2, False, 0, 0, 100)
        
        # 推进到现在
        self.algorithm.SetTime(original_time)
        
        # 创建新的建仓信号
        new_targets = [
            MockPortfolioTarget(self.symbol1, 100),
            MockPortfolioTarget(self.symbol2, -50)
        ]
        
        # 执行风控（应该过滤掉）
        risk_adjusted_targets = self.risk_manager.ManageRisk(self.algorithm, new_targets)
        
        # 冷却期内的建仓信号应该被过滤
        self.assertEqual(len(risk_adjusted_targets), 0)
        
    def test_normal_position_no_action(self):
        """测试正常持仓不触发风控"""
        # 设置配对
        self.pair_registry.update_pairs([(self.symbol1, self.symbol2)])
        
        # 保存原始时间
        original_time = self.algorithm.Time
        
        # 回到10天前建仓
        self.algorithm.SetTime(original_time - timedelta(days=10))
        
        # 建仓
        order1 = self.algorithm.Transactions.CreateOrder(self.symbol1, 100, self.algorithm.Time)
        order2 = self.algorithm.Transactions.CreateOrder(self.symbol2, -50, self.algorithm.Time)
        self.order_tracker.on_order_event(create_filled_order_event(order1))
        self.order_tracker.on_order_event(create_filled_order_event(order2))
        
        # 回到现在
        self.algorithm.SetTime(original_time)
        
        # 设置正常持仓状态
        self.algorithm.Portfolio[self.symbol1] = MockHolding(
            self.symbol1, True, 100, 100, 105  # 盈利5%
        )
        self.algorithm.Portfolio[self.symbol2] = MockHolding(
            self.symbol2, True, -50, 50, 48  # 盈利4%
        )
        
        # 执行风控检查
        targets = []
        risk_adjusted_targets = self.risk_manager.ManageRisk(self.algorithm, targets)
        
        # 不应该生成任何平仓指令
        self.assertEqual(len(risk_adjusted_targets), 0)
        
        # 验证没有触发任何风控
        stats = self.risk_manager.get_statistics()
        self.assertEqual(stats['holding_timeout'], 0)
        self.assertEqual(stats['pair_stop_loss'], 0)
        self.assertEqual(stats['single_stop_loss'], 0)
        
    def test_abnormal_order_detection(self):
        """测试异常订单检测"""
        # 设置配对
        self.pair_registry.update_pairs([(self.symbol1, self.symbol2)])
        
        # 初始化空持仓
        self.algorithm.Portfolio[self.symbol1] = MockHolding(self.symbol1, False, 0, 0, 100)
        self.algorithm.Portfolio[self.symbol2] = MockHolding(self.symbol2, False, 0, 0, 100)
        
        # 创建异常订单（只有一边成交）
        order1 = self.algorithm.Transactions.CreateOrder(self.symbol1, 100, self.algorithm.Time)
        order2 = self.algorithm.Transactions.CreateOrder(self.symbol2, -50, self.algorithm.Time)
        
        # 两边都提交
        from tests.mocks.mock_quantconnect import create_submitted_order_event
        self.order_tracker.on_order_event(create_submitted_order_event(order1))
        self.order_tracker.on_order_event(create_submitted_order_event(order2))
        
        # 只有一边成交
        self.order_tracker.on_order_event(create_filled_order_event(order1))
        # order2 保持提交状态
        
        # 执行风控检查
        targets = []
        self.risk_manager.ManageRisk(self.algorithm, targets)
        
        # 验证是否检测到异常配对
        abnormal_pairs = self.order_tracker.get_abnormal_pairs()
        self.assertEqual(len(abnormal_pairs), 1)
        
    def test_create_liquidation_targets(self):
        """测试创建平仓指令"""
        # 准备平仓列表
        liquidation_pairs = [
            (self.symbol1, self.symbol2, "持仓超时"),
            (self.symbol3, self.symbol4, "配对止损")
        ]
        
        # 创建平仓指令
        targets = self.risk_manager._create_liquidation_targets(liquidation_pairs)
        
        # 验证生成了正确的平仓指令
        self.assertEqual(len(targets), 4)
        
        # 所有目标数量都应该是0
        for target in targets:
            self.assertEqual(target.Quantity, 0)
        
        # 验证包含所有股票
        target_symbols = {target.Symbol for target in targets}
        self.assertIn(self.symbol1, target_symbols)
        self.assertIn(self.symbol2, target_symbols)
        self.assertIn(self.symbol3, target_symbols)
        self.assertIn(self.symbol4, target_symbols)


if __name__ == '__main__':
    unittest.main()