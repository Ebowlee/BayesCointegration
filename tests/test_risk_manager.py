"""
RiskManager单元测试

测试风控调度器的规则注册、优先级排序、触发返回等功能
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.risk.RiskManager import RiskManager


# ========== Mock对象 ==========

class MockPortfolio:
    """模拟Portfolio对象"""
    def __init__(self, total_value):
        self.TotalPortfolioValue = total_value


class MockConfig:
    """模拟Config对象"""
    def __init__(self, initial_cash, enabled=True, blowup_enabled=True):
        self.main = {'cash': initial_cash}
        self.risk_management = {
            'enabled': enabled,
            'portfolio_rules': {
                'account_blowup': {
                    'enabled': blowup_enabled,
                    'priority': 100,
                    'threshold': 0.25,
                    'cooldown_days': 36500,
                    'action': 'portfolio_liquidate_all'
                }
            },
            'pair_rules': {}
        }


class MockAlgorithm:
    """模拟Algorithm对象"""
    def __init__(self, initial_cash, current_value, config=None):
        self.config = config or MockConfig(initial_cash)
        self.Portfolio = MockPortfolio(current_value)
        self.Time = datetime(2024, 1, 15)
        self.debug_messages = []

    def Debug(self, message):
        """捕获Debug消息"""
        self.debug_messages.append(message)
        print(f"  [Debug] {message}")


# ========== 测试函数 ==========

def test_rule_registration():
    """测试1: 规则注册正确"""
    print("测试1: Portfolio规则注册...")

    algo = MockAlgorithm(initial_cash=1000000, current_value=900000)
    risk_manager = RiskManager(algo, algo.config)

    # 验证规则数量
    print(f"  Portfolio规则数量: {len(risk_manager.portfolio_rules)} (预期: 1)")

    # 验证规则类型
    if len(risk_manager.portfolio_rules) > 0:
        rule = risk_manager.portfolio_rules[0]
        rule_name = rule.__class__.__name__
        print(f"  规则类型: {rule_name} (预期: AccountBlowupRule)")
        print(f"  规则优先级: {rule.priority} (预期: 100)")

        if rule_name == 'AccountBlowupRule' and rule.priority == 100:
            print("  [PASS]")
            return True

    print("  [FAIL]")
    return False


def test_triggered_returns_action():
    """测试2: 触发规则返回正确action"""
    print("\n测试2: 触发规则返回action...")

    # 初始100万，当前60万，亏损40%（会触发）
    algo = MockAlgorithm(initial_cash=1000000, current_value=600000)
    risk_manager = RiskManager(algo, algo.config)

    action, triggered_rules = risk_manager.check_portfolio_risks()

    print(f"  返回action: {action} (预期: portfolio_liquidate_all)")
    print(f"  触发规则数量: {len(triggered_rules)} (预期: 1)")

    if action == 'portfolio_liquidate_all' and len(triggered_rules) == 1:
        rule, description = triggered_rules[0]
        print(f"  触发描述: {description}")
        print("  [PASS]")
        return True
    else:
        print("  [FAIL]")
        return False


def test_not_triggered_returns_none():
    """测试3: 未触发返回None"""
    print("\n测试3: 未触发情况...")

    # 初始100万，当前90万，亏损10%（未触发）
    algo = MockAlgorithm(initial_cash=1000000, current_value=900000)
    risk_manager = RiskManager(algo, algo.config)

    action, triggered_rules = risk_manager.check_portfolio_risks()

    print(f"  返回action: {action} (预期: None)")
    print(f"  触发规则数量: {len(triggered_rules)} (预期: 0)")

    if action is None and len(triggered_rules) == 0:
        print("  [PASS]")
        return True
    else:
        print("  [FAIL]")
        return False


def test_global_disabled():
    """测试4: 全局禁用不检查规则"""
    print("\n测试4: 全局禁用...")

    # 创建禁用的config
    config = MockConfig(initial_cash=1000000, enabled=False)
    algo = MockAlgorithm(initial_cash=1000000, current_value=600000, config=config)
    risk_manager = RiskManager(algo, algo.config)

    # 即使亏损40%也不应该触发
    action, triggered_rules = risk_manager.check_portfolio_risks()

    print(f"  返回action: {action} (预期: None)")
    print(f"  触发规则数量: {len(triggered_rules)} (预期: 0)")
    print(f"  注册规则数量: {len(risk_manager.portfolio_rules)} (预期: 0)")

    if action is None and len(triggered_rules) == 0 and len(risk_manager.portfolio_rules) == 0:
        print("  [PASS]")
        return True
    else:
        print("  [FAIL]")
        return False


def test_cooldown_skipped():
    """测试5: 冷却期内的规则被跳过"""
    print("\n测试5: 冷却期跳过...")

    # 初始100万，当前60万，亏损40%（会触发）
    algo = MockAlgorithm(initial_cash=1000000, current_value=600000)
    risk_manager = RiskManager(algo, algo.config)

    # 第一次检查 - 应该触发
    action1, triggered1 = risk_manager.check_portfolio_risks()
    print(f"  首次检查: {action1} (预期: portfolio_liquidate_all)")

    # 激活冷却期
    if triggered1:
        rule, desc = triggered1[0]
        rule.activate_cooldown()
        print(f"  激活冷却期: {rule.cooldown_until}")

    # 第二次检查 - 应该被跳过
    action2, triggered2 = risk_manager.check_portfolio_risks()
    print(f"  冷却期内检查: {action2} (预期: None)")
    print(f"  触发数量: {len(triggered2)} (预期: 0)")

    if action1 == 'portfolio_liquidate_all' and action2 is None and len(triggered2) == 0:
        print("  [PASS]")
        return True
    else:
        print("  [FAIL]")
        return False


def test_triggered_rules_list():
    """测试6: 返回完整的触发规则列表"""
    print("\n测试6: 触发规则列表...")

    # 初始100万，当前60万，亏损40%（会触发）
    algo = MockAlgorithm(initial_cash=1000000, current_value=600000)
    risk_manager = RiskManager(algo, algo.config)

    action, triggered_rules = risk_manager.check_portfolio_risks()

    print(f"  触发规则数量: {len(triggered_rules)} (预期: 1)")

    if len(triggered_rules) > 0:
        rule, description = triggered_rules[0]
        print(f"  规则类型: {rule.__class__.__name__}")
        print(f"  规则优先级: {rule.priority}")
        print(f"  描述: {description}")

        # 验证元组结构
        from src.risk.base import RiskRule
        if isinstance(rule, RiskRule) and isinstance(description, str) and len(description) > 0:
            print("  [PASS]")
            return True

    print("  [FAIL]")
    return False


def test_rule_disabled():
    """测试7: 禁用规则不会被注册"""
    print("\n测试7: 规则禁用...")

    # 创建禁用AccountBlowupRule的config
    config = MockConfig(initial_cash=1000000, enabled=True, blowup_enabled=False)
    algo = MockAlgorithm(initial_cash=1000000, current_value=600000, config=config)
    risk_manager = RiskManager(algo, algo.config)

    # 即使亏损40%也不应该触发（因为规则未注册）
    action, triggered_rules = risk_manager.check_portfolio_risks()

    print(f"  返回action: {action} (预期: None)")
    print(f"  注册规则数量: {len(risk_manager.portfolio_rules)} (预期: 0)")

    if action is None and len(risk_manager.portfolio_rules) == 0:
        print("  [PASS]")
        return True
    else:
        print("  [FAIL]")
        return False


def test_get_registered_rules_info():
    """测试8: 获取规则信息"""
    print("\n测试8: 获取规则信息...")

    algo = MockAlgorithm(initial_cash=1000000, current_value=900000)
    risk_manager = RiskManager(algo, algo.config)

    info = risk_manager.get_registered_rules_info()

    print(f"  Portfolio规则: {info['portfolio']}")
    print(f"  Pair规则: {info['pair']}")

    # 验证信息结构
    if len(info['portfolio']) == 1:
        rule_info = info['portfolio'][0]
        checks = [
            rule_info['name'] == 'AccountBlowupRule',
            rule_info['priority'] == 100,
            rule_info['enabled'] == True,
            rule_info['in_cooldown'] == False
        ]

        if all(checks):
            print("  [PASS]")
            return True

    print("  [FAIL]")
    return False


# ========== 主测试运行器 ==========

def run_all_tests():
    """运行所有测试"""
    print("=" * 60)
    print("RiskManager单元测试")
    print("=" * 60)

    tests = [
        test_rule_registration,
        test_triggered_returns_action,
        test_not_triggered_returns_none,
        test_global_disabled,
        test_cooldown_skipped,
        test_triggered_rules_list,
        test_rule_disabled,
        test_get_registered_rules_info
    ]

    results = []
    for test_func in tests:
        try:
            result = test_func()
            results.append(result)
        except Exception as e:
            print(f"  [ERROR] 测试异常: {e}")
            import traceback
            traceback.print_exc()
            results.append(False)

    # 总结
    print("\n" + "=" * 60)
    passed = sum(results)
    total = len(results)
    print(f"测试结果: {passed}/{total} 通过")

    if passed == total:
        print("[SUCCESS] 所有测试通过!")
    else:
        print(f"[FAILURE] {total - passed} 个测试失败")

    print("=" * 60)

    return passed == total


if __name__ == "__main__":
    success = run_all_tests()
    exit(0 if success else 1)
