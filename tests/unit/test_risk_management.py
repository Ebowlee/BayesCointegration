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
from src.RiskManagement import BayesianCointegrationRiskManagementModel

# Mock CentralPairManager for testing
class MockCentralPairManager:
    def __init__(self):
        self.active_pairs = []
        self.expired_pairs = []
        
    def get_active_pairs_with_position(self):
        return self.active_pairs
    
    def get_risk_alerts(self):
        return {'expired_pairs': self.expired_pairs}
    
    def clear_expired_pairs(self):
        self.expired_pairs = []
    
    def add_active_pair(self, symbol1, symbol2):
        """Helper method for tests to add active pairs"""
        self.active_pairs.append({
            'pair_key': (symbol1.Value, symbol2.Value)
        })

# Mock OrderTracker for testing
class MockOrderTracker:
    def __init__(self):
        self.orders = []
        self.abnormal_pairs = []
        
    def on_order_event(self, order_event):
        self.orders.append(order_event)
    
    def get_abnormal_pairs(self):
        return self.abnormal_pairs
    
    def get_holding_days(self, symbol1, symbol2):
        # Return 35 days for timeout testing
        return 35

# 为测试环境设置PortfolioTarget别名
import tests.mocks.mock_quantconnect as mock_qc
mock_qc.PortfolioTarget = MockPortfolioTarget


class TestRiskManagement(unittest.TestCase):
    """RiskManagement 核心方法测试类"""
    
    def setUp(self):
        """测试前准备"""
        self.algorithm = MockAlgorithm()
        self.central_pair_manager = MockCentralPairManager()
        self.order_tracker = MockOrderTracker()
        
        # 设置 algorithm 的属性
        self.algorithm.central_pair_manager = self.central_pair_manager
        self.algorithm.order_tracker = self.order_tracker
        
        # 风控配置
        self.config = {
            'max_holding_days': 30,
            'cooldown_days': 7,
            'max_pair_drawdown': 0.20,  # 更新为20%
            'max_single_drawdown': 0.30   # 更新为30%
        }
        
        self.risk_manager = BayesianCointegrationRiskManagementModel(
            self.algorithm, self.config, None, self.central_pair_manager
        )
        
        # 创建测试用的股票
        self.symbol1 = MockSymbol("AAPL")
        self.symbol2 = MockSymbol("MSFT")
        self.symbol3 = MockSymbol("GOOGL")
        self.symbol4 = MockSymbol("AMZN")
        
        # 添加到Securities
        self.algorithm.Securities.add_symbol(self.symbol1)
        self.algorithm.Securities.add_symbol(self.symbol2)
        self.algorithm.Securities.add_symbol(self.symbol3)
        self.algorithm.Securities.add_symbol(self.symbol4)
        
    def test_initialization(self):
        """测试初始化"""
        self.assertEqual(self.risk_manager.max_holding_days, 30)
        # cooldown_days 存储在 config 中，不是直接属性
        self.assertEqual(self.risk_manager.config.get('cooldown_days'), 7)
        self.assertEqual(self.risk_manager.max_pair_drawdown, 0.20)  # 更新为20%
        self.assertEqual(self.risk_manager.max_single_drawdown, 0.30)  # 更新为30%
        
    def test_pair_drawdown_calculation(self):
        """测试配对回撤计算逻辑"""
        # 设置活跃配对
        self.central_pair_manager.add_active_pair(self.symbol1, self.symbol2)
        
        # 设置持仓
        self.algorithm.Portfolio[self.symbol1] = MockHolding(
            self.symbol1, True, 100, 100, 95  # 亏损5%
        )
        self.algorithm.Portfolio[self.symbol2] = MockHolding(
            self.symbol2, True, -50, 50, 48  # 盈利4%（做空）
        )
        
        # 执行风控检查
        targets = []
        risk_adjusted_targets = self.risk_manager.ManageRisk(self.algorithm, targets)
        
        # 总成本 = 10000 + 2500 = 12500
        # 总盈亏 = -500 + 100 = -400
        # 回撤率 = -400 / 12500 = -0.032 (3.2%，不触发20%阈值)
        self.assertEqual(len(risk_adjusted_targets), 0)  # 不应该触发止损
        
    def test_single_drawdown_calculation(self):
        """测试单边回撤检查逻辑"""
        # 设置活跃配对
        self.central_pair_manager.add_active_pair(self.symbol1, self.symbol2)
        
        # 设置持仓（做多亏损20%，做空盈利20%）
        self.algorithm.Portfolio[self.symbol1] = MockHolding(
            self.symbol1, True, 100, 100, 80  # 下跌20%
        )
        self.algorithm.Portfolio[self.symbol2] = MockHolding(
            self.symbol2, True, -50, 50, 40  # 下跌20%，做空盈利20%
        )
        
        # 执行风控检查
        targets = []
        risk_adjusted_targets = self.risk_manager.ManageRisk(self.algorithm, targets)
        
        # symbol1: 做多亏损20%，未达30%阈值
        # symbol2: 做空盈利20%
        # 配对整体回撤 = 0 (盈亏相抵)
        self.assertEqual(len(risk_adjusted_targets), 0)  # 不应该触发止损
        
    # test_check_pair_integrity 已移除 - 该方法在新版RiskManagement中不存在
        
    def test_holding_timeout_liquidation(self):
        """测试持仓超时平仓"""
        # 设置配对
        self.central_pair_manager.add_active_pair(self.symbol1, self.symbol2)
        
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
        
        # 验证风控触发
        # 注：get_statistics方法在新版中不存在，直接检查targets
        
    def test_pair_stop_loss(self):
        """测试配对止损"""
        # 设置配对和持仓（配对整体亏损22%，超过20%阈值）
        self.central_pair_manager.add_active_pair(self.symbol1, self.symbol2)
        self.algorithm.Portfolio[self.symbol1] = MockHolding(
            self.symbol1, True, 100, 100, 73  # 亏损27%
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
        
        # 验证风控触发（通过检查targets数量）
        
    def test_single_stop_loss(self):
        """测试单边止损（30%阈值）"""
        # 设置配对
        self.central_pair_manager.add_active_pair(self.symbol1, self.symbol2)
        
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
        # symbol1: 做多亏损35%（超过30%单边止损阈值）
        self.algorithm.Portfolio[self.symbol1] = MockHolding(
            self.symbol1, True, 100, 100, 65  # 亏损35%
        )
        # symbol2: 做空盈利20% (价格下跌对做空有利)
        # 配对整体亏损 = (-35% * 10000 + 20% * 2500) / 12500 = -24%
        # 虽然配对整体亏损24%也超过20%阈值，但单边35%会先触发
        self.algorithm.Portfolio[self.symbol2] = MockHolding(
            self.symbol2, True, -50, 100, 80  # 价格下跌20%，做空盈利20%
        )
        
        # 执行风控检查
        targets = []
        risk_adjusted_targets = self.risk_manager.ManageRisk(self.algorithm, targets)
        
        
        # 应该触发止损
        self.assertEqual(len(risk_adjusted_targets), 2)
        
        # 验证风控触发（通过检查targets数量）
        
    def test_cooldown_filter(self):
        """测试冷却期过滤"""
        # 设置配对
        self.central_pair_manager.add_active_pair(self.symbol1, self.symbol2)
        
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
        self.central_pair_manager.add_active_pair(self.symbol1, self.symbol2)
        
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
        
        # 验证没有触发任何风控（通过检查targets为空）
        
    def test_abnormal_order_detection(self):
        """测试异常订单检测"""
        # 设置配对
        self.central_pair_manager.add_active_pair(self.symbol1, self.symbol2)
        
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
        
    # test_create_liquidation_targets 已移除 - 该方法在新版RiskManagement中不存在
    
    def test_single_stop_loss_boundary(self):
        """测试单边止损边界条件（刚好29%不触发）"""
        # 设置配对
        self.central_pair_manager.add_active_pair(self.symbol1, self.symbol2)
        
        # 模拟建仓
        entry_time = self.algorithm.Time
        order1 = self.algorithm.Transactions.CreateOrder(self.symbol1, 100, entry_time)
        order2 = self.algorithm.Transactions.CreateOrder(self.symbol2, -50, entry_time)
        self.order_tracker.on_order_event(create_filled_order_event(order1, entry_time))
        self.order_tracker.on_order_event(create_filled_order_event(order2, entry_time))
        
        # 设置当前持仓
        # symbol1: 做多亏损29%（刚好不触发30%单边止损）
        self.algorithm.Portfolio[self.symbol1] = MockHolding(
            self.symbol1, True, 100, 100, 71  # 亏损29%
        )
        # symbol2: 做空盈利，需要足够盈利使配对整体回撤 < 20%
        # symbol1亏损2900，要使总亏损<2500(20%*12500)，symbol2需盈利>400
        # 50股做空，从50跌到42，盈利50*(50-42)=400
        self.algorithm.Portfolio[self.symbol2] = MockHolding(
            self.symbol2, True, -50, 50, 42  # 价格下跌16%，做空盈利16%
        )
        
        # 执行风控检查
        targets = []
        risk_adjusted_targets = self.risk_manager.ManageRisk(self.algorithm, targets)
        
        # 不应该触发止损
        self.assertEqual(len(risk_adjusted_targets), 0)
        
        # 验证没有触发单边止损（通过检查targets为空）
    
    def test_pair_stop_loss_boundary(self):
        """测试配对止损边界条件（刚好19%不触发）"""
        # 设置配对
        self.central_pair_manager.add_active_pair(self.symbol1, self.symbol2)
        
        # 模拟建仓
        entry_time = self.algorithm.Time
        order1 = self.algorithm.Transactions.CreateOrder(self.symbol1, 100, entry_time)
        order2 = self.algorithm.Transactions.CreateOrder(self.symbol2, -50, entry_time)
        self.order_tracker.on_order_event(create_filled_order_event(order1, entry_time))
        self.order_tracker.on_order_event(create_filled_order_event(order2, entry_time))
        
        # 设置当前持仓
        # 配对整体亏损刚好19%（不触发20%配对止损）
        # 总成本 = 10000 + 2500 = 12500
        # 目标亏损 = 12500 * 0.19 = 2375
        self.algorithm.Portfolio[self.symbol1] = MockHolding(
            self.symbol1, True, 100, 100, 76  # 亏损24%，损失2400
        )
        self.algorithm.Portfolio[self.symbol2] = MockHolding(
            self.symbol2, True, -50, 50, 49  # 价格下跌2%，做空盈利1%，赚25
        )
        # 实际亏损 = -2400 + 25 = -2375，亏损率 = 2375/12500 = 19%
        
        # 执行风控检查
        targets = []
        risk_adjusted_targets = self.risk_manager.ManageRisk(self.algorithm, targets)
        
        # 不应该触发止损
        self.assertEqual(len(risk_adjusted_targets), 0)
        
        # 验证没有触发配对止损（通过检查targets为空）


if __name__ == '__main__':
    unittest.main()