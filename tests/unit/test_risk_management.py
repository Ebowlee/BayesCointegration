"""
RiskManagement 简化版单元测试
测试简化后的风险管理模块基础功能
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# 设置测试环境
import tests.setup_test_env

import unittest
from datetime import datetime, timedelta
from tests.mocks.mock_quantconnect import (
    MockAlgorithm, MockSymbol, MockHolding, MockPortfolioTarget
)
from src.RiskManagement import BayesianCointegrationRiskManagementModel

class TestRiskManagement(unittest.TestCase):
    """测试简化后的风险管理模块"""

    def setUp(self):
        """设置测试环境"""
        self.algorithm = MockAlgorithm()
        self.algorithm.SetTime(datetime(2023, 1, 15))

        # 初始化风险管理（无需CPM）
        config = {
            'max_single_loss': 0.15,
            'max_portfolio_loss': 0.10
        }
        self.risk_manager = BayesianCointegrationRiskManagementModel(
            self.algorithm, config
        )

        # 设置测试股票
        self.symbol1 = MockSymbol("AAPL")
        self.symbol2 = MockSymbol("MSFT")

        # 添加到Securities
        self.algorithm.Securities = {self.symbol1: None, self.symbol2: None}

    def test_no_positions_no_action(self):
        """测试：无持仓时不触发风控"""
        targets = []
        result = self.risk_manager.ManageRisk(self.algorithm, targets)

        self.assertEqual(len(result), 0)
        self.assertEqual(len(self.risk_manager.risk_triggers['single_loss']), 0)

    def test_single_stock_loss_trigger(self):
        """测试：单只股票亏损超限触发平仓"""
        # 设置亏损持仓（亏损20%，超过15%阈值）
        self.algorithm.Portfolio[self.symbol1] = MockHolding(
            self.symbol1, True, 100, 100, 80  # 亏损20%
        )
        # 确保在Securities中
        self.algorithm.Securities = {self.symbol1: None}

        targets = []
        result = self.risk_manager.ManageRisk(self.algorithm, targets)

        # 应该生成平仓指令
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].Symbol, self.symbol1)
        self.assertEqual(result[0].Quantity, 0)  # 平仓

        # 应该记录风控触发
        self.assertEqual(len(self.risk_manager.risk_triggers['single_loss']), 1)

    def test_short_position_loss_trigger(self):
        """测试：空头持仓亏损超限触发平仓"""
        # 设置空头亏损持仓（价格上涨20%导致亏损）
        self.algorithm.Portfolio[self.symbol1] = MockHolding(
            self.symbol1, True, -100, 100, 120  # 空头亏损20%
        )
        # 确保在Securities中
        self.algorithm.Securities = {self.symbol1: None}

        targets = []
        result = self.risk_manager.ManageRisk(self.algorithm, targets)

        # 应该生成平仓指令
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].Symbol, self.symbol1)
        self.assertEqual(result[0].Quantity, 0)

    def test_profitable_position_no_trigger(self):
        """测试：盈利持仓不触发风控"""
        # 设置盈利持仓
        self.algorithm.Portfolio[self.symbol1] = MockHolding(
            self.symbol1, True, 100, 100, 110  # 盈利10%
        )
        # 确保在Securities中
        self.algorithm.Securities = {self.symbol1: None}

        targets = []
        result = self.risk_manager.ManageRisk(self.algorithm, targets)

        # 不应该生成额外指令
        self.assertEqual(len(result), 0)
        self.assertEqual(len(self.risk_manager.risk_triggers['single_loss']), 0)

    def test_modify_existing_target(self):
        """测试：修改现有target为平仓"""
        # 设置亏损持仓
        self.algorithm.Portfolio[self.symbol1] = MockHolding(
            self.symbol1, True, 100, 100, 80  # 亏损20%
        )
        # 确保在Securities中
        self.algorithm.Securities = {self.symbol1: None}

        # 已有target
        existing_target = MockPortfolioTarget(self.symbol1, 50)
        targets = [existing_target]

        result = self.risk_manager.ManageRisk(self.algorithm, targets)

        # 应该修改现有target为平仓
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], existing_target)
        self.assertEqual(result[0].Quantity, 0)

if __name__ == '__main__':
    unittest.main()