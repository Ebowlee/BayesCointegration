"""简化版测试 - 验证核心功能(无特殊符号)"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# 注入Mock AlgorithmImports
import tests.mocks.algorithm_imports_stub as AlgorithmImports
sys.modules['AlgorithmImports'] = AlgorithmImports

from tests.mocks.mock_qc_objects import (
    MockAlgorithm, MockOrderTicket, MockSymbol, MockOrderStatus,
    MockPairsManager, MockPairs, MockOrderEvent
)

from src.TicketsManager import TicketsManager

def test_one_leg_canceled():
    """核心测试: 单腿Canceled → ANOMALY"""
    print("\n[TEST] 单腿Canceled异常检测")

    mock_algo = MockAlgorithm()
    mock_pairs_manager = MockPairsManager()
    tm = TicketsManager(mock_algo, mock_pairs_manager)

    symbol1 = MockSymbol("TSLA")
    symbol2 = MockSymbol("NVDA")

    # 模拟异常: 一条成功,一条被取消
    ticket1 = MockOrderTicket(201, symbol1, MockOrderStatus.Filled)
    ticket1.QuantityFilled = 50
    ticket2 = MockOrderTicket(202, symbol2, MockOrderStatus.Canceled)

    tm.register_tickets("(TSLA, NVDA)", [ticket1, ticket2], "OPEN")

    # 验证异常检测
    status = tm.get_pair_status("(TSLA, NVDA)")
    print(f"[OK] 配对状态: {status}")
    assert status == "ANOMALY", f"预期ANOMALY,实际{status}"

    # 验证异常集合
    anomalies = tm.get_anomaly_pairs()
    print(f"[OK] 异常配对数: {len(anomalies)}")
    assert "(TSLA, NVDA)" in anomalies

    print("[PASS] 单腿Canceled检测正确\n")


def test_normal_completion():
    """测试: 双腿Filled → COMPLETED"""
    print("\n[TEST] 正常完成场景")

    mock_algo = MockAlgorithm()
    mock_pairs_manager = MockPairsManager()

    symbol1 = MockSymbol("AAPL")
    symbol2 = MockSymbol("MSFT")
    mock_pairs = MockPairs(symbol1, symbol2)
    mock_pairs_manager.add_pair("(AAPL, MSFT)", mock_pairs)

    tm = TicketsManager(mock_algo, mock_pairs_manager)

    ticket1 = MockOrderTicket(101, symbol1, MockOrderStatus.Filled)
    ticket1.QuantityFilled = 100
    ticket2 = MockOrderTicket(102, symbol2, MockOrderStatus.Filled)
    ticket2.QuantityFilled = -100

    tm.register_tickets("(AAPL, MSFT)", [ticket1, ticket2], "OPEN")

    status = tm.get_pair_status("(AAPL, MSFT)")
    print(f"[OK] 配对状态: {status}")
    assert status == "COMPLETED"

    # 模拟OnOrderEvent触发回调
    tm.on_order_event(MockOrderEvent(101, MockOrderStatus.Filled))
    tm.on_order_event(MockOrderEvent(102, MockOrderStatus.Filled))

    # 验证回调
    assert mock_pairs.callback_called
    assert mock_pairs.tracked_qty1 == 100
    assert mock_pairs.tracked_qty2 == -100

    print("[PASS] 正常完成+回调验证通过\n")


def test_pending_state():
    """测试: Submitted → PENDING + 锁定"""
    print("\n[TEST] Pending状态")

    mock_algo = MockAlgorithm()
    mock_pairs_manager = MockPairsManager()
    tm = TicketsManager(mock_algo, mock_pairs_manager)

    symbol1 = MockSymbol("IBM")
    symbol2 = MockSymbol("ORCL")

    ticket1 = MockOrderTicket(501, symbol1, MockOrderStatus.Filled)
    ticket1.QuantityFilled = 200
    ticket2 = MockOrderTicket(502, symbol2, MockOrderStatus.Submitted)

    tm.register_tickets("(IBM, ORCL)", [ticket1, ticket2], "OPEN")

    status = tm.get_pair_status("(IBM, ORCL)")
    print(f"[OK] 配对状态: {status}")
    assert status == "PENDING"

    is_locked = tm.is_pair_locked("(IBM, ORCL)")
    print(f"[OK] 配对锁定: {is_locked}")
    assert is_locked

    print("[PASS] Pending状态+锁定机制正确\n")


if __name__ == "__main__":
    print("=" * 50)
    print("TicketsManager 核心功能测试")
    print("=" * 50)

    tests = [
        test_normal_completion,
        test_one_leg_canceled,
        test_pending_state
    ]

    passed = 0
    for test_func in tests:
        try:
            test_func()
            passed += 1
        except AssertionError as e:
            print(f"[FAIL] {test_func.__name__}: {e}\n")
        except Exception as e:
            print(f"[ERROR] {test_func.__name__}: {e}\n")

    print("=" * 50)
    print(f"结果: {passed}/{len(tests)} 通过")
    print("=" * 50)

    exit(0 if passed == len(tests) else 1)
