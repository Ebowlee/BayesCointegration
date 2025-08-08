"""
测试基于订单成交的CPM状态管理

验证CPM状态更新只在订单真正成交后发生
"""

import unittest
from datetime import datetime
from unittest.mock import Mock, MagicMock, patch
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.CentralPairManager import CentralPairManager, PairState
from src.PairRegistry import PairRegistry


class TestOrderBasedCPM(unittest.TestCase):
    """测试基于订单成交的CPM状态管理"""
    
    def setUp(self):
        """初始化测试环境"""
        self.algorithm = Mock()
        self.algorithm.Time = datetime(2024, 7, 1)
        self.algorithm.Debug = Mock()
        self.algorithm.Portfolio = {}
        
        # 配置
        config = {
            'enable_central_pair_manager': True,
            'max_pairs': 20,
            'max_symbol_repeats': 1,
            'cooldown_days': 7,
            'max_holding_days': 30,
            'min_quality_score': 0.3
        }
        
        # 创建CPM和PairRegistry
        self.cpm = CentralPairManager(self.algorithm, config)
        self.pair_registry = PairRegistry(self.algorithm)
        
        # 创建测试股票（使用Mock对象）
        self.symbol1 = Mock()
        self.symbol1.Value = "AAPL"
        self.symbol2 = Mock()
        self.symbol2.Value = "MSFT"
        
        # 模拟Portfolio
        holding1 = Mock()
        holding1.Invested = False
        holding1.Quantity = 0
        holding2 = Mock()
        holding2.Invested = False
        holding2.Quantity = 0
        self.algorithm.Portfolio[self.symbol1] = holding1
        self.algorithm.Portfolio[self.symbol2] = holding2
    
    def test_entry_not_registered_before_fill(self):
        """测试建仓信号生成后，成交前不应该注册entry"""
        # 1. 候选配对通过CPM审核
        candidates = [(self.symbol1, self.symbol2)]
        approved = self.cpm.evaluate_candidates(candidates)
        self.assertEqual(len(approved), 1)
        
        # 2. 此时配对应该是APPROVED状态，不是ACTIVE
        pair_id = self.cpm._get_pair_id(self.symbol1, self.symbol2)
        self.assertEqual(self.cpm.pair_states[pair_id].state, PairState.APPROVED)
        
        # 3. 获取活跃配对应该为空
        active_pairs = self.cpm.get_active_pairs()
        self.assertEqual(len(active_pairs), 0)
    
    def test_entry_registered_after_both_filled(self):
        """测试只有两边都成交后才注册entry"""
        # 1. 候选配对通过审核
        candidates = [(self.symbol1, self.symbol2)]
        approved = self.cpm.evaluate_candidates(candidates)
        
        # 2. 模拟第一只股票成交
        self.algorithm.Portfolio[self.symbol1].Invested = True
        self.algorithm.Portfolio[self.symbol1].Quantity = 100
        
        # 不应该注册entry（因为另一边还没成交）
        active_pairs = self.cpm.get_active_pairs()
        self.assertEqual(len(active_pairs), 0)
        
        # 3. 模拟第二只股票成交
        self.algorithm.Portfolio[self.symbol2].Invested = True
        self.algorithm.Portfolio[self.symbol2].Quantity = -50
        
        # 手动调用register_entry（在实际中由OnOrderEvent调用）
        self.cpm.register_entry(self.symbol1, self.symbol2)
        
        # 现在应该有活跃配对
        active_pairs = self.cpm.get_active_pairs()
        self.assertEqual(len(active_pairs), 1)
        self.assertEqual(active_pairs[0]['pair'], (self.symbol1, self.symbol2))
    
    def test_exit_not_registered_before_fill(self):
        """测试平仓信号生成后，成交前不应该注册exit"""
        # 1. 先建立活跃配对
        candidates = [(self.symbol1, self.symbol2)]
        self.cpm.evaluate_candidates(candidates)
        
        # 模拟持仓
        self.algorithm.Portfolio[self.symbol1].Invested = True
        self.algorithm.Portfolio[self.symbol1].Quantity = 100
        self.algorithm.Portfolio[self.symbol2].Invested = True
        self.algorithm.Portfolio[self.symbol2].Quantity = -50
        
        # 注册entry
        self.cpm.register_entry(self.symbol1, self.symbol2)
        
        # 2. 生成平仓信号但不调用register_exit
        # （在实际中PC或Risk生成平仓target但不更新CPM）
        
        # 配对应该仍然是ACTIVE
        pair_id = self.cpm._get_pair_id(self.symbol1, self.symbol2)
        self.assertEqual(self.cpm.pair_states[pair_id].state, PairState.ACTIVE)
        
        # 不应该在冷却期
        self.assertFalse(self.cpm.is_in_cooldown(self.symbol1, self.symbol2))
    
    def test_exit_registered_after_both_closed(self):
        """测试只有两边都平仓后才注册exit"""
        # 1. 建立活跃配对
        candidates = [(self.symbol1, self.symbol2)]
        self.cpm.evaluate_candidates(candidates)
        self.algorithm.Portfolio[self.symbol1].Invested = True
        self.algorithm.Portfolio[self.symbol1].Quantity = 100
        self.algorithm.Portfolio[self.symbol2].Invested = True
        self.algorithm.Portfolio[self.symbol2].Quantity = -50
        self.cpm.register_entry(self.symbol1, self.symbol2)
        
        # 2. 模拟第一只股票平仓
        self.algorithm.Portfolio[self.symbol1].Invested = False
        self.algorithm.Portfolio[self.symbol1].Quantity = 0
        
        # 不应该注册exit（因为另一边还有持仓）
        pair_id = self.cpm._get_pair_id(self.symbol1, self.symbol2)
        self.assertEqual(self.cpm.pair_states[pair_id].state, PairState.ACTIVE)
        
        # 3. 模拟第二只股票平仓
        self.algorithm.Portfolio[self.symbol2].Invested = False
        self.algorithm.Portfolio[self.symbol2].Quantity = 0
        
        # 手动调用register_exit（在实际中由OnOrderEvent调用）
        self.cpm.register_exit(self.symbol1, self.symbol2)
        
        # 现在应该在冷却期
        self.assertTrue(self.cpm.is_in_cooldown(self.symbol1, self.symbol2))
        self.assertEqual(self.cpm.pair_states[pair_id].state, PairState.COOLDOWN)
    
    def test_pair_registry_retains_positions(self):
        """测试PairRegistry保留有持仓的配对"""
        # 1. 初始配对
        pairs = [(self.symbol1, self.symbol2)]
        self.pair_registry.update_pairs(pairs)
        
        # 2. 模拟持仓
        self.algorithm.Portfolio[self.symbol1].Invested = True
        self.algorithm.Portfolio[self.symbol1].Quantity = 100
        
        # 3. 新的选股结果没有这个配对
        new_pairs = []
        self.pair_registry.update_pairs(new_pairs)
        
        # 4. 应该仍能查找到配对（因为有持仓）
        paired = self.pair_registry.get_paired_symbol(self.symbol1)
        self.assertEqual(paired, self.symbol2)
        
        # 5. 平仓后再更新
        self.algorithm.Portfolio[self.symbol1].Invested = False
        self.algorithm.Portfolio[self.symbol1].Quantity = 0
        self.pair_registry.update_pairs([])
        
        # 6. 现在应该找不到配对了
        paired = self.pair_registry.get_paired_symbol(self.symbol1)
        self.assertIsNone(paired)


if __name__ == '__main__':
    unittest.main()