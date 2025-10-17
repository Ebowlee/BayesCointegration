"""
RiskRule基类功能测试

验证抽象基类、优先级、冷却期等核心功能
"""

import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.RiskManagement.base import RiskRule
from typing import Tuple
from datetime import datetime


# ========== Mock对象 ==========

class MockAlgorithm:
    """模拟算法对象"""
    def __init__(self):
        self.Time = datetime(2024, 1, 15)


class TestRuleComplete(RiskRule):
    """完整实现的测试规则"""
    def check(self, **kwargs) -> Tuple[bool, str]:
        return False, ""

    def get_action(self) -> str:
        return self.config.get('action', 'test_action')


class TestRuleIncomplete(RiskRule):
    """不完整实现的测试规则（只实现check）"""
    def check(self, **kwargs) -> Tuple[bool, str]:
        return False, ""
    # 故意不实现 get_action()


class PortfolioTestRule(RiskRule):
    """Portfolio规则示例（不使用kwargs）"""
    def check(self, **kwargs) -> Tuple[bool, str]:
        return False, "Portfolio层面检测"

    def get_action(self) -> str:
        return 'liquidate_all'


class PairTestRule(RiskRule):
    """Pair规则示例（使用kwargs['pair']）"""
    def check(self, **kwargs) -> Tuple[bool, str]:
        pair = kwargs.get('pair')
        if pair:
            return True, f"检测到配对: {pair}"
        return False, ""

    def get_action(self) -> str:
        return 'close_pair'


# ========== 测试函数 ==========

def test_abstract_class_cannot_instantiate():
    """测试1: 抽象类不能直接实例化"""
    print("测试1: 抽象类机制...")
    try:
        rule = RiskRule(None, {})
        print("  [FAIL] 失败: 抽象类不应该能直接实例化")
        return False
    except TypeError as e:
        print(f"  [PASS] 通过: {e}")
        return True


def test_abstract_methods_must_implement():
    """测试2: 必须实现所有抽象方法"""
    print("\n测试2: 抽象方法强制实现...")
    try:
        rule = TestRuleIncomplete(None, {})
        print("  [FAIL] 失败: 应该报错（未实现get_action）")
        return False
    except TypeError as e:
        print(f"  [PASS] 通过: {e}")
        return True


def test_complete_implementation():
    """测试3: 完整实现的规则可以正常工作"""
    print("\n测试3: 完整规则实现...")

    config = {
        'enabled': True,
        'priority': 100,
        'cooldown_days': 10,
        'action': 'liquidate_all'
    }

    rule = TestRuleComplete(MockAlgorithm(), config)

    # 验证属性
    checks = [
        (rule.enabled == True, f"enabled: {rule.enabled} (预期: True)"),
        (rule.priority == 100, f"priority: {rule.priority} (预期: 100)"),
        (rule.get_action() == 'liquidate_all', f"action: {rule.get_action()} (预期: liquidate_all)"),
        (rule.is_in_cooldown() == False, f"冷却状态: {rule.is_in_cooldown()} (预期: False)")
    ]

    all_pass = True
    for passed, msg in checks:
        print(f"  {msg}")
        if not passed:
            all_pass = False

    if all_pass:
        print("  [PASS]")
    else:
        print("  [FAIL]")

    return all_pass


def test_cooldown_mechanism():
    """测试4: 冷却期机制"""
    print("\n测试4: 冷却期机制...")

    config = {
        'enabled': True,
        'priority': 100,
        'cooldown_days': 10,
        'action': 'test'
    }

    rule = TestRuleComplete(MockAlgorithm(), config)

    # 激活前
    before_cooldown = rule.is_in_cooldown()
    print(f"  激活前: {before_cooldown} (预期: False)")

    # 激活冷却
    rule.activate_cooldown()

    # 激活后
    after_cooldown = rule.is_in_cooldown()
    print(f"  激活后: {after_cooldown} (预期: True)")
    print(f"  冷却至: {rule.cooldown_until} (预期: 2024-01-25 00:00:00)")

    # 验证
    expected_date = datetime(2024, 1, 25)
    date_match = rule.cooldown_until == expected_date

    if not before_cooldown and after_cooldown and date_match:
        print("  [PASS]")
        return True
    else:
        print("  [FAIL]")
        return False


def test_priority_default():
    """测试5: Priority默认值"""
    print("\n测试5: Priority默认值...")

    config_no_priority = {
        'enabled': True,
        'action': 'test'
        # 不设置priority
    }

    rule = TestRuleComplete(MockAlgorithm(), config_no_priority)
    print(f"  未设置priority: {rule.priority} (预期: 50)")

    if rule.priority == 50:
        print("  [PASS]")
        return True
    else:
        print("  [FAIL]")
        return False


def test_optional_cooldown():
    """测试6: 可选的冷却期配置"""
    print("\n测试6: 无冷却期配置...")

    config_no_cooldown = {
        'enabled': True,
        'priority': 80,
        'action': 'test'
        # 不设置cooldown_days
    }

    rule = TestRuleComplete(MockAlgorithm(), config_no_cooldown)
    rule.activate_cooldown()

    in_cooldown = rule.is_in_cooldown()
    print(f"  无冷却配置时: {in_cooldown} (预期: False)")

    if not in_cooldown:
        print("  [PASS]")
        return True
    else:
        print("  [FAIL]")
        return False


def test_portfolio_and_pair_compatibility():
    """测试7: Portfolio和Pair规则接口兼容性"""
    print("\n测试7: Portfolio和Pair规则接口兼容性...")

    p_rule = PortfolioTestRule(MockAlgorithm(), {
        'enabled': True,
        'priority': 100
    })

    pair_rule = PairTestRule(MockAlgorithm(), {
        'enabled': True,
        'priority': 50
    })

    # Portfolio规则调用（不传参）
    p_triggered, p_desc = p_rule.check()
    print(f"  Portfolio规则: {p_desc}")

    # Pair规则调用（传入pair）
    pair_triggered, pair_desc = pair_rule.check(pair="(AAPL, MSFT)")
    print(f"  Pair规则: {pair_desc} (triggered={pair_triggered})")

    # 验证
    if not p_triggered and pair_triggered and "(AAPL, MSFT)" in pair_desc:
        print("  [PASS]")
        return True
    else:
        print("  [FAIL]")
        return False


# ========== 主测试运行器 ==========

def run_all_tests():
    """运行所有测试"""
    print("=" * 60)
    print("RiskRule基类功能测试")
    print("=" * 60)

    tests = [
        test_abstract_class_cannot_instantiate,
        test_abstract_methods_must_implement,
        test_complete_implementation,
        test_cooldown_mechanism,
        test_priority_default,
        test_optional_cooldown,
        test_portfolio_and_pair_compatibility
    ]

    results = []
    for test_func in tests:
        try:
            result = test_func()
            results.append(result)
        except Exception as e:
            print(f"  [ERROR] 测试异常: {e}")
            results.append(False)

    # 总结
    print("\n" + "=" * 60)
    passed = sum(results)
    total = len(results)
    print(f"测试结果: {passed}/{total} 通过")

    if passed == total:
        print("[SUCCESS] 所有测试通过！")
    else:
        print(f"[FAILURE] {total - passed} 个测试失败")

    print("=" * 60)

    return passed == total


if __name__ == "__main__":
    success = run_all_tests()
    exit(0 if success else 1)
