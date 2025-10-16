"""
AccountBlowupRule单元测试

测试账户爆仓线风控规则的各种场景
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.RiskManagement.AccountBlowupRule import AccountBlowupRule


# ========== Mock对象 ==========

class MockPortfolio:
    """模拟Portfolio对象"""
    def __init__(self, total_value):
        self.TotalPortfolioValue = total_value


class MockConfig:
    """模拟Config对象"""
    def __init__(self, initial_cash):
        self.main = {'cash': initial_cash}
        self.risk_management = {
            'portfolio_rules': {
                'account_blowup': {
                    'enabled': True,
                    'priority': 100,
                    'threshold': 0.25,
                    'cooldown_days': 36500,
                    'action': 'portfolio_liquidate_all'
                }
            }
        }


class MockAlgorithm:
    """模拟Algorithm对象"""
    def __init__(self, initial_cash, current_value):
        self.config = MockConfig(initial_cash)
        self.Portfolio = MockPortfolio(current_value)
        self.Time = datetime(2024, 1, 15)


# ========== 测试函数 ==========

def test_normal_loss():
    """测试1: 正常亏损情况（未触发）"""
    print("测试1: 正常亏损10% (未触发)...")

    # 初始100万，当前90万，亏损10%
    algo = MockAlgorithm(initial_cash=1000000, current_value=900000)
    config = algo.config.risk_management['portfolio_rules']['account_blowup']
    rule = AccountBlowupRule(algo, config)

    triggered, description = rule.check()

    print(f"  触发状态: {triggered} (预期: False)")
    print(f"  描述: {description} (预期: 空)")

    if not triggered and description == "":
        print("  [PASS]")
        return True
    else:
        print("  [FAIL]")
        return False


def test_exact_threshold():
    """测试2: 刚好达到阈值（触发）"""
    print("\n测试2: 刚好亏损25% (触发)...")

    # 初始100万，当前75万，亏损25%
    algo = MockAlgorithm(initial_cash=1000000, current_value=750000)
    config = algo.config.risk_management['portfolio_rules']['account_blowup']
    rule = AccountBlowupRule(algo, config)

    triggered, description = rule.check()

    print(f"  触发状态: {triggered} (预期: True)")
    print(f"  描述: {description}")

    # 验证：应该触发，且描述包含关键信息
    if triggered and "账户爆仓" in description and "25.0%" in description:
        print("  [PASS]")
        return True
    else:
        print("  [FAIL]")
        return False


def test_severe_loss():
    """测试3: 严重亏损（触发）"""
    print("\n测试3: 严重亏损40% (触发)...")

    # 初始100万，当前60万，亏损40%
    algo = MockAlgorithm(initial_cash=1000000, current_value=600000)
    config = algo.config.risk_management['portfolio_rules']['account_blowup']
    rule = AccountBlowupRule(algo, config)

    triggered, description = rule.check()

    print(f"  触发状态: {triggered} (预期: True)")
    print(f"  描述: {description}")

    if triggered and "账户爆仓" in description and "40.0%" in description:
        print("  [PASS]")
        return True
    else:
        print("  [FAIL]")
        return False


def test_profit_situation():
    """测试4: 盈利情况（未触发）"""
    print("\n测试4: 盈利20% (未触发)...")

    # 初始100万，当前120万，盈利20%
    algo = MockAlgorithm(initial_cash=1000000, current_value=1200000)
    config = algo.config.risk_management['portfolio_rules']['account_blowup']
    rule = AccountBlowupRule(algo, config)

    triggered, description = rule.check()

    print(f"  触发状态: {triggered} (预期: False)")
    print(f"  描述: {description} (预期: 空)")

    if not triggered and description == "":
        print("  [PASS]")
        return True
    else:
        print("  [FAIL]")
        return False


def test_cooldown_mechanism():
    """测试5: 冷却期机制"""
    print("\n测试5: 冷却期机制...")

    # 初始100万，当前60万，亏损40%（会触发）
    algo = MockAlgorithm(initial_cash=1000000, current_value=600000)
    config = algo.config.risk_management['portfolio_rules']['account_blowup']
    rule = AccountBlowupRule(algo, config)

    # 第一次检查 - 应该触发
    triggered1, desc1 = rule.check()
    print(f"  首次检查: {triggered1} (预期: True)")

    # 激活冷却期
    rule.activate_cooldown()
    print(f"  激活冷却期: {rule.cooldown_until}")

    # 验证冷却期状态
    in_cooldown = rule.is_in_cooldown()
    print(f"  冷却状态: {in_cooldown} (预期: True)")

    # 第二次检查 - 应该被冷却期阻止
    triggered2, desc2 = rule.check()
    print(f"  冷却期内检查: {triggered2} (预期: False)")
    print(f"  描述: {desc2} (预期: 空)")

    # 验证冷却期时长（36500天约100年）
    expected_date = algo.Time + timedelta(days=36500)
    date_match = rule.cooldown_until == expected_date

    if triggered1 and in_cooldown and not triggered2 and desc2 == "" and date_match:
        print("  [PASS]")
        return True
    else:
        print("  [FAIL]")
        return False


def test_config_reading():
    """测试6: 配置读取正确性"""
    print("\n测试6: 配置参数读取...")

    algo = MockAlgorithm(initial_cash=1000000, current_value=900000)
    config = algo.config.risk_management['portfolio_rules']['account_blowup']
    rule = AccountBlowupRule(algo, config)

    # 验证各项配置
    checks = [
        (rule.enabled == True, f"enabled: {rule.enabled} (预期: True)"),
        (rule.priority == 100, f"priority: {rule.priority} (预期: 100)"),
        (rule.config['threshold'] == 0.25, f"threshold: {rule.config['threshold']} (预期: 0.25)"),
        (rule.config['cooldown_days'] == 36500, f"cooldown_days: {rule.config['cooldown_days']} (预期: 36500)"),
        (rule.get_action() == 'portfolio_liquidate_all', f"action: {rule.get_action()} (预期: portfolio_liquidate_all)"),
        (rule.initial_capital == 1000000, f"initial_capital: {rule.initial_capital} (预期: 1000000)")
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


def test_edge_case_zero_threshold():
    """测试7: 边界情况 - 任何亏损都触发（threshold=0）"""
    print("\n测试7: 边界情况 - 零容忍阈值...")

    algo = MockAlgorithm(initial_cash=1000000, current_value=999999)

    # 修改配置为零容忍
    config = {
        'enabled': True,
        'priority': 100,
        'threshold': 0.0,  # 任何亏损都触发
        'cooldown_days': 36500,
        'action': 'portfolio_liquidate_all'
    }

    rule = AccountBlowupRule(algo, config)
    triggered, description = rule.check()

    print(f"  触发状态: {triggered} (预期: True)")
    print(f"  描述: {description}")

    if triggered and "账户爆仓" in description:
        print("  [PASS]")
        return True
    else:
        print("  [FAIL]")
        return False


# ========== 主测试运行器 ==========

def run_all_tests():
    """运行所有测试"""
    print("=" * 60)
    print("AccountBlowupRule单元测试")
    print("=" * 60)

    tests = [
        test_normal_loss,
        test_exact_threshold,
        test_severe_loss,
        test_profit_situation,
        test_cooldown_mechanism,
        test_config_reading,
        test_edge_case_zero_threshold
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
