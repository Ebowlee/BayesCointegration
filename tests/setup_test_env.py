"""
测试环境设置
在导入任何源代码之前运行此模块，以替换 AlgorithmImports
"""
import sys
import os

# 添加项目根目录到路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# 用我们的模拟版本替换 AlgorithmImports
from tests.mocks import algorithm_imports
sys.modules['AlgorithmImports'] = algorithm_imports