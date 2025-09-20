"""
AlphaModel模块化包

该包包含贝叶斯协整策略的所有Alpha模型相关组件。
通过模块化设计，提高代码的可维护性和可测试性。
"""

# 导出主要接口
from .AlphaModel import BayesianCointegrationAlphaModel
from .AlphaState import AlphaModelState
from .DataProcessor import DataProcessor
from .CointegrationAnalyzer import CointegrationAnalyzer
from .BayesianModeler import BayesianModeler
from .SignalGenerator import SignalGenerator

__all__ = [
    'BayesianCointegrationAlphaModel',
    'AlphaModelState',
    'DataProcessor',
    'CointegrationAnalyzer',
    'BayesianModeler',
    'SignalGenerator'
]