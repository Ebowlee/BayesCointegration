"""
测试RiskManagement新功能
验证生产代码修改后的新功能是否正常工作
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
    create_filled_order_event, create_submitted_order_event
)
from src.PairRegistry import PairRegistry
from src.OrderTracker import OrderTracker
from src.RiskManagement import BayesianCointegrationRiskManagementModel

# 为测试环境设置PortfolioTarget别名
import tests.mocks.mock_quantconnect as mock_qc
mock_qc.PortfolioTarget = MockPortfolioTarget


class TestRiskNewFeatures(unittest.TestCase):
    """测试新添加的功能"""
    
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
        
    def test_none_targets_handling(self):
        """测试None输入处理"""
        # 应该能够处理None而不崩溃
        targets = self.risk_manager.ManageRisk(self.algorithm, None)
        self.assertEqual(len(targets), 0)
        
        # 检查debug消息中显示0个targets
        debug_messages = self.algorithm.debug_messages
        self.assertTrue(any("收到0个targets" in msg for msg in debug_messages))
        
    def test_zero_price_handling(self):
        """测试价格为0的处理"""
        self.pair_registry.update_pairs([(self.symbol1, self.symbol2)])
        
        # 建仓
        order1 = self.algorithm.Transactions.CreateOrder(self.symbol1, 100, self.algorithm.Time)
        order2 = self.algorithm.Transactions.CreateOrder(self.symbol2, -50, self.algorithm.Time)
        
        self.order_tracker.on_order_event(create_filled_order_event(order1))
        self.order_tracker.on_order_event(create_filled_order_event(order2))
        
        # 设置价格为0
        self.algorithm.Portfolio[self.symbol1] = MockHolding(self.symbol1, True, 100, 100, 0)
        self.algorithm.Portfolio[self.symbol2] = MockHolding(self.symbol2, True, -50, 100, 100)
        
        # 执行风控
        targets = self.risk_manager.ManageRisk(self.algorithm, [])
        
        # 应该有警告消息
        debug_messages = self.algorithm.debug_messages
        self.assertTrue(any("价格为0，跳过回撤计算" in msg for msg in debug_messages))
        
    def test_abnormal_orders_active_handling(self):
        """测试异常订单的主动处理"""
        self.pair_registry.update_pairs([(self.symbol1, self.symbol2)])
        
        # 创建异常订单（只有一边成交）
        order1 = self.algorithm.Transactions.CreateOrder(self.symbol1, 100, self.algorithm.Time)
        order2 = self.algorithm.Transactions.CreateOrder(self.symbol2, -50, self.algorithm.Time)
        
        # 两边都提交
        self.order_tracker.on_order_event(create_submitted_order_event(order1))
        self.order_tracker.on_order_event(create_submitted_order_event(order2))
        
        # 只有一边成交
        self.order_tracker.on_order_event(create_filled_order_event(order1))
        
        # 设置持仓（只有一边）
        self.algorithm.Portfolio[self.symbol1] = MockHolding(self.symbol1, True, 100, 100, 100)
        self.algorithm.Portfolio[self.symbol2] = MockHolding(self.symbol2, False, 0, 0, 100)
        
        # 执行风控
        targets = self.risk_manager.ManageRisk(self.algorithm, [])
        
        # 应该生成平仓指令（主动处理异常配对）
        self.assertEqual(len(targets), 2)
        
        # 检查平仓指令
        for target in targets:
            self.assertEqual(target.Quantity, 0)
        
        # 检查debug消息
        debug_messages = self.algorithm.debug_messages
        self.assertTrue(any("异常配对" in msg for msg in debug_messages))
        self.assertTrue(any("订单执行异常" in msg for msg in debug_messages))
        
    def test_parameter_validation(self):
        """测试参数验证"""
        # 测试无效的持仓天数
        invalid_config = self.config.copy()
        invalid_config['max_holding_days'] = 0
        
        with self.assertRaises(AssertionError) as context:
            BayesianCointegrationRiskManagementModel(
                self.algorithm, invalid_config, self.order_tracker, self.pair_registry
            )
        self.assertIn("持仓天数必须在1-365之间", str(context.exception))
        
        # 测试无效的回撤阈值
        invalid_config = self.config.copy()
        invalid_config['max_pair_drawdown'] = 1.5
        
        with self.assertRaises(AssertionError) as context:
            BayesianCointegrationRiskManagementModel(
                self.algorithm, invalid_config, self.order_tracker, self.pair_registry
            )
        self.assertIn("配对回撤阈值必须在0-100%之间", str(context.exception))
        

if __name__ == '__main__':
    unittest.main()