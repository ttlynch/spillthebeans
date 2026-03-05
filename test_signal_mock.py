"""Test script for signal evaluation pipeline using mock data."""

from strategy import (
    evaluate_signal,
    calculate_trade_stats,
    Signal,
    _cooldown_tracker,
    evaluate_test_signal,
)
from db import init_db, save_signal, get_signal_history
from datetime import datetime
import os


def build_mock_response(current_price: float, last_percentiles: dict) -> dict:
    """Build mock Synth API response with 60-element percentiles array."""
    neutral_step = {
        "0.05": current_price,
        "0.2": current_price,
        "0.35": current_price,
        "0.5": current_price,
        "0.65": current_price,
        "0.8": current_price,
        "0.95": current_price,
        "0.005": current_price,
        "0.995": current_price,
    }

    percentiles = [neutral_step.copy() for _ in range(59)] + [last_percentiles]

    return {
        "current_price": current_price,
        "forecast_future": {"percentiles": percentiles},
    }


def print_signal(signal: Signal) -> None:
    """Print Signal dataclass fields."""
    print(f"  asset={signal.asset}")
    print(f"  direction={signal.direction}")
    print(f"  entry_price={signal.entry_price:.2f}")
    print(f"  take_profit={signal.take_profit:.2f}")
    print(f"  stop_loss={signal.stop_loss:.2f}")
    print(f"  win_rate={signal.win_rate:.2f}")
    print(f"  vol_spread_pct={signal.vol_spread_pct:.2f}%")
    print(f"  timestamp={signal.timestamp.isoformat()}")


def test_long_signal(conn) -> bool:
    """Test LONG signal scenario."""
    print("\n=== TEST 1: LONG SIGNAL ===")

    last_percentiles = {
        "0.05": 66800,
        "0.2": 66950,
        "0.35": 67050,
        "0.5": 67150,
        "0.65": 67250,
        "0.8": 67400,
        "0.95": 67600,
        "0.005": 66700,
        "0.995": 67700,
    }

    mock_data = build_mock_response(67000, last_percentiles)
    signal = evaluate_signal("BTC", mock_data)

    if signal is None:
        print("FAIL - Expected LONG signal, got None")
        return False

    if signal.direction != "long":
        print(f"FAIL - Expected direction 'long', got '{signal.direction}'")
        return False

    print("PASS - LONG signal generated")
    print("Signal:")
    print_signal(signal)

    stats = calculate_trade_stats(signal, 100.0)
    print(f"\nTrade Stats (position_size=$100):")
    print(f"  expected_profit=${stats['expected_profit']:.2f}")
    print(f"  max_loss=${stats['max_loss']:.2f}")
    print(f"  risk_reward_ratio={stats['risk_reward_ratio']:.4f}")

    signal_id = save_signal(conn, signal)
    print(f"\nDB Verify: Signal saved with id={signal_id}")

    history = get_signal_history(conn, limit=1)
    if history and history[0]["id"] == signal_id:
        saved = history[0]
        if (
            saved["asset"] == signal.asset
            and saved["direction"] == signal.direction
            and abs(saved["entry"] - signal.entry_price) < 0.01
        ):
            print("DB Verify: Signal read back successfully - all fields match")
        else:
            print("FAIL - DB fields don't match")
            return False
    else:
        print("FAIL - Could not read signal back from DB")
        return False

    return True


def test_short_signal(conn) -> bool:
    """Test SHORT signal scenario."""
    print("\n=== TEST 2: SHORT SIGNAL ===")

    last_percentiles = {
        "0.05": 66300,
        "0.2": 66500,
        "0.35": 66700,
        "0.5": 66850,
        "0.65": 66950,
        "0.8": 67050,
        "0.95": 67200,
        "0.005": 66200,
        "0.995": 67300,
    }

    mock_data = build_mock_response(67000, last_percentiles)
    signal = evaluate_signal("ETH", mock_data)

    if signal is None:
        print("FAIL - Expected SHORT signal, got None")
        return False

    if signal.direction != "short":
        print(f"FAIL - Expected direction 'short', got '{signal.direction}'")
        return False

    print("PASS - SHORT signal generated")
    print("Signal:")
    print_signal(signal)

    stats = calculate_trade_stats(signal, 100.0)
    print(f"\nTrade Stats (position_size=$100):")
    print(f"  expected_profit=${stats['expected_profit']:.2f}")
    print(f"  max_loss=${stats['max_loss']:.2f}")
    print(f"  risk_reward_ratio={stats['risk_reward_ratio']:.4f}")

    signal_id = save_signal(conn, signal)
    print(f"\nDB Verify: Signal saved with id={signal_id}")

    history = get_signal_history(conn, limit=1)
    if history and history[0]["id"] == signal_id:
        saved = history[0]
        if (
            saved["asset"] == signal.asset
            and saved["direction"] == signal.direction
            and abs(saved["entry"] - signal.entry_price) < 0.01
        ):
            print("DB Verify: Signal read back successfully - all fields match")
        else:
            print("FAIL - DB fields don't match")
            return False
    else:
        print("FAIL - Could not read signal back from DB")
        return False

    return True


def test_neutral_signal() -> bool:
    """Test NEUTRAL scenario (no signal)."""
    print("\n=== TEST 3: NEUTRAL SIGNAL ===")

    last_percentiles = {
        "0.05": 66500,
        "0.2": 66750,
        "0.35": 66950,
        "0.5": 67000,
        "0.65": 67050,
        "0.8": 67250,
        "0.95": 67500,
        "0.005": 66400,
        "0.995": 67600,
    }

    mock_data = build_mock_response(67000, last_percentiles)
    signal = evaluate_signal("SOL", mock_data)

    if signal is not None:
        print(f"FAIL - Expected None, got signal: {signal.direction}")
        return False

    print("PASS - No signal (neutral)")
    print("Output: No signal")
    return True


def test_cooldown() -> bool:
    """Test cooldown mechanism."""
    print("\n=== TEST 4: COOLDOWN ===")

    _cooldown_tracker.clear()

    last_percentiles = {
        "0.05": 66800,
        "0.2": 66950,
        "0.35": 67050,
        "0.5": 67150,
        "0.65": 67250,
        "0.8": 67400,
        "0.95": 67600,
        "0.005": 66700,
        "0.995": 67700,
    }

    mock_data = build_mock_response(67000, last_percentiles)

    signal1 = evaluate_signal("BTC", mock_data)
    if signal1 is None:
        print("FAIL - First signal should be generated")
        return False

    print(f"First signal generated: {signal1.direction}")

    signal2 = evaluate_signal("BTC", mock_data)
    if signal2 is not None:
        print(
            f"FAIL - Second signal should be blocked by cooldown, got {signal2.direction}"
        )
        return False

    print("PASS - Second signal blocked by 30-minute cooldown")
    print("Confirmation: Cooldown mechanism working correctly")
    return True


def test_evaluate_test_signal_long() -> bool:
    """Test evaluate_test_signal LONG scenario."""
    print("\n=== TEST 5: TEST SIGNAL LONG ===")

    last_percentiles = {
        "0.05": 66800,
        "0.2": 66950,
        "0.35": 67050,
        "0.5": 67150,
        "0.65": 67250,
        "0.8": 67400,
        "0.95": 67600,
        "0.005": 66700,
        "0.995": 67700,
    }

    mock_data = build_mock_response(67000, last_percentiles)
    signal = evaluate_test_signal("BTC", mock_data)

    if signal.direction != "long":
        print(f"FAIL - Expected direction 'long', got '{signal.direction}'")
        return False

    if signal.entry_price != 67000:
        print(f"FAIL - Expected entry_price 67000, got {signal.entry_price}")
        return False

    if signal.take_profit != 67150:
        print(f"FAIL - Expected take_profit 67150 (p50), got {signal.take_profit}")
        return False

    if signal.stop_loss != 66800:
        print(f"FAIL - Expected stop_loss 66800 (p05), got {signal.stop_loss}")
        return False

    print("PASS - Test LONG signal generated correctly")
    print("Signal:")
    print_signal(signal)
    return True


def test_evaluate_test_signal_short() -> bool:
    """Test evaluate_test_signal SHORT scenario."""
    print("\n=== TEST 6: TEST SIGNAL SHORT ===")

    last_percentiles = {
        "0.05": 66300,
        "0.2": 66500,
        "0.35": 66700,
        "0.5": 66850,
        "0.65": 66950,
        "0.8": 67050,
        "0.95": 67200,
        "0.005": 66200,
        "0.995": 67300,
    }

    mock_data = build_mock_response(67000, last_percentiles)
    signal = evaluate_test_signal("ETH", mock_data)

    if signal.direction != "short":
        print(f"FAIL - Expected direction 'short', got '{signal.direction}'")
        return False

    if signal.entry_price != 67000:
        print(f"FAIL - Expected entry_price 67000, got {signal.entry_price}")
        return False

    if signal.take_profit != 66850:
        print(f"FAIL - Expected take_profit 66850 (p50), got {signal.take_profit}")
        return False

    if signal.stop_loss != 67200:
        print(f"FAIL - Expected stop_loss 67200 (p95), got {signal.stop_loss}")
        return False

    print("PASS - Test SHORT signal generated correctly")
    print("Signal:")
    print_signal(signal)
    return True


def test_evaluate_test_signal_no_cooldown() -> bool:
    """Test that evaluate_test_signal ignores cooldown."""
    print("\n=== TEST 7: TEST SIGNAL NO COOLDOWN ===")

    _cooldown_tracker.clear()

    last_percentiles = {
        "0.05": 66800,
        "0.2": 66950,
        "0.35": 67050,
        "0.5": 67150,
        "0.65": 67250,
        "0.8": 67400,
        "0.95": 67600,
        "0.005": 66700,
        "0.995": 67700,
    }

    mock_data = build_mock_response(67000, last_percentiles)

    signal1 = evaluate_test_signal("BTC", mock_data)
    if signal1 is None:
        print("FAIL - First test signal should be generated")
        return False

    signal2 = evaluate_test_signal("BTC", mock_data)
    if signal2 is None:
        print("FAIL - Second test signal should NOT be blocked by cooldown")
        return False

    print("PASS - Test signals ignore cooldown")
    return True


def main():
    """Run all tests."""
    db_path = "data/test_trading.db"

    if os.path.exists(db_path):
        os.remove(db_path)

    conn = init_db(db_path)

    results = []

    results.append(("LONG signal", test_long_signal(conn)))
    results.append(("SHORT signal", test_short_signal(conn)))
    results.append(("NEUTRAL signal", test_neutral_signal()))
    results.append(("Cooldown", test_cooldown()))
    results.append(("Test signal LONG", test_evaluate_test_signal_long()))
    results.append(("Test signal SHORT", test_evaluate_test_signal_short()))
    results.append(("Test signal no cooldown", test_evaluate_test_signal_no_cooldown()))

    conn.close()

    if os.path.exists(db_path):
        os.remove(db_path)

    print("\n" + "=" * 50)
    print("SUMMARY")
    print("=" * 50)

    all_passed = True
    for test_name, passed in results:
        status = "PASS" if passed else "FAIL"
        print(f"  {test_name}: {status}")
        if not passed:
            all_passed = False

    print("=" * 50)
    if all_passed:
        print("ALL TESTS PASSED")
    else:
        print("SOME TESTS FAILED")

    return 0 if all_passed else 1


if __name__ == "__main__":
    exit(main())
