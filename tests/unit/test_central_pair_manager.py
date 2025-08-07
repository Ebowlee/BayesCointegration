"""
CentralPairManager单元测试
测试核心功能：冷却期、单股票限制、全局配对数限制
"""

import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, Mock
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.CentralPairManager import CentralPairManager, PairState, PairInfo


class TestCentralPairManager(unittest.TestCase):
    """测试CentralPairManager核心功能"""
    
    def setUp(self):
        """设置测试环境"""
        # 创建mock算法实例
        self.algorithm = MagicMock()
        self.algorithm.Time = datetime(2024, 7, 1)
        self.algorithm.Debug = Mock()
        
        # 配置
        self.config = {
            'enable_central_pair_manager': True,
            'max_pairs': 2,  # 测试用较小的值
            'max_symbol_repeats': 1,
            'cooldown_days': 7,
            'max_holding_days': 30
        }
        
        # 创建CentralPairManager实例
        self.cpm = CentralPairManager(self.algorithm, self.config)
        
        # 创建mock symbols
        self.create_mock_symbols()
    
    def create_mock_symbols(self):
        """创建测试用的mock symbols"""
        self.aapl = Mock()
        self.aapl.Value = "AAPL"
        
        self.msft = Mock()
        self.msft.Value = "MSFT"
        
        self.googl = Mock()
        self.googl.Value = "GOOGL"
        
        self.amzn = Mock()
        self.amzn.Value = "AMZN"
        
        self.tsla = Mock()
        self.tsla.Value = "TSLA"
    
    def test_basic_approval(self):
        """测试基本的配对批准流程"""
        candidates = [(self.aapl, self.msft), (self.googl, self.amzn)]
        
        # 评估候选配对
        approved = self.cpm.evaluate_candidates(candidates)
        
        # 应该批准所有配对（没有违反任何规则）
        self.assertEqual(len(approved), 2)
        self.assertIn((self.aapl, self.msft), approved)
        self.assertIn((self.googl, self.amzn), approved)
    
    def test_max_pairs_limit(self):
        """测试全局配对数限制"""
        # 先批准2个配对（达到max_pairs=2）
        candidates1 = [(self.aapl, self.msft), (self.googl, self.amzn)]
        approved1 = self.cpm.evaluate_candidates(candidates1)
        self.assertEqual(len(approved1), 2)
        
        # 登记为活跃
        for symbol1, symbol2 in approved1:
            self.cpm.register_entry(symbol1, symbol2)
        
        # 尝试添加第3个配对
        candidates2 = [(self.tsla, self.aapl)]  # AAPL已经在配对中
        approved2 = self.cpm.evaluate_candidates(candidates2)
        
        # 应该拒绝（超过max_pairs限制）
        self.assertEqual(len(approved2), 0)
    
    def test_single_symbol_repeat_limit(self):
        """测试单股票配对限制"""
        # 先批准一个包含AAPL的配对
        candidates1 = [(self.aapl, self.msft)]
        approved1 = self.cpm.evaluate_candidates(candidates1)
        self.assertEqual(len(approved1), 1)
        
        # 登记为活跃
        self.cpm.register_entry(self.aapl, self.msft)
        
        # 尝试添加另一个包含AAPL的配对
        candidates2 = [(self.aapl, self.googl)]
        approved2 = self.cpm.evaluate_candidates(candidates2)
        
        # 应该拒绝（AAPL已经参与其他配对）
        self.assertEqual(len(approved2), 0)
        
        # 尝试添加不包含AAPL的配对
        candidates3 = [(self.googl, self.amzn)]
        approved3 = self.cpm.evaluate_candidates(candidates3)
        
        # 应该批准
        self.assertEqual(len(approved3), 1)
    
    def test_cooldown_period(self):
        """测试冷却期机制"""
        # 批准并建仓一个配对
        candidates1 = [(self.aapl, self.msft)]
        approved1 = self.cpm.evaluate_candidates(candidates1)
        self.cpm.register_entry(self.aapl, self.msft)
        
        # 平仓该配对
        self.cpm.register_exit(self.aapl, self.msft)
        
        # 立即尝试重新建仓（在冷却期内）
        self.algorithm.Time = datetime(2024, 7, 3)  # 2天后
        candidates2 = [(self.aapl, self.msft)]
        approved2 = self.cpm.evaluate_candidates(candidates2)
        
        # 应该拒绝（在7天冷却期内）
        self.assertEqual(len(approved2), 0)
        
        # 等待冷却期结束
        self.algorithm.Time = datetime(2024, 7, 9)  # 8天后
        candidates3 = [(self.aapl, self.msft)]
        approved3 = self.cpm.evaluate_candidates(candidates3)
        
        # 应该批准（冷却期已过）
        self.assertEqual(len(approved3), 1)
    
    def test_get_active_pairs(self):
        """测试获取活跃配对信息"""
        # 批准并建仓两个配对
        candidates = [(self.aapl, self.msft), (self.googl, self.amzn)]
        approved = self.cpm.evaluate_candidates(candidates)
        
        for symbol1, symbol2 in approved:
            self.cpm.register_entry(symbol1, symbol2)
        
        # 获取活跃配对
        active_pairs = self.cpm.get_active_pairs()
        
        # 验证结果
        self.assertEqual(len(active_pairs), 2)
        
        # 验证配对信息
        pairs = [info['pair'] for info in active_pairs]
        self.assertIn((self.aapl, self.msft), pairs)
        self.assertIn((self.googl, self.amzn), pairs)
        
        # 验证持仓天数（刚建仓应该是0天）
        for info in active_pairs:
            self.assertEqual(info['holding_days'], 0)
    
    def test_state_transitions(self):
        """测试状态转换"""
        # 创建配对信息
        pair_info = PairInfo(self.aapl, self.msft)
        
        # 初始状态应该是CANDIDATE
        self.assertEqual(pair_info.state, PairState.CANDIDATE)
        
        # 记录状态转换
        self.cpm._update_state(pair_info, PairState.APPROVED)
        self.assertEqual(pair_info.state, PairState.APPROVED)
        
        self.cpm._update_state(pair_info, PairState.ACTIVE)
        self.assertEqual(pair_info.state, PairState.ACTIVE)
        
        self.cpm._update_state(pair_info, PairState.CLOSING)
        self.assertEqual(pair_info.state, PairState.CLOSING)
        
        self.cpm._update_state(pair_info, PairState.COOLDOWN)
        self.assertEqual(pair_info.state, PairState.COOLDOWN)
        
        # 验证状态历史
        self.assertEqual(len(pair_info.state_history), 4)
        self.assertEqual(pair_info.state_history[0]['from'], PairState.CANDIDATE)
        self.assertEqual(pair_info.state_history[0]['to'], PairState.APPROVED)
    
    def test_disabled_mode(self):
        """测试禁用模式"""
        # 创建禁用的CentralPairManager
        config_disabled = self.config.copy()
        config_disabled['enable_central_pair_manager'] = False
        cpm_disabled = CentralPairManager(self.algorithm, config_disabled)
        
        # 在禁用模式下，应该批准所有候选
        candidates = [(self.aapl, self.msft), (self.googl, self.amzn), 
                      (self.tsla, self.aapl)]  # 故意违反规则
        approved = cpm_disabled.evaluate_candidates(candidates)
        
        # 应该批准所有（禁用模式不检查）
        self.assertEqual(len(approved), 3)


if __name__ == '__main__':
    unittest.main()