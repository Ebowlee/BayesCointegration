"""
CentralPairManager v1功能测试
测试PC → CPM意图管理功能
"""
import unittest
from datetime import datetime
from unittest.mock import MagicMock
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.CentralPairManager import CentralPairManager


class TestCPMv1PCInteraction(unittest.TestCase):
    """测试CPM v1版本的PC交互功能"""
    
    def setUp(self):
        """测试前初始化"""
        self.algorithm = MagicMock()
        self.algorithm.Time = datetime(2024, 1, 15, 10, 0, 0)
        self.algorithm.Debug = MagicMock()
        self.algorithm.Error = MagicMock()
        
        self.cpm = CentralPairManager(self.algorithm)
        
        # 准备一些活跃配对（模拟Alpha已提交）
        self.cpm.submit_modeled_pairs(
            cycle_id=20240101,
            pairs=[
                {'symbol1_value': 'AAPL', 'symbol2_value': 'MSFT', 'beta': 0.8, 'quality_score': 0.85},
                {'symbol1_value': 'GOOGL', 'symbol2_value': 'META', 'beta': 1.2, 'quality_score': 0.75}
            ]
        )
    
    def test_prepare_open_accepted(self):
        """测试开仓意图被接受"""
        # 提交开仓意图
        result = self.cpm.submit_intent(
            pair_key=('AAPL', 'MSFT'),
            action='prepare_open',
            intent_date=20240115
        )
        
        self.assertEqual(result, 'accepted')
        # 验证实例被创建
        pair_key = ('AAPL', 'MSFT')
        self.assertIn(pair_key, self.cpm.open_instances)
        self.assertEqual(self.cpm.open_instances[pair_key]['instance_id'], 1)
        self.assertEqual(self.cpm.open_instances[pair_key]['cycle_id_start'], 20240101)
    
    def test_prepare_open_duplicate_ignored(self):
        """测试同日重复开仓意图被忽略"""
        # 第一次提交
        result1 = self.cpm.submit_intent(
            pair_key=('AAPL', 'MSFT'),
            action='prepare_open',
            intent_date=20240115
        )
        self.assertEqual(result1, 'accepted')
        
        # 同日重复提交
        result2 = self.cpm.submit_intent(
            pair_key=('AAPL', 'MSFT'),
            action='prepare_open',
            intent_date=20240115
        )
        self.assertEqual(result2, 'ignored_duplicate')
    
    def test_prepare_open_not_active_rejected(self):
        """测试非活跃配对开仓被拒绝"""
        result = self.cpm.submit_intent(
            pair_key=('IBM', 'ORCL'),  # 不在活跃列表中
            action='prepare_open',
            intent_date=20240115
        )
        
        self.assertEqual(result, 'rejected')
        self.algorithm.Error.assert_called_with(
            '[CPM] 拒绝(NOT_ACTIVE): (\'IBM\', \'ORCL\') 不在当前活跃列表'
        )
    
    def test_prepare_open_has_instance_rejected(self):
        """测试已有实例的配对再次开仓被拒绝"""
        # 第一次开仓
        result1 = self.cpm.submit_intent(
            pair_key=('AAPL', 'MSFT'),
            action='prepare_open',
            intent_date=20240115
        )
        self.assertEqual(result1, 'accepted')
        
        # 第二天尝试再次开仓（实例未平）
        self.cpm.cache_date = None  # 清除缓存日期
        result2 = self.cpm.submit_intent(
            pair_key=('AAPL', 'MSFT'),
            action='prepare_open',
            intent_date=20240116
        )
        self.assertEqual(result2, 'rejected')
    
    def test_prepare_close_accepted(self):
        """测试平仓意图被接受"""
        # 先开仓
        self.cpm.submit_intent(
            pair_key=('AAPL', 'MSFT'),
            action='prepare_open',
            intent_date=20240115
        )
        
        # 再平仓
        result = self.cpm.submit_intent(
            pair_key=('AAPL', 'MSFT'),
            action='prepare_close',
            intent_date=20240116
        )
        
        self.assertEqual(result, 'accepted')
        # 验证意图记录包含instance_id
        last_intent = self.cpm.intents_log[-1]
        self.assertEqual(last_intent['action'], 'prepare_close')
        self.assertEqual(last_intent['instance_id'], 1)
        self.assertEqual(last_intent['cycle_id'], 20240101)
    
    def test_prepare_close_no_position_ignored(self):
        """测试无仓位平仓被忽略"""
        result = self.cpm.submit_intent(
            pair_key=('GOOGL', 'META'),  # 没有开仓
            action='prepare_close',
            intent_date=20240115
        )
        
        self.assertEqual(result, 'ignored_no_position')
    
    def test_daily_cache_clearing(self):
        """测试日缓存自动清理"""
        # 第一天提交
        result1 = self.cpm.submit_intent(
            pair_key=('AAPL', 'MSFT'),
            action='prepare_open',
            intent_date=20240115
        )
        self.assertEqual(result1, 'accepted')
        
        # 第二天提交同样的意图（应该被接受，因为缓存已清理）
        result2 = self.cpm.submit_intent(
            pair_key=('GOOGL', 'META'),
            action='prepare_open',
            intent_date=20240116
        )
        self.assertEqual(result2, 'accepted')
        
        # 验证缓存已更新
        self.assertEqual(self.cpm.cache_date, 20240116)
    
    def test_conflict_same_day_rejected(self):
        """测试同日冲突意图被拒绝"""
        # 提交开仓意图
        result1 = self.cpm.submit_intent(
            pair_key=('AAPL', 'MSFT'),
            action='prepare_open',
            intent_date=20240115
        )
        self.assertEqual(result1, 'accepted')
        
        # 同日提交平仓意图（冲突）
        result2 = self.cpm.submit_intent(
            pair_key=('AAPL', 'MSFT'),
            action='prepare_close',
            intent_date=20240115
        )
        self.assertEqual(result2, 'rejected')
        self.algorithm.Error.assert_called_with(
            '[CPM] 拒绝(CONFLICT_SAME_DAY): (\'AAPL\', \'MSFT\') 已有prepare_open，又收到prepare_close'
        )
    
    def test_instance_counter_persistence(self):
        """测试实例计数器永不回退"""
        # 第一次开仓
        self.cpm.submit_intent(('AAPL', 'MSFT'), 'prepare_open', 20240115)
        self.assertEqual(self.cpm.instance_counters[('AAPL', 'MSFT')], 1)
        
        # 模拟平仓（删除open_instances）
        del self.cpm.open_instances[('AAPL', 'MSFT')]
        
        # 第二次开仓
        self.cpm.cache_date = None  # 清除缓存
        self.cpm.submit_intent(('AAPL', 'MSFT'), 'prepare_open', 20240116)
        
        # 计数器应该是2，不是1
        self.assertEqual(self.cpm.instance_counters[('AAPL', 'MSFT')], 2)
        self.assertEqual(self.cpm.open_instances[('AAPL', 'MSFT')]['instance_id'], 2)
    
    def test_cross_cycle_holding_eligibility(self):
        """测试跨期持仓的eligible标记"""
        # 第一轮开仓
        self.cpm.submit_intent(('AAPL', 'MSFT'), 'prepare_open', 20240115)
        
        # 新一轮选股，AAPL-MSFT不在新列表中但有持仓
        self.cpm.submit_modeled_pairs(
            cycle_id=20240201,
            pairs=[
                {'symbol1_value': 'GOOGL', 'symbol2_value': 'META', 'beta': 1.2, 'quality_score': 0.75}
            ]
        )
        
        # AAPL-MSFT应该保留但标记为not eligible
        pair_key = ('AAPL', 'MSFT')
        self.assertIn(pair_key, self.cpm.current_active)
        self.assertFalse(self.cpm.current_active[pair_key]['eligible_in_cycle'])
        
        # 尝试再次开仓应该被拒绝（NOT_ELIGIBLE）
        self.cpm.cache_date = None
        del self.cpm.open_instances[pair_key]  # 模拟已平仓
        
        result = self.cpm.submit_intent(pair_key, 'prepare_open', 20240201)
        self.assertEqual(result, 'rejected')


if __name__ == '__main__':
    unittest.main()