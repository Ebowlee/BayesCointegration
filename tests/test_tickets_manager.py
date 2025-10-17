"""
TicketsManager单元测试

测试目标:
验证订单生命周期管理器在各种极端场景下的异常检测能力

测试覆盖:
1. 正常场景: 双腿都Filled → COMPLETED
2. 异常场景: 单腿Canceled → ANOMALY
3. 异常场景: 双腿Canceled → ANOMALY
4. 异常场景: 单腿Invalid → ANOMALY
5. 待完成场景: 一腿Filled,一腿Submitted → PENDING
6. 异常集合: get_anomaly_pairs()检测多个异常配对
7. 回调机制: 异常时不应触发on_position_filled()

设计原则:
- 完全隔离,不影响生产代码
- 使用Mock对象模拟QuantConnect组件
- 每个测试独立,互不干扰
"""

import sys
import os

# 添加项目根目录到Python路径(使得可以import src模块)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# ⭐ 关键步骤: 在导入生产代码前,先注入Mock的AlgorithmImports
# 这样TicketsManager.py中的 "from AlgorithmImports import *" 会加载Mock版本
import tests.mocks.algorithm_imports_stub as AlgorithmImports
sys.modules['AlgorithmImports'] = AlgorithmImports

# 导入Mock对象
from tests.mocks.mock_qc_objects import (
    MockAlgorithm, MockOrderTicket, MockSymbol, MockOrderStatus,
    MockPairsManager, MockPairs
)

# 导入生产代码(只读,不修改)
from src.TicketsManager import TicketsManager


# ========== 测试用例1: 正常完成 ==========

def test_normal_completion():
    """
    测试场景: 双腿订单都成功成交

    预期结果:
    - get_pair_status() 返回 "COMPLETED"
    - get_anomaly_pairs() 返回空集合
    - on_position_filled() 被正确调用
    """
    print("\n" + "="*60)
    print("测试用例1: 正常完成 (双腿Filled)")
    print("="*60)

    # 准备Mock对象
    mock_algo = MockAlgorithm()
    mock_pairs_manager = MockPairsManager()

    symbol1 = MockSymbol("AAPL")
    symbol2 = MockSymbol("MSFT")
    mock_pairs = MockPairs(symbol1, symbol2)
    mock_pairs_manager.add_pair("(AAPL, MSFT)", mock_pairs)

    # 创建TicketsManager实例(真实代码)
    tm = TicketsManager(mock_algo, mock_pairs_manager)

    # 模拟双腿订单都成功
    ticket1 = MockOrderTicket(101, symbol1, MockOrderStatus.Filled)
    ticket1.QuantityFilled = 100
    ticket2 = MockOrderTicket(102, symbol2, MockOrderStatus.Filled)
    ticket2.QuantityFilled = -100

    # 注册订单
    tm.register_tickets("(AAPL, MSFT)", [ticket1, ticket2], "OPEN")

    # 验证状态
    status = tm.get_pair_status("(AAPL, MSFT)")
    print(f"[OK] 配对状态: {status}")
    assert status == "COMPLETED", f"预期COMPLETED,实际{status}"

    # 验证无异常
    anomalies = tm.get_anomaly_pairs()
    print(f"[OK] 异常配对数: {len(anomalies)}")
    assert len(anomalies) == 0, f"不应有异常配对,实际{anomalies}"

    # 模拟OnOrderEvent触发回调(手动调用,因为我们没有真实的LEAN引擎)
    class MockOrderEvent:
        def __init__(self, order_id, status):
            self.OrderId = order_id
            self.Status = status

    tm.on_order_event(MockOrderEvent(101, MockOrderStatus.Filled))
    tm.on_order_event(MockOrderEvent(102, MockOrderStatus.Filled))

    # 验证回调被触发
    assert mock_pairs.callback_called, "on_position_filled应该被调用"
    assert mock_pairs.callback_action == "OPEN", "回调动作应该是OPEN"
    assert mock_pairs.tracked_qty1 == 100, "数量1应该是100"
    assert mock_pairs.tracked_qty2 == -100, "数量2应该是-100"

    print("[OK] 回调机制验证通过")
    print("[PASS] 测试通过: 正常完成场景\n")


# ========== 测试用例2: 单腿Canceled ==========

def test_one_leg_canceled():
    """
    测试场景: 一条腿Filled,另一条腿Canceled

    预期结果:
    - get_pair_status() 返回 "ANOMALY"
    - get_anomaly_pairs() 包含该配对
    - on_position_filled() 不应被调用
    """
    print("\n" + "="*60)
    print("测试用例2: 单腿Canceled (极端异常)")
    print("="*60)

    mock_algo = MockAlgorithm()
    mock_pairs_manager = MockPairsManager()

    symbol1 = MockSymbol("TSLA")
    symbol2 = MockSymbol("NVDA")
    mock_pairs = MockPairs(symbol1, symbol2)
    mock_pairs_manager.add_pair("(TSLA, NVDA)", mock_pairs)

    tm = TicketsManager(mock_algo, mock_pairs_manager)

    # 模拟异常: 一条成功,一条被取消
    ticket1 = MockOrderTicket(201, symbol1, MockOrderStatus.Filled)
    ticket1.QuantityFilled = 50
    ticket2 = MockOrderTicket(202, symbol2, MockOrderStatus.Canceled)  # ← 异常!

    tm.register_tickets("(TSLA, NVDA)", [ticket1, ticket2], "OPEN")

    # 验证异常检测
    status = tm.get_pair_status("(TSLA, NVDA)")
    print(f"[OK] 配对状态: {status}")
    assert status == "ANOMALY", f"预期ANOMALY,实际{status}"

    # 验证异常集合
    anomalies = tm.get_anomaly_pairs()
    print(f"[OK] 异常配对: {anomalies}")
    assert "(TSLA, NVDA)" in anomalies, "应该检测到异常配对"

    # 模拟OnOrderEvent
    class MockOrderEvent:
        def __init__(self, order_id, status):
            self.OrderId = order_id
            self.Status = status

    tm.on_order_event(MockOrderEvent(201, MockOrderStatus.Filled))
    tm.on_order_event(MockOrderEvent(202, MockOrderStatus.Canceled))

    # 验证回调不应被触发(因为是ANOMALY状态)
    assert not mock_pairs.callback_called, "ANOMALY状态不应触发回调"

    print("[OK] 异常检测验证通过")
    print("[OK] 回调隔离验证通过")
    print("[PASS] 测试通过: 单腿Canceled被正确检测\n")


# ========== 测试用例3: 双腿Canceled ==========

def test_both_legs_canceled():
    """
    测试场景: 双腿订单都被取消

    预期结果:
    - get_pair_status() 返回 "ANOMALY"
    - get_anomaly_pairs() 包含该配对
    """
    print("\n" + "="*60)
    print("测试用例3: 双腿Canceled (极端异常)")
    print("="*60)

    mock_algo = MockAlgorithm()
    mock_pairs_manager = MockPairsManager()

    tm = TicketsManager(mock_algo, mock_pairs_manager)

    symbol1 = MockSymbol("GOOGL")
    symbol2 = MockSymbol("AMZN")

    # 双腿都Canceled
    ticket1 = MockOrderTicket(301, symbol1, MockOrderStatus.Canceled)
    ticket2 = MockOrderTicket(302, symbol2, MockOrderStatus.Canceled)

    tm.register_tickets("(GOOGL, AMZN)", [ticket1, ticket2], "CLOSE")

    # 验证
    status = tm.get_pair_status("(GOOGL, AMZN)")
    print(f"[OK] 配对状态: {status}")
    assert status == "ANOMALY", f"预期ANOMALY,实际{status}"

    anomalies = tm.get_anomaly_pairs()
    assert "(GOOGL, AMZN)" in anomalies

    print("[OK] 双腿取消检测通过")
    print("[PASS] 测试通过: 双腿Canceled被正确检测\n")


# ========== 测试用例4: 单腿Invalid ==========

def test_one_leg_invalid():
    """
    测试场景: 一条腿Filled,另一条腿Invalid

    触发条件:
    - 保证金不足
    - 价格无效
    - 订单参数错误

    预期结果:
    - get_pair_status() 返回 "ANOMALY"
    """
    print("\n" + "="*60)
    print("测试用例4: 单腿Invalid (订单无效)")
    print("="*60)

    mock_algo = MockAlgorithm()
    mock_pairs_manager = MockPairsManager()

    tm = TicketsManager(mock_algo, mock_pairs_manager)

    symbol1 = MockSymbol("META")
    symbol2 = MockSymbol("NFLX")

    # 一条成功,一条无效
    ticket1 = MockOrderTicket(401, symbol1, MockOrderStatus.Filled)
    ticket1.QuantityFilled = 75
    ticket2 = MockOrderTicket(402, symbol2, MockOrderStatus.Invalid)  # ← 无效订单

    tm.register_tickets("(META, NFLX)", [ticket1, ticket2], "OPEN")

    # 验证
    status = tm.get_pair_status("(META, NFLX)")
    print(f"[OK] 配对状态: {status}")
    assert status == "ANOMALY", f"预期ANOMALY,实际{status}"

    print("[OK] Invalid订单检测通过")
    print("[PASS] 测试通过: 单腿Invalid被正确检测\n")


# ========== 测试用例5: Pending状态 ==========

def test_pending_state():
    """
    测试场景: 一条腿Filled,另一条腿还在Submitted

    预期结果:
    - get_pair_status() 返回 "PENDING"
    - is_pair_locked() 返回 True (阻止重复下单)
    """
    print("\n" + "="*60)
    print("测试用例5: Pending状态 (订单执行中)")
    print("="*60)

    mock_algo = MockAlgorithm()
    mock_pairs_manager = MockPairsManager()

    tm = TicketsManager(mock_algo, mock_pairs_manager)

    symbol1 = MockSymbol("IBM")
    symbol2 = MockSymbol("ORCL")

    # 一条成交,一条还在提交中
    ticket1 = MockOrderTicket(501, symbol1, MockOrderStatus.Filled)
    ticket1.QuantityFilled = 200
    ticket2 = MockOrderTicket(502, symbol2, MockOrderStatus.Submitted)  # ← 还未成交

    tm.register_tickets("(IBM, ORCL)", [ticket1, ticket2], "OPEN")

    # 验证状态
    status = tm.get_pair_status("(IBM, ORCL)")
    print(f"[OK] 配对状态: {status}")
    assert status == "PENDING", f"预期PENDING,实际{status}"

    # 验证锁定机制
    is_locked = tm.is_pair_locked("(IBM, ORCL)")
    print(f"[OK] 配对锁定: {is_locked}")
    assert is_locked, "PENDING状态应该锁定配对"

    print("[OK] Pending状态检测通过")
    print("[OK] 锁定机制验证通过")
    print("[PASS] 测试通过: Pending状态正确处理\n")


# ========== 测试用例6: 多配对异常检测 ==========

def test_multiple_anomaly_pairs():
    """
    测试场景: 同时管理多个配对,部分异常,部分正常

    验证:
    - get_anomaly_pairs() 只返回异常的配对
    - 不会误报正常配对
    """
    print("\n" + "="*60)
    print("测试用例6: 多配对异常检测")
    print("="*60)

    mock_algo = MockAlgorithm()
    mock_pairs_manager = MockPairsManager()

    tm = TicketsManager(mock_algo, mock_pairs_manager)

    # 配对1: 正常
    tm.register_tickets("(AAA, BBB)", [
        MockOrderTicket(601, MockSymbol("AAA"), MockOrderStatus.Filled),
        MockOrderTicket(602, MockSymbol("BBB"), MockOrderStatus.Filled)
    ], "OPEN")

    # 配对2: 异常(单腿Canceled)
    tm.register_tickets("(CCC, DDD)", [
        MockOrderTicket(603, MockSymbol("CCC"), MockOrderStatus.Filled),
        MockOrderTicket(604, MockSymbol("DDD"), MockOrderStatus.Canceled)
    ], "OPEN")

    # 配对3: 正常
    tm.register_tickets("(EEE, FFF)", [
        MockOrderTicket(605, MockSymbol("EEE"), MockOrderStatus.Filled),
        MockOrderTicket(606, MockSymbol("FFF"), MockOrderStatus.Filled)
    ], "CLOSE")

    # 配对4: 异常(双腿Invalid)
    tm.register_tickets("(GGG, HHH)", [
        MockOrderTicket(607, MockSymbol("GGG"), MockOrderStatus.Invalid),
        MockOrderTicket(608, MockSymbol("HHH"), MockOrderStatus.Invalid)
    ], "OPEN")

    # 验证异常检测
    anomalies = tm.get_anomaly_pairs()
    print(f"[OK] 检测到异常配对: {anomalies}")

    assert len(anomalies) == 2, f"应该检测到2个异常配对,实际{len(anomalies)}"
    assert "(CCC, DDD)" in anomalies, "应该包含(CCC, DDD)"
    assert "(GGG, HHH)" in anomalies, "应该包含(GGG, HHH)"
    assert "(AAA, BBB)" not in anomalies, "不应包含正常配对(AAA, BBB)"
    assert "(EEE, FFF)" not in anomalies, "不应包含正常配对(EEE, FFF)"

    print("[OK] 多配对异常检测精确")
    print("[OK] 无误报正常配对")
    print("[PASS] 测试通过: 多配对场景处理正确\n")


# ========== 测试用例7: 回调不应在异常时触发 ==========

def test_no_callback_on_anomaly():
    """
    测试场景: 订单异常时,不应触发Pairs.on_position_filled()

    重要性:
    - 避免错误记录持仓时间
    - 避免错误更新tracked_qty
    - 保持数据一致性
    """
    print("\n" + "="*60)
    print("测试用例7: 异常时不触发回调 (安全机制)")
    print("="*60)

    mock_algo = MockAlgorithm()
    mock_pairs_manager = MockPairsManager()

    symbol1 = MockSymbol("XYZ")
    symbol2 = MockSymbol("ABC")
    mock_pairs = MockPairs(symbol1, symbol2)
    mock_pairs_manager.add_pair("(XYZ, ABC)", mock_pairs)

    tm = TicketsManager(mock_algo, mock_pairs_manager)

    # 注册异常订单
    ticket1 = MockOrderTicket(701, symbol1, MockOrderStatus.Filled)
    ticket2 = MockOrderTicket(702, symbol2, MockOrderStatus.Canceled)

    tm.register_tickets("(XYZ, ABC)", [ticket1, ticket2], "OPEN")

    # 模拟OnOrderEvent
    class MockOrderEvent:
        def __init__(self, order_id, status):
            self.OrderId = order_id
            self.Status = status

    tm.on_order_event(MockOrderEvent(701, MockOrderStatus.Filled))
    tm.on_order_event(MockOrderEvent(702, MockOrderStatus.Canceled))

    # 验证回调未触发
    print(f"[OK] 回调是否触发: {mock_pairs.callback_called}")
    assert not mock_pairs.callback_called, "ANOMALY状态不应触发on_position_filled()"

    # 验证数据未污染
    assert mock_pairs.position_opened_time is None, "持仓时间不应被设置"
    assert mock_pairs.tracked_qty1 == 0, "数量1不应被更新"
    assert mock_pairs.tracked_qty2 == 0, "数量2不应被更新"

    print("[OK] 回调隔离机制正常")
    print("[OK] 数据一致性保持")
    print("[PASS] 测试通过: 异常时安全隔离\n")


# ========== 主测试入口 ==========

def run_all_tests():
    """运行所有测试用例"""
    print("\n" + "="*60)
    print("=" + " "*58 + "=")
    print("=" + "  TicketsManager单元测试套件".center(56) + "=")
    print("=" + " "*58 + "=")
    print("="*60 + "\n")

    test_functions = [
        test_normal_completion,
        test_one_leg_canceled,
        test_both_legs_canceled,
        test_one_leg_invalid,
        test_pending_state,
        test_multiple_anomaly_pairs,
        test_no_callback_on_anomaly
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
