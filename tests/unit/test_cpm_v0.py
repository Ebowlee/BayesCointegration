import unittest
from datetime import datetime
from AlgorithmImports import *
from src.CentralPairManager import CentralPairManager


class MockAlgorithm:
    """模拟算法对象"""
    def __init__(self):
        self.messages = []
    
    def Debug(self, message):
        self.messages.append(('DEBUG', message))
    
    def Error(self, message):
        self.messages.append(('ERROR', message))


class TestCPMv0(unittest.TestCase):
    """CPM v0 最小用例测试"""
    
    def setUp(self):
        """每个测试前初始化"""
        self.algo = MockAlgorithm()
        self.cpm = CentralPairManager(self.algo)
    
    def test_basic_submission(self):
        """基础提交测试"""
        pairs = [
            {'symbol1_value': 'AAPL', 'symbol2_value': 'MSFT', 'beta': 0.9, 'quality_score': 0.8},
            {'symbol1_value': 'GOOGL', 'symbol2_value': 'META', 'beta': 1.1, 'quality_score': 0.7}
        ]
        
        self.cpm.submit_modeled_pairs(20250801, pairs)
        
        # 验证存储
        self.assertEqual(len(self.cpm.current_active), 2)
        key1 = ('AAPL', 'MSFT')  # 已排序
        self.assertIn(key1, self.cpm.current_active)
        self.assertEqual(self.cpm.current_active[key1]['beta'], 0.9)
    
    def test_case_A_same_cycle_param_change_rejected(self):
        """用例A（修正）：同一cycle二次提交，集合相同但beta/quality不同 - 应拒绝"""
        # 第一次提交
        pairs_v1 = [
            {'symbol1_value': 'AAPL', 'symbol2_value': 'MSFT', 'beta': 0.9, 'quality_score': 0.8},
            {'symbol1_value': 'GOOGL', 'symbol2_value': 'META', 'beta': 1.1, 'quality_score': 0.7}
        ]
        self.cpm.submit_modeled_pairs(20250801, pairs_v1)
        
        # 记录初始状态
        initial_active = self.cpm.current_active.copy()
        initial_pairs = self.cpm.last_cycle_pairs.copy()
        
        # 第二次提交 - 相同配对，不同参数
        pairs_v2 = [
            {'symbol1_value': 'MSFT', 'symbol2_value': 'AAPL', 'beta': 0.95, 'quality_score': 0.85},  # 参数不同
            {'symbol1_value': 'GOOGL', 'symbol2_value': 'META', 'beta': 1.2, 'quality_score': 0.75}
        ]
        self.cpm.submit_modeled_pairs(20250801, pairs_v2)
        
        # 验证：应有错误（拒绝修改）
        error_msgs = [msg for level, msg in self.algo.messages if level == 'ERROR']
        self.assertTrue(any('拒绝修改已冻结的cycle' in msg for msg in error_msgs))
        
        # 验证：状态未变（严格冻结）
        self.assertEqual(self.cpm.current_active, initial_active)
        self.assertEqual(self.cpm.last_cycle_pairs, initial_pairs)
        
        # 验证：beta/quality未被修改
        key1 = ('AAPL', 'MSFT')
        self.assertEqual(self.cpm.current_active[key1]['beta'], 0.9)  # 保持原值
        self.assertEqual(self.cpm.current_active[key1]['quality_score'], 0.8)  # 保持原值
    
    def test_case_B_cycle_rollback_allowed(self):
        """用例B（修正）：允许cycle回退（无回退检查）"""
        # 先提交20250801
        pairs1 = [
            {'symbol1_value': 'AAPL', 'symbol2_value': 'MSFT', 'beta': 0.9, 'quality_score': 0.8}
        ]
        self.cpm.submit_modeled_pairs(20250801, pairs1)
        
        # 提交20250701（回退） - 应该被允许
        pairs2 = [
            {'symbol1_value': 'GOOGL', 'symbol2_value': 'META', 'beta': 1.1, 'quality_score': 0.7}
        ]
        self.cpm.submit_modeled_pairs(20250701, pairs2)
        
        # 验证：无错误
        error_msgs = [msg for level, msg in self.algo.messages if level == 'ERROR']
        self.assertEqual(len(error_msgs), 0, "不应有错误（允许回退）")
        
        # 验证：状态已更新为新的cycle
        self.assertEqual(self.cpm.last_cycle_id, 20250701)
        self.assertEqual(len(self.cpm.current_active), 1)
        self.assertIn(('GOOGL', 'META'), self.cpm.current_active)
    
    def test_idempotent_exact_same(self):
        """幂等测试：完全相同的重复提交"""
        pairs = [
            {'symbol1_value': 'AAPL', 'symbol2_value': 'MSFT', 'beta': 0.9, 'quality_score': 0.8}
        ]
        
        # 第一次提交
        self.cpm.submit_modeled_pairs(20250801, pairs)
        initial_active = self.cpm.current_active.copy()
        
        # 第二次提交 - 完全相同
        self.cpm.submit_modeled_pairs(20250801, pairs)
        
        # 验证：有幂等日志
        debug_msgs = [msg for level, msg in self.algo.messages if level == 'DEBUG']
        self.assertTrue(any('幂等重复提交' in msg for msg in debug_msgs))
        
        # 验证：状态未变
        self.assertEqual(self.cpm.current_active, initial_active)
    
    def test_batch_duplicate(self):
        """批内重复测试"""
        pairs = [
            {'symbol1_value': 'AAPL', 'symbol2_value': 'MSFT', 'beta': 0.9, 'quality_score': 0.8},
            {'symbol1_value': 'MSFT', 'symbol2_value': 'AAPL', 'beta': 0.95, 'quality_score': 0.85}  # 重复
        ]
        
        with self.assertRaises(ValueError) as context:
            self.cpm.submit_modeled_pairs(20250801, pairs)
        
        self.assertIn("Duplicate pair_key", str(context.exception))
    
    def test_cross_cycle_cleanup(self):
        """跨周期清理测试"""
        # 第一轮
        pairs1 = [
            {'symbol1_value': 'AAPL', 'symbol2_value': 'MSFT', 'beta': 0.9, 'quality_score': 0.8},
            {'symbol1_value': 'GOOGL', 'symbol2_value': 'META', 'beta': 1.1, 'quality_score': 0.7}
        ]
        self.cpm.submit_modeled_pairs(20250801, pairs1)
        
        # 第二轮 - 只保留一个
        pairs2 = [
            {'symbol1_value': 'AAPL', 'symbol2_value': 'MSFT', 'beta': 0.95, 'quality_score': 0.85}
        ]
        self.cpm.submit_modeled_pairs(20250901, pairs2)
        
        # 验证：GOOGL-META被删除
        self.assertEqual(len(self.cpm.current_active), 1)
        self.assertNotIn(('GOOGL', 'META'), self.cpm.current_active)
        
        # 验证：AAPL-MSFT参数已更新（新cycle）
        key1 = ('AAPL', 'MSFT')
        self.assertEqual(self.cpm.current_active[key1]['beta'], 0.95)
        self.assertEqual(self.cpm.current_active[key1]['quality_score'], 0.85)


if __name__ == '__main__':
    unittest.main()