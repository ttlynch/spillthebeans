"""Test script to verify the execution pipeline components."""

import asyncio
import logging
from datetime import datetime
from config import validate_config, HL_WALLET_ADDRESS, HL_PRIVATE_KEY, HL_TESTNET
from db import init_db, save_signal
from strategy import Signal, evaluate_signal
from execution import (
    calculate_position_size,
    calculate_pnl,
    check_exit_conditions,
    check_signal_validity,
)
from hl_client import HLClient
from synth_client import SynthClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def test_calculate_position_size():
    """Test position size calculation."""
    logger.info("Testing calculate_position_size...")

    size_usd = 100.0
    price = 50000.0
    sz_decimals = 4

    size = calculate_position_size(size_usd, price, sz_decimals)
    expected = 100.0 / 50000.0

    logger.info(
        f"USD: ${size_usd}, Price: ${price}, Size: {size}, Expected: {expected}"
    )

    if abs(size - expected) < 0.0001:
        logger.info("✅ calculate_position_size works correctly")
        return True
    else:
        logger.error("❌ calculate_position_size incorrect")
        return False


def test_calculate_pnl():
    """Test P&L calculation."""
    logger.info("Testing calculate_pnl...")

    size_usd = 100.0
    entry = 50000.0
    exit_price = 51000.0

    pnl_usd, pnl_pct = calculate_pnl("long", size_usd, entry, exit_price)
    expected_usd = 100.0 * (51000.0 - 50000.0) / 50000.0
    expected_pct = (51000.0 - 50000.0) / 50000.0

    logger.info(
        f"Long P&L: ${pnl_usd:.2f} ({pnl_pct:.2%}), Expected: ${expected_usd:.2f} ({expected_pct:.2%})"
    )

    if abs(pnl_usd - expected_usd) < 0.01 and abs(pnl_pct - expected_pct) < 0.0001:
        logger.info("✅ calculate_pnl (long) works correctly")
    else:
        logger.error("❌ calculate_pnl (long) incorrect")
        return False

    pnl_usd, pnl_pct = calculate_pnl("short", size_usd, entry, exit_price)
    expected_usd = 100.0 * (50000.0 - 51000.0) / 50000.0
    expected_pct = (50000.0 - 51000.0) / 50000.0

    logger.info(
        f"Short P&L: ${pnl_usd:.2f} ({pnl_pct:.2%}), Expected: ${expected_usd:.2f} ({expected_pct:.2%})"
    )

    if abs(pnl_usd - expected_usd) < 0.01 and abs(pnl_pct - expected_pct) < 0.0001:
        logger.info("✅ calculate_pnl (short) works correctly")
        return True
    else:
        logger.error("❌ calculate_pnl (short) incorrect")
        return False


def test_check_exit_conditions():
    """Test exit condition checking."""
    logger.info("Testing check_exit_conditions...")

    position_long = {
        "asset": "BTC",
        "direction": "long",
        "entry_price": 50000.0,
        "tp_price": 51000.0,
        "sl_price": 49000.0,
    }

    result = check_exit_conditions(position_long, 51000.0)
    if result == "TP":
        logger.info("✅ Long TP hit detected")
    else:
        logger.error(f"❌ Long TP not detected, got: {result}")
        return False

    result = check_exit_conditions(position_long, 49000.0)
    if result == "SL":
        logger.info("✅ Long SL hit detected")
    else:
        logger.error(f"❌ Long SL not detected, got: {result}")
        return False

    position_short = {
        "asset": "BTC",
        "direction": "short",
        "entry_price": 50000.0,
        "tp_price": 49000.0,
        "sl_price": 51000.0,
    }

    result = check_exit_conditions(position_short, 49000.0)
    if result == "TP":
        logger.info("✅ Short TP hit detected")
    else:
        logger.error(f"❌ Short TP not detected, got: {result}")
        return False

    result = check_exit_conditions(position_short, 51000.0)
    if result == "SL":
        logger.info("✅ Short SL hit detected")
        return True
    else:
        logger.error(f"❌ Short SL not detected, got: {result}")
        return False


def test_check_signal_validity():
    """Test signal validity checking."""
    logger.info("Testing check_signal_validity...")

    signal = Signal(
        asset="BTC",
        direction="long",
        entry_price=50000.0,
        take_profit=51000.0,
        stop_loss=49000.0,
        win_rate=0.71,
        vol_spread_pct=1.8,
        timestamp=datetime.utcnow(),
        percentiles_snapshot={},
    )

    current_price = 50100.0
    existing_positions = []

    is_valid, reason = check_signal_validity(signal, current_price, existing_positions)

    if is_valid:
        logger.info("✅ Fresh signal with no existing positions is valid")
    else:
        logger.error(f"❌ Fresh signal rejected: {reason}")
        return False

    signal_old = Signal(
        asset="BTC",
        direction="long",
        entry_price=50000.0,
        take_profit=51000.0,
        stop_loss=49000.0,
        win_rate=0.71,
        vol_spread_pct=1.8,
        timestamp=datetime.utcnow() - timedelta(minutes=11),
        percentiles_snapshot={},
    )

    is_valid, reason = check_signal_validity(
        signal_old, current_price, existing_positions
    )

    if not is_valid and "too old" in reason:
        logger.info("✅ Old signal correctly rejected")
    else:
        logger.error(f"❌ Old signal not rejected, reason: {reason}")
        return False

    existing_position = {
        "id": 1,
        "asset": "BTC",
        "direction": "long",
        "status": "open",
    }

    is_valid, reason = check_signal_validity(signal, current_price, [existing_position])

    if not is_valid and "existing position" in reason:
        logger.info("✅ Signal with existing position correctly rejected")
        return True
    else:
        logger.error(f"❌ Signal with existing position not rejected, reason: {reason}")
        return False


async def test_hl_client():
    """Test Hyperliquid client initialization and price fetching."""
    logger.info("Testing HLClient...")

    try:
        validate_config()
        hl_client = HLClient(HL_WALLET_ADDRESS, HL_PRIVATE_KEY, HL_TESTNET)
        logger.info("✅ HLClient initialized successfully")

        price = hl_client.get_mid_price("BTC")
        logger.info(f"✅ BTC mid price: ${price:.2f}")

        mids = hl_client.get_all_mids()
        logger.info(f"✅ Fetched prices for {len(mids)} assets")

        return True
    except Exception as e:
        logger.error(f"❌ HLClient test failed: {e}")
        return False


async def test_synth_client():
    """Test Synth API client."""
    logger.info("Testing SynthClient...")

    try:
        synth_client = SynthClient()
        logger.info("✅ SynthClient initialized")

        async with synth_client:
            data = await synth_client.get_prediction_percentiles("BTC", "1h")
            logger.info(f"✅ Fetched Synth data for BTC")

            current_price = data.get("current_price")
            logger.info(f"✅ BTC current price: ${current_price:.2f}")

        return True
    except Exception as e:
        logger.error(f"❌ SynthClient test failed: {e}")
        return False


async def main():
    """Run all tests."""
    logger.info("=" * 60)
    logger.info("Starting component tests...")
    logger.info("=" * 60)

    results = []

    results.append(("calculate_position_size", test_calculate_position_size()))
    results.append(("calculate_pnl", test_calculate_pnl()))
    results.append(("check_exit_conditions", test_check_exit_conditions()))
    results.append(("check_signal_validity", test_check_signal_validity()))
    results.append(("HLClient", await test_hl_client()))
    results.append(("SynthClient", await test_synth_client()))

    logger.info("=" * 60)
    logger.info("Test Results Summary:")
    logger.info("=" * 60)

    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        logger.info(f"{status}: {test_name}")

    all_passed = all(result for _, result in results)

    logger.info("=" * 60)
    if all_passed:
        logger.info("🎉 All tests passed!")
    else:
        logger.error("❌ Some tests failed")
    logger.info("=" * 60)

    return all_passed


if __name__ == "__main__":
    from datetime import timedelta

    success = asyncio.run(main())
    exit(0 if success else 1)
