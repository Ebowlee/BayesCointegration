#!/usr/bin/env python
"""
测试运行脚本
运行所有测试或指定的测试模块
"""
import sys
import unittest
import argparse

def main():
    parser = argparse.ArgumentParser(description='运行策略测试')
    parser.add_argument('--unit', action='store_true', help='只运行单元测试')
    parser.add_argument('--integration', action='store_true', help='只运行集成测试')
    parser.add_argument('--verbose', '-v', action='store_true', help='详细输出')
    parser.add_argument('module', nargs='?', help='指定测试模块 (例如: test_order_tracker)')
    
    args = parser.parse_args()
    
    # 确定测试目录
    if args.unit:
        test_dir = 'tests/unit'
    elif args.integration:
        test_dir = 'tests/integration'
    else:
        test_dir = 'tests'
    
    # 配置测试运行器
    verbosity = 2 if args.verbose else 1
    
    if args.module:
        # 运行指定模块
        if args.unit:
            module_path = f'tests.unit.{args.module}'
        elif args.integration:
            module_path = f'tests.integration.{args.module}'
        else:
            # 尝试在两个目录中查找
            try:
                suite = unittest.defaultTestLoader.loadTestsFromName(f'tests.unit.{args.module}')
                module_path = f'tests.unit.{args.module}'
            except:
                module_path = f'tests.integration.{args.module}'
        
        suite = unittest.defaultTestLoader.loadTestsFromName(module_path)
    else:
        # 运行所有测试
        suite = unittest.defaultTestLoader.discover(test_dir)
    
    # 运行测试
    runner = unittest.TextTestRunner(verbosity=verbosity)
    result = runner.run(suite)
    
    # 返回状态码
    return 0 if result.wasSuccessful() else 1

if __name__ == '__main__':
    sys.exit(main())