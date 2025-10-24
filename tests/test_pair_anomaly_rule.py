"""
PairAnomalyRule 单元测试

测试目标:
验证配对异常风控规则在各种持仓状态下的检测能力

测试覆盖:
1. 单腿持仓LEG1: 只有第一腿有仓位
2. 单腿持仓LEG2: 只有第二腿有仓位
3. 同向持仓: 双腿方向相同(都多或都空)
4. 正常持仓: 一多一空,不应触发
5. 优先级验证: PairAnomalyRule(100) > PairHoldingTimeoutRule(60)

设计原则:
- 完全隔离,不影响生产代码
- 使用Mock对象模拟持仓状态
- 每个测试独立,互不干扰
"""

import sys
import os

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# 注入Mock的AlgorithmImports
import tests.mocks.algorithm_imports_stub as AlgorithmImports
sys.modules['AlgorithmImports'] = AlgorithmImports

# 导入Mock对象
from tests.mocks.mock_qc_objects import MockAlgorithm, MockSymbol

# 导入生产代码
from src.RiskManagement.PairAnomalyRule import PairAnomalyRule
from src.Pairs import PositionMode


# ========== Mock Pairs对象(简化版) ==========

class MockPairsForAnomalyTest:
    """
    为PairAnomalyRule测试专门设计的Mock Pairs对象

    关键特性:
    - 可手动设置tracked_qty1和tracked_qty2
    - 实现has_anomaly()方法(复用Pairs.py的逻辑)
    - 实现get_position_info()方法
    """
    def __init__(self, algorithm, symbol1: MockSymbol, symbol2: MockSymbol):
        self.algorithm = algorithm
        self.symbol1 = symbol1
        self.symbol2 = symbol2
        self.pair_id = (symbol1.Value, symbol2.Value)

        # 持仓追踪(测试时手动设置)
        self.tracked_qty1 = 0
        self.tracked_qty2 = 0

    def get_position_info(self):
        """
        复用Pairs.py的逻辑判断持仓模式
        """
        qty1 = self.tracked_qty1
        qty2 = self.tracked_qty2

        # 判断持仓模式(与Pairs.py保持一致)
        if qty1 == 0 and qty2 == 0:
            position_mode = PositionMode.NONE
        elif qty1 > 0 and qty2 < 0:
            position_mode = PositionMode.LONG_SPREAD
        elif qty1 < 0 and qty2 > 0:
            position_mode = PositionMode.SHORT_SPREAD
        elif qty1 != 0 and qty2 == 0:
            position_mode = PositionMode.PARTIAL_LEG1
        elif qty1 == 0 and qty2 != 0:
            position_mode = PositionMode.PARTIAL_LEG2
        else:  # 同向持仓
            position_mode = PositionMode.ANOMALY_SAME

        return {
            'position_mode': position_mode,
            'qty1': qty1,
            'qty2': qty2,
            'value1': 0,  # 测试不关心市值
            'value2': 0
        }

    def has_anomaly(self):
        """检查是否有异常持仓(复用Pairs.py逻辑)"""
        mode = self.get_position_info()['position_mode']
        return mode in [PositionMode.PARTIAL_LEG1, PositionMode.PARTIAL_LEG2, PositionMode.ANOMALY_SAME]


# ========== 测试用例1: 单腿持仓LEG1 ==========

def test_partial_leg1_detection():
    """
    测试场景: 只有第一腿有持仓,第二腿为0

    触发条件: 第二腿订单被Canceled/Invalid

    预期结果:
    - rule.check() 返回 (True, "单边持仓LEG1: ...")
    """
    print("\n" + "="*60)
    print("测试用例1: 单腿持仓LEG1检测")
    print("="*60)

    # 准备Mock对象
    mock_algo = MockAlgorithm()
    symbol1 = MockSymbol("AAPL")
    symbol2 = MockSymbol("MSFT")
    mock_pair = MockPairsForAnomalyTest(mock_algo, symbol1, symbol2)

    # 模拟异常: 只有第一腿有仓位
    mock_pair.tracked_qty1 = 100
    mock_pair.tracked_qty2 = 0

    # 创建规则实例
    config = {'enabled': True, 'priority': 100, 'action': 'pair_close'}
    rule = PairAnomalyRule(mock_algo, config)

    # 执行检查
    triggered, description = rule.check(pair=mock_pair)

    print(f"[OK] 触发状态: {triggered}")
    print(f"[OK] 描述信息: {description}")

    # 验证
    assert triggered == True, f"应该触发,实际{triggered}"
    assert "单边持仓LEG1" in description, f"描述应包含'单边持仓LEG1',实际{description}"
    assert "AAPL=+100" in description, f"描述应包含'AAPL=+100',实际{description}"
    assert "MSFT=0" in description, f"描述应包含'MSFT=0',实际{description}"

    print("[PASS] 测试通过: 单腿持仓LEG1被正确检测\n")


# ========== 测试用例2: 单腿持仓LEG2 ==========

def test_partial_leg2_detection():
    """
    测试场景: 只有第二腿有持仓,第一腿为0

    触发条件: 第一腿订单被Canceled/Invalid

    预期结果:
    - rule.check() 返回 (True, "单边持仓LEG2: ...")
    """
    print("\n" + "="*60)
    print("测试用例2: 单腿持仓LEG2检测")
    print("="*60)

    mock_algo = MockAlgorithm()
    symbol1 = MockSymbol("GOOGL")
    symbol2 = MockSymbol("AMZN")
    mock_pair = MockPairsForAnomalyTest(mock_algo, symbol1, symbol2)

    # 模拟异常: 只有第二腿有仓位
    mock_pair.tracked_qty1 = 0
    mock_pair.tracked_qty2 = -50

    config = {'enabled': True, 'priority': 100, 'action': 'pair_close'}
    rule = PairAnomalyRule(mock_algo, config)

    triggered, description = rule.check(pair=mock_pair)

    print(f"[OK] 触发状态: {triggered}")
    print(f"[OK] 描述信息: {description}")

    assert triggered == True, f"应该触发"
    assert "单边持仓LEG2" in description
    assert "GOOGL=0" in description
    assert "AMZN=-50" in description

    print("[PASS] 测试通过: 单腿持仓LEG2被正确检测\n")


# ========== 测试用例3: 同向持仓 ==========

def test_anomaly_same_direction():
    """
    测试场景: 双腿持仓方向相同(都是正数或都是负数)

    触发条件: 订单执行逻辑错误,导致两腿都买入或都卖出

    预期结果:
    - rule.check() 返回 (True, "同向持仓: ...")
    """
    print("\n" + "="*60)
    print("测试用例3: 同向持仓检测")
    print("="*60)

    mock_algo = MockAlgorithm()
    symbol1 = MockSymbol("TSLA")
    symbol2 = MockSymbol("NVDA")
    mock_pair = MockPairsForAnomalyTest(mock_algo, symbol1, symbol2)

    # 模拟异常: 双腿都是正数(都买入)
    mock_pair.tracked_qty1 = 100
    mock_pair.tracked_qty2 = 80

    config = {'enabled': True, 'priority': 100, 'action': 'pair_close'}
    rule = PairAnomalyRule(mock_algo, config)

    triggered, description = rule.check(pair=mock_pair)

    print(f"[OK] 触发状态: {triggered}")
    print(f"[OK] 描述信息: {description}")

    assert triggered == True
    assert "同向持仓" in description
    assert "TSLA=+100" in description
    assert "NVDA=+80" in description

    print("[PASS] 测试通过: 同向持仓被正确检测\n")


# ========== 测试用例4: 正常持仓不触发 ==========

def test_normal_position_no_trigger():
    """
    测试场景: 正常的对冲持仓(一多一空)

    预期结果:
    - rule.check() 返回 (False, "")
    - 不应误报正常持仓
    """
    print("\n" + "="*60)
    print("测试用例4: 正常持仓不触发")
    print("="*60)

    mock_algo = MockAlgorithm()
    symbol1 = MockSymbol("META")
    symbol2 = MockSymbol("NFLX")
    mock_pair = MockPairsForAnomalyTest(mock_algo, symbol1, symbol2)

    # 设置正常持仓: LONG_SPREAD (qty1>0, qty2<0)
    mock_pair.tracked_qty1 = 100
    mock_pair.tracked_qty2 = -80

    config = {'enabled': True, 'priority': 100, 'action': 'pair_close'}
    rule = PairAnomalyRule(mock_algo, config)

    triggered, description = rule.check(pair=mock_pair)

    print(f"[OK] 触发状态: {triggered}")
    print(f"[OK] 描述信息: '{description}'")

    assert triggered == False, "正常持仓不应触发"
    assert description == "", "描述应为空"

    print("[PASS] 测试通过: 正常持仓不误报\n")


# ========== 测试用例5: 无持仓不触发 ==========

def test_no_position_no_trigger():
    """
    测试场景: 配对没有任何持仓

    预期结果:
    - rule.check() 返回 (False, "")
    """
    print("\n" + "="*60)
    print("测试用例5: 无持仓不触发")
    print("="*60)

    mock_algo = MockAlgorithm()
    symbol1 = MockSymbol("IBM")
    symbol2 = MockSymbol("ORCL")
    mock_pair = MockPairsForAnomalyTest(mock_algo, symbol1, symbol2)

    # 无持仓
    mock_pair.tracked_qty1 = 0
    mock_pair.tracked_qty2 = 0

    config = {'enabled': True, 'priority': 100, 'action': 'pair_close'}
    rule = PairAnomalyRule(mock_algo, config)

    triggered, description = rule.check(pair=mock_pair)

    print(f"[OK] 触发状态: {triggered}")

    assert triggered == False, "无持仓不应触发"
    assert description == ""

    print("[PASS] 测试通过: 无持仓不误报\n")


# ========== 测试用例6: 规则禁用时不触发 ==========

def test_disabled_rule_no_trigger():
    """
    测试场景: 规则被禁用(enabled=False)

    预期结果:
    - 即使有异常,也不触发
    """
    print("\n" + "="*60)
    print("测试用例6: 禁用规则不触发")
    print("="*60)

    mock_algo = MockAlgorithm()
    symbol1 = MockSymbol("XYZ")
    symbol2 = MockSymbol("ABC")
    mock_pair = MockPairsForAnomalyTest(mock_algo, symbol1, symbol2)

    # 设置异常持仓
    mock_pair.tracked_qty1 = 100
    mock_pair.tracked_qty2 = 0

    # 禁用规则
    config = {'enabled': False, 'priority': 100, 'action': 'pair_close'}
    rule = PairAnomalyRule(mock_algo, config)

    triggered, description = rule.check(pair=mock_pair)

    print(f"[OK] 触发状态: {triggered}")

    assert triggered == False, "禁用时不应触发"
    assert description == ""

    print("[PASS] 测试通过: 禁用规则正确处理\n")


# ========== 测试用例7: 优先级验证 ==========

def test_priority_over_holding_timeout():
    """
    测试场景: 同时触发PairAnomalyRule和PairHoldingTimeoutRule

    验证: RiskManager应返回PairAnomalyRule(priority=100)的动作

    注: 这个测试需要RiskManager,暂时简化为验证优先级属性
    """
    print("\n" + "="*60)
    print("测试用例7: 优先级验证")
    print("="*60)

    mock_algo = MockAlgorithm()

    # 创建PairAnomalyRule
    config_anomaly = {'enabled': True, 'priority': 100, 'action': 'pair_close'}
    rule_anomaly = PairAnomalyRule(mock_algo, config_anomaly)

    # 验证优先级
    print(f"[OK] PairAnomalyRule 优先级: {rule_anomaly.priority}")
    assert rule_anomaly.priority == 100, "优先级应为100"

    # 验证动作
    action = rule_anomaly.get_action()
    print(f"[OK] 响应动作: {action}")
    assert action == 'pair_close', "动作应为pair_close"

    print("[PASS] 测试通过: 优先级和动作正确\n")


# ========== 主测试入口 ==========

def run_all_tests():
    """运行所有测试用例"""
    print("\n" + "="*60)
    print("=" + " "*58 + "=")
    print("=" + "  PairAnomalyRule单元测试套件".center(56) + "=")
    print("=" + " "*58 + "=")
    print("="*60 + "\n")

    test_functions = [
        test_partial_leg1_detection,
        test_partial_leg2_detection,
        test_anomaly_same_direction,
        test_normal_position_no_trigger,
        test_no_position_no_trigger,
        test_disabled_rule_no_trigger,
        test_priority_over_holding_timeout
    ]

    passed = 0
    failed = 0

    for test_func in test_functions:
        try:
            test_func()
            passed += 1
        except AssertionError as e:
            print(f"[FAIL] 测试失败: {test_func.__name__}")
            print(f"   错误: {str(e)}\n")
            failed += 1
        except Exception as e:
            print(f"[FAIL] 测试异常: {test_func.__name__}")
            print(f"   异常: {str(e)}\n")
            failed += 1

    # 总结
    print("\n" + "="*60)
    print(f"=  测试结果汇总".ljust(58) + "=")
    print("=" + "-"*58 + "=")
    print(f"=  总计: {len(test_functions)}个测试".ljust(58) + "=")
    print(f"=  通过: {passed}个 [PASS]".ljust(58) + "=")
    print(f"=  失败: {failed}个 [FAIL]".ljust(58) + "=")
    print("=" + "-"*58 + "=")

    if failed == 0:
        print("=  状态: 全部通过!".ljust(58) + "=")
    else:
        print(f"=  状态: 有{failed}个测试失败".ljust(58) + "=")

    print("="*60 + "\n")

    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    exit(0 if success else 1)
