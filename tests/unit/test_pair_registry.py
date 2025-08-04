"""
PairRegistry 单元测试
测试配对注册表的核心功能
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# 设置测试环境
import tests.setup_test_env

import unittest
from datetime import datetime
from tests.mocks.mock_quantconnect import MockAlgorithm, MockSymbol
from src.PairRegistry import PairRegistry


class TestPairRegistry(unittest.TestCase):
    """PairRegistry 单元测试类"""
    
    def setUp(self):
        """测试前准备"""
        self.algorithm = MockAlgorithm()
        self.pair_registry = PairRegistry(self.algorithm)
        
        # 创建测试用的股票
        self.symbol1 = MockSymbol("AAPL")
        self.symbol2 = MockSymbol("MSFT")
        self.symbol3 = MockSymbol("GOOGL")
        self.symbol4 = MockSymbol("AMZN")
        self.symbol5 = MockSymbol("TSLA")
        
    def test_initialization(self):
        """测试初始化"""
        self.assertIsNotNone(self.pair_registry.algorithm)
        self.assertEqual(len(self.pair_registry.active_pairs), 0)
        self.assertIsNone(self.pair_registry.last_update_time)
        
    def test_update_pairs(self):
        """测试更新配对列表"""
        # 创建配对列表
        pairs = [
            (self.symbol1, self.symbol2),
            (self.symbol3, self.symbol4)
        ]
        
        # 更新配对
        self.pair_registry.update_pairs(pairs)
        
        # 验证更新
        self.assertEqual(len(self.pair_registry.active_pairs), 2)
        self.assertEqual(self.pair_registry.active_pairs[0], (self.symbol1, self.symbol2))
        self.assertEqual(self.pair_registry.active_pairs[1], (self.symbol3, self.symbol4))
        self.assertEqual(self.pair_registry.last_update_time, self.algorithm.Time)
        
        # 验证调试消息
        self.assertEqual(len(self.algorithm.debug_messages), 1)
        self.assertIn("更新配对列表(2对)", self.algorithm.debug_messages[0])
        
    def test_get_active_pairs(self):
        """测试获取活跃配对列表"""
        # 设置配对
        pairs = [(self.symbol1, self.symbol2), (self.symbol3, self.symbol4)]
        self.pair_registry.update_pairs(pairs)
        
        # 获取配对
        active_pairs = self.pair_registry.get_active_pairs()
        
        # 验证返回的是副本
        self.assertEqual(active_pairs, pairs)
        self.assertIsNot(active_pairs, self.pair_registry.active_pairs)
        
        # 修改返回的列表不应该影响原列表
        active_pairs.append((self.symbol5, self.symbol1))
        self.assertEqual(len(self.pair_registry.active_pairs), 2)
        
    def test_get_all_symbols_in_pairs(self):
        """测试获取所有配对中的股票"""
        # 设置配对
        pairs = [
            (self.symbol1, self.symbol2),
            (self.symbol3, self.symbol4),
            (self.symbol2, self.symbol5)  # symbol2 出现在多个配对中
        ]
        self.pair_registry.update_pairs(pairs)
        
        # 获取所有股票
        all_symbols = self.pair_registry.get_all_symbols_in_pairs()
        
        # 验证结果
        self.assertEqual(len(all_symbols), 5)
        self.assertIn(self.symbol1, all_symbols)
        self.assertIn(self.symbol2, all_symbols)
        self.assertIn(self.symbol3, all_symbols)
        self.assertIn(self.symbol4, all_symbols)
        self.assertIn(self.symbol5, all_symbols)
        
    def test_is_symbol_in_pairs(self):
        """测试检查股票是否在配对中"""
        # 设置配对
        pairs = [(self.symbol1, self.symbol2), (self.symbol3, self.symbol4)]
        self.pair_registry.update_pairs(pairs)
        
        # 测试在配对中的股票
        self.assertTrue(self.pair_registry.is_symbol_in_pairs(self.symbol1))
        self.assertTrue(self.pair_registry.is_symbol_in_pairs(self.symbol2))
        self.assertTrue(self.pair_registry.is_symbol_in_pairs(self.symbol3))
        self.assertTrue(self.pair_registry.is_symbol_in_pairs(self.symbol4))
        
        # 测试不在配对中的股票
        self.assertFalse(self.pair_registry.is_symbol_in_pairs(self.symbol5))
        
    def test_get_pair_for_symbol(self):
        """测试获取包含指定股票的配对"""
        # 设置配对
        pairs = [(self.symbol1, self.symbol2), (self.symbol3, self.symbol4)]
        self.pair_registry.update_pairs(pairs)
        
        # 测试获取配对
        pair1 = self.pair_registry.get_pair_for_symbol(self.symbol1)
        self.assertEqual(pair1, (self.symbol1, self.symbol2))
        
        pair2 = self.pair_registry.get_pair_for_symbol(self.symbol2)
        self.assertEqual(pair2, (self.symbol1, self.symbol2))
        
        pair3 = self.pair_registry.get_pair_for_symbol(self.symbol3)
        self.assertEqual(pair3, (self.symbol3, self.symbol4))
        
        # 测试不存在的股票
        pair5 = self.pair_registry.get_pair_for_symbol(self.symbol5)
        self.assertIsNone(pair5)
        
    def test_get_paired_symbol(self):
        """测试获取配对的另一只股票"""
        # 设置配对
        pairs = [(self.symbol1, self.symbol2), (self.symbol3, self.symbol4)]
        self.pair_registry.update_pairs(pairs)
        
        # 测试获取配对股票
        paired1 = self.pair_registry.get_paired_symbol(self.symbol1)
        self.assertEqual(paired1, self.symbol2)
        
        paired2 = self.pair_registry.get_paired_symbol(self.symbol2)
        self.assertEqual(paired2, self.symbol1)
        
        paired3 = self.pair_registry.get_paired_symbol(self.symbol3)
        self.assertEqual(paired3, self.symbol4)
        
        paired4 = self.pair_registry.get_paired_symbol(self.symbol4)
        self.assertEqual(paired4, self.symbol3)
        
        # 测试不存在的股票
        paired5 = self.pair_registry.get_paired_symbol(self.symbol5)
        self.assertIsNone(paired5)
        
    def test_contains_pair(self):
        """测试检查是否包含指定配对"""
        # 设置配对
        pairs = [(self.symbol1, self.symbol2), (self.symbol3, self.symbol4)]
        self.pair_registry.update_pairs(pairs)
        
        # 测试存在的配对（正序）
        self.assertTrue(self.pair_registry.contains_pair(self.symbol1, self.symbol2))
        self.assertTrue(self.pair_registry.contains_pair(self.symbol3, self.symbol4))
        
        # 测试存在的配对（倒序）
        self.assertTrue(self.pair_registry.contains_pair(self.symbol2, self.symbol1))
        self.assertTrue(self.pair_registry.contains_pair(self.symbol4, self.symbol3))
        
        # 测试不存在的配对
        self.assertFalse(self.pair_registry.contains_pair(self.symbol1, self.symbol3))
        self.assertFalse(self.pair_registry.contains_pair(self.symbol1, self.symbol4))
        self.assertFalse(self.pair_registry.contains_pair(self.symbol2, self.symbol3))
        self.assertFalse(self.pair_registry.contains_pair(self.symbol5, self.symbol1))
        
    def test_empty_pairs_handling(self):
        """测试空配对列表的处理"""
        # 初始状态应该是空的
        self.assertEqual(len(self.pair_registry.get_active_pairs()), 0)
        self.assertEqual(len(self.pair_registry.get_all_symbols_in_pairs()), 0)
        self.assertFalse(self.pair_registry.is_symbol_in_pairs(self.symbol1))
        self.assertIsNone(self.pair_registry.get_pair_for_symbol(self.symbol1))
        self.assertIsNone(self.pair_registry.get_paired_symbol(self.symbol1))
        self.assertFalse(self.pair_registry.contains_pair(self.symbol1, self.symbol2))
        
        # 更新为空列表
        self.pair_registry.update_pairs([])
        self.assertEqual(len(self.pair_registry.active_pairs), 0)
        self.assertIsNotNone(self.pair_registry.last_update_time)
        
    def test_duplicate_symbol_in_pairs(self):
        """测试股票在多个配对中的情况"""
        # 注意：根据当前实现，一个股票只能在一个配对中
        # 但测试仍然验证这种情况下的行为
        pairs = [
            (self.symbol1, self.symbol2),
            (self.symbol2, self.symbol3)  # symbol2 重复
        ]
        self.pair_registry.update_pairs(pairs)
        
        # get_pair_for_symbol 应该返回第一个匹配的配对
        pair = self.pair_registry.get_pair_for_symbol(self.symbol2)
        self.assertIn(pair, pairs)
        
        # 所有股票都应该在集合中
        all_symbols = self.pair_registry.get_all_symbols_in_pairs()
        self.assertEqual(len(all_symbols), 3)


if __name__ == '__main__':
    unittest.main()