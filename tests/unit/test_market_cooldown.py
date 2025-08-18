"""
测试市场冷静期强制平仓逻辑
"""

import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from AlgorithmImports import *
from src.alpha.SignalGenerator import SignalGenerator
from src.alpha.AlphaState import AlphaModelState


class TestMarketCooldown(unittest.TestCase):
    """测试市场冷静期功能"""
    
    def setUp(self):
        """设置测试环境"""
        self.algorithm = MagicMock()
        self.algorithm.Time = datetime(2024, 1, 15)
        self.algorithm.Debug = MagicMock()
        
        self.config = {
            'entry_threshold': 1.2,
            'exit_threshold': 0.3,
            'upper_limit': 3.0,
            'lower_limit': -3.0,
            'flat_signal_duration_days': 1,
            'entry_signal_duration_days': 2,
            'market_severe_threshold': 0.05,
            'market_cooldown_days': 14
        }
        
        self.state = AlphaModelState()
        self.cpm = MagicMock()
        
        self.signal_generator = SignalGenerator(
            self.algorithm, 
            self.config, 
            self.state,
            self.cpm
        )
        
    def test_market_cooldown_forces_close(self):
        """测试市场冷静期强制平仓"""
        # 设置市场冷静期
        self.signal_generator.market_cooldown_until = datetime(2024, 1, 29).date()
        
        # 模拟一个有持仓的配对
        symbol1 = MagicMock()
        symbol1.Value = "AAPL"
        symbol2 = MagicMock()
        symbol2.Value = "MSFT"
        
        pair = {
            'symbol1': symbol1,
            'symbol2': symbol2,
            'zscore': 2.5,  # 高z-score，正常情况不会平仓
            'alpha_mean': 0.1,
            'beta_mean': 0.9,
            'quality_score': 0.8
        }
        
        # 设置CPM返回该配对有持仓
        self.cpm.get_trading_pairs.return_value = {('AAPL', 'MSFT')}
        
        # 调用信号生成
        insights = self.signal_generator._generate_pair_signals(pair, is_market_cooldown=True)
        
        # 验证生成了平仓信号
        self.assertIsNotNone(insights)
        self.assertTrue(len(insights) > 0)
        
        # 验证日志输出
        self.algorithm.Debug.assert_called()
        debug_calls = [str(call) for call in self.algorithm.Debug.call_args_list]
        self.assertTrue(any('市场风控' in str(call) and '强制平仓' in str(call) for call in debug_calls))
        
    def test_market_cooldown_no_new_positions(self):
        """测试市场冷静期不生成建仓信号"""
        # 设置市场冷静期
        self.signal_generator.market_cooldown_until = datetime(2024, 1, 29).date()
        
        # 模拟一个无持仓的配对
        symbol1 = MagicMock()
        symbol1.Value = "GOOGL"
        symbol2 = MagicMock()
        symbol2.Value = "META"
        
        pair = {
            'symbol1': symbol1,
            'symbol2': symbol2,
            'zscore': 2.0,  # 超过建仓阈值
            'alpha_mean': 0.1,
            'beta_mean': 0.9,
            'quality_score': 0.8
        }
        
        # 设置CPM返回该配对无持仓
        self.cpm.get_trading_pairs.return_value = set()
        self.cpm.get_excluded_pairs.return_value = set()
        
        # 调用信号生成
        insights = self.signal_generator._generate_pair_signals(pair, is_market_cooldown=True)
        
        # 验证没有生成任何信号
        self.assertEqual(insights, [])
        
    def test_normal_market_conditions(self):
        """测试正常市场条件下的信号生成"""
        # 无市场冷静期
        self.signal_generator.market_cooldown_until = None
        
        # 模拟一个配对
        symbol1 = MagicMock()
        symbol1.Value = "TSLA"
        symbol2 = MagicMock()
        symbol2.Value = "GM"
        
        pair = {
            'symbol1': symbol1,
            'symbol2': symbol2,
            'zscore': 1.5,  # 超过建仓阈值
            'alpha_mean': 0.1,
            'beta_mean': 0.9,
            'quality_score': 0.8
        }
        
        # 设置CPM返回
        self.cpm.get_trading_pairs.return_value = set()
        self.cpm.get_excluded_pairs.return_value = set()
        
        # 调用信号生成（正常市场）
        insights = self.signal_generator._generate_pair_signals(pair, is_market_cooldown=False)
        
        # 验证生成了建仓信号
        self.assertIsNotNone(insights)
        self.assertTrue(len(insights) > 0)


if __name__ == '__main__':
    unittest.main()