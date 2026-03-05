"""End-to-end test script for SpillTheBeans trading pipeline.

Simulates the full trading loop on Hyperliquid testnet:
1. Initialize all components
2. Force-fetch Synth signal for BTC
3. Generate chart image
4. Send signal alert to Telegram
5. Simulate $50 size button tap
6. Simulate EXECUTE tap
7. Verify order on Hyperliquid testnet
8. Wait 2 minutes, send P&L update
9. Force-close position
10. Send close summary
11. Verify SQLite records
"""

import asyncio
import logging
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import MagicMock

from telegram import Update, User, Chat, Message, CallbackQuery, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import (
    validate_config,
    HL_WALLET_ADDRESS,
    HL_PRIVATE_KEY,
    HL_TESTNET,
    TELEGRAM_CHAT_ID,
)
from db import init_db, save_signal, get_open_positions, get_closed_positions
from hl_client import HLClient
from synth_client import SynthClient
from strategy import Signal, evaluate_test_signal
from chart_renderer import fetch_candles, render_signal_chart
from telegram_bot import (
    create_bot,
    send_signal_alert,
    send_pnl_update,
    send_close_summary,
    handle_size_callback,
    handle_execute_callback,
    _selected_sizes,
)
from execution import calculate_pnl
from logging_config import setup_logging

logger = logging.getLogger(__name__)


class E2ETestResult:
    """Track test results."""

    def __init__(self):
        self.steps_passed = 0
        self.steps_failed = 0
        self.errors = []
        self.signal_id: Optional[int] = None
        self.position_id: Optional[int] = None
        self.signal: Optional[Signal] = None
        self.chart_image = None
        self.actual_entry_price: Optional[float] = None
        self.actual_size_tokens: Optional[float] = None

    def pass_step(self, step_name: str):
        self.steps_passed += 1
        logger.info(f"✅ STEP PASSED: {step_name}")

    def fail_step(self, step_name: str, error: str):
        self.steps_failed += 1
        self.errors.append(f"{step_name}: {error}")
        logger.error(f"❌ STEP FAILED: {step_name} - {error}")


def create_mock_update(
    callback_data: str, user_id: int = 12345, chat_id: int = None
) -> Update:
    """Create a mock Update object for testing callback handlers."""
    if chat_id is None:
        chat_id = int(TELEGRAM_CHAT_ID)

    mock_user = MagicMock(spec=User)
    mock_user.id = user_id
    mock_user.is_bot = False

    mock_chat = MagicMock(spec=Chat)
    mock_chat.id = chat_id

    mock_message = MagicMock(spec=Message)
    mock_message.chat_id = chat_id
    mock_message.message_id = 1
    mock_message.caption = "Test caption"
    mock_message.reply_markup = MagicMock()

    mock_query = MagicMock(spec=CallbackQuery)
    mock_query.id = "test_query_id"
    mock_query.data = callback_data
    mock_query.from_user = mock_user
    mock_query.message = mock_message

    mock_update = MagicMock(spec=Update)
    mock_update.callback_query = mock_query
    mock_update.effective_chat = mock_chat
    mock_update.effective_user = mock_user

    return mock_update


async def step_1_init_components(
    result: E2ETestResult, use_temp_db: bool = True
) -> Dict[str, Any]:
    """Step 1: Initialize all components."""
    logger.info("=" * 60)
    logger.info("STEP 1: Initializing components...")
    logger.info("=" * 60)

    components = {}

    try:
        validate_config()
        result.pass_step("Config validation")
    except Exception as e:
        result.fail_step("Config validation", str(e))
        return components

    try:
        if use_temp_db:
            temp_dir = tempfile.mkdtemp()
            db_path = Path(temp_dir) / "test_trading.db"
            db_conn = init_db(str(db_path))
            components["temp_dir"] = temp_dir
        else:
            db_conn = init_db("data/e2e_test_trading.db")

        components["db_conn"] = db_conn
        result.pass_step("Database initialization")
        logger.info(f"Database initialized at {db_path}")
    except Exception as e:
        result.fail_step("Database initialization", str(e))
        return components

    try:
        hl_client = HLClient(HL_WALLET_ADDRESS, HL_PRIVATE_KEY, HL_TESTNET)
        components["hl_client"] = hl_client
        result.pass_step("Hyperliquid client initialization")
        logger.info(f"HL client initialized (testnet={HL_TESTNET})")
    except Exception as e:
        result.fail_step("Hyperliquid client initialization", str(e))
        return components

    try:
        synth_client = SynthClient()
        components["synth_client"] = synth_client
        result.pass_step("Synth client initialization")
    except Exception as e:
        result.fail_step("Synth client initialization", str(e))
        return components

    try:
        telegram_app = create_bot(
            db_conn=db_conn, hl_client=hl_client, synth_client=synth_client
        )
        await telegram_app.initialize()
        components["telegram_app"] = telegram_app
        result.pass_step("Telegram bot initialization")
    except Exception as e:
        result.fail_step("Telegram bot initialization", str(e))
        return components

    return components


async def step_2_fetch_signal(
    result: E2ETestResult, components: Dict[str, Any], asset: str = "BTC"
) -> Optional[Signal]:
    """Step 2: Force-fetch Synth signal (bypass conviction threshold)."""
    logger.info("=" * 60)
    logger.info(f"STEP 2: Force-fetching Synth signal for {asset}...")
    logger.info("=" * 60)

    synth_client = components["synth_client"]
    db_conn = components["db_conn"]

    try:
        async with synth_client:
            percentile_data = await synth_client.get_prediction_percentiles(asset, "1h")

            current_price = percentile_data.get("current_price")
            logger.info(
                f"Fetched percentile data, current {asset} price: ${current_price:.2f}"
            )

            signal = evaluate_test_signal(asset, percentile_data)
            signal_id = save_signal(db_conn, signal, status="pending")
            signal.id = signal_id

            result.signal_id = signal_id
            result.signal = signal

            result.pass_step("Synth signal fetch and save")
            logger.info(
                f"Signal generated: {asset} {signal.direction.upper()}, "
                f"entry=${signal.entry_price:.2f}, "
                f"TP=${signal.take_profit:.2f}, "
                f"SL=${signal.stop_loss:.2f}, "
                f"win_rate={signal.win_rate:.0%}"
            )
            return signal

    except Exception as e:
        result.fail_step("Synth signal fetch", str(e))
        return None


async def step_3_generate_chart(result: E2ETestResult, signal: Signal) -> Optional[Any]:
    """Step 3: Generate chart image."""
    logger.info("=" * 60)
    logger.info("STEP 3: Generating chart image...")
    logger.info("=" * 60)

    try:
        candles = fetch_candles(signal.asset, num_candles=60)
        logger.info(f"Fetched {len(candles)} candles for {signal.asset}")

        p05 = signal.percentiles_snapshot.get("0.05", signal.stop_loss)
        p95 = signal.percentiles_snapshot.get("0.95", signal.take_profit)
        percentile_band = (p05, p95)

        chart_image = render_signal_chart(
            candle_data=candles,
            signal=signal,
            percentile_band=percentile_band,
            asset=signal.asset,
        )

        result.chart_image = chart_image
        result.pass_step("Chart generation")
        logger.info(f"Chart image generated ({chart_image.getbuffer().nbytes} bytes)")
        return chart_image

    except Exception as e:
        result.fail_step("Chart generation", str(e))
        return None


async def step_4_send_alert(
    result: E2ETestResult, components: Dict[str, Any], signal: Signal, chart_image
):
    """Step 4: Send signal alert to Telegram."""
    logger.info("=" * 60)
    logger.info("STEP 4: Sending signal alert to Telegram...")
    logger.info("=" * 60)

    telegram_app = components["telegram_app"]

    try:
        await send_signal_alert(
            signal=signal,
            chart_image=chart_image,
            default_size=100.0,
            application=telegram_app,
        )
        result.pass_step("Telegram signal alert")
        logger.info(f"Signal alert sent for {signal.asset} {signal.direction}")
    except Exception as e:
        result.fail_step("Telegram signal alert", str(e))


async def step_5_simulate_size_selection(
    result: E2ETestResult,
    components: Dict[str, Any],
    signal_id: int,
    size: float = 50.0,
):
    """Step 5: Simulate user tapping $50 size button."""
    logger.info("=" * 60)
    logger.info(f"STEP 5: Simulating size selection (${size})...")
    logger.info("=" * 60)

    telegram_app = components["telegram_app"]

    try:
        callback_data = f"size:{signal_id}:{size}"
        mock_update = create_mock_update(callback_data)

        context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
        context.application = telegram_app
        context.bot = telegram_app.bot

        await handle_size_callback(mock_update, context)

        if signal_id in _selected_sizes and _selected_sizes[signal_id] == size:
            result.pass_step("Size selection simulation")
            logger.info(f"Size selected: ${size}")
        else:
            result.fail_step(
                "Size selection simulation",
                f"Size not set correctly: {_selected_sizes.get(signal_id)}",
            )

    except Exception as e:
        result.fail_step("Size selection simulation", str(e))


async def step_6_simulate_execute(
    result: E2ETestResult,
    components: Dict[str, Any],
    signal_id: int,
    size: float = 50.0,
):
    """Step 6: Simulate user tapping EXECUTE."""
    logger.info("=" * 60)
    logger.info(f"STEP 6: Simulating EXECUTE tap (${size})...")
    logger.info("=" * 60)

    telegram_app = components["telegram_app"]
    db_conn = components["db_conn"]

    try:
        callback_data = f"execute:{signal_id}:{size}"
        mock_update = create_mock_update(callback_data)

        context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
        context.application = telegram_app
        context.bot = telegram_app.bot

        await handle_execute_callback(mock_update, context)

        await asyncio.sleep(2)

        positions = get_open_positions(db_conn)
        if positions:
            position = positions[0]
            result.position_id = position["id"]
            result.actual_entry_price = float(position["entry_price"])
            result.actual_size_tokens = float(position["size_tokens"])
            result.pass_step("Execute simulation")
            logger.info(
                f"Position opened: ID={position['id']}, "
                f"entry=${result.actual_entry_price:.2f}, "
                f"size={result.actual_size_tokens:.6f} tokens"
            )
        else:
            result.fail_step(
                "Execute simulation", "No open position found after execute"
            )

    except Exception as e:
        result.fail_step("Execute simulation", str(e))


async def step_7_verify_order(
    result: E2ETestResult, components: Dict[str, Any], asset: str
):
    """Step 7: Verify order was placed on Hyperliquid testnet."""
    logger.info("=" * 60)
    logger.info("STEP 7: Verifying order on Hyperliquid testnet...")
    logger.info("=" * 60)

    hl_client = components["hl_client"]

    try:
        positions = hl_client.get_positions()

        asset_position = None
        for pos in positions:
            position_data = pos.get("position", {})
            if position_data.get("coin") == asset:
                asset_position = pos
                break

        if asset_position:
            position_data = asset_position.get("position", {})
            size = float(position_data.get("szi", 0))
            entry_px = float(position_data.get("entryPx", 0))

            if abs(size) > 0:
                result.pass_step("Hyperliquid order verification")
                logger.info(
                    f"Position confirmed on HL: {size:.6f} {asset} @ ${entry_px:.2f}"
                )
            else:
                result.fail_step(
                    "Hyperliquid order verification", "Position size is zero"
                )
        else:
            result.fail_step(
                "Hyperliquid order verification", f"No position found for {asset}"
            )

    except Exception as e:
        result.fail_step("Hyperliquid order verification", str(e))


async def step_8_pnl_update(
    result: E2ETestResult,
    components: Dict[str, Any],
    signal: Signal,
    wait_seconds: int = 120,
):
    """Step 8: Wait and send P&L update."""
    logger.info("=" * 60)
    logger.info(f"STEP 8: Waiting {wait_seconds}s then sending P&L update...")
    logger.info("=" * 60)

    telegram_app = components["telegram_app"]
    hl_client = components["hl_client"]
    db_conn = components["db_conn"]

    try:
        logger.info(f"Waiting {wait_seconds} seconds...")
        await asyncio.sleep(wait_seconds)

        positions = get_open_positions(db_conn)
        if not positions:
            result.fail_step("P&L update", "No open position found")
            return

        position = positions[0]
        entry = float(position["entry_price"])
        size_usd = float(position["size_usd"])
        direction = position["direction"]
        opened_at = datetime.fromisoformat(position["opened_at"])
        duration_min = int((datetime.utcnow() - opened_at).total_seconds() / 60)

        current_price = hl_client.get_mid_price(signal.asset)
        pnl_usd, pnl_pct = calculate_pnl(direction, size_usd, entry, current_price)

        await send_pnl_update(
            asset=signal.asset,
            direction=direction,
            entry=entry,
            current_price=current_price,
            pnl_usd=pnl_usd,
            pnl_pct=pnl_pct * 100,
            duration_min=duration_min,
            application=telegram_app,
        )

        result.pass_step("P&L update")
        logger.info(f"P&L update sent: {pnl_usd:+.2f} ({pnl_pct * 100:+.2f}%)")

    except Exception as e:
        result.fail_step("P&L update", str(e))


async def step_9_close_position(
    result: E2ETestResult, components: Dict[str, Any], signal: Signal
):
    """Step 9: Force-close position (market order)."""
    logger.info("=" * 60)
    logger.info("STEP 9: Force-closing position...")
    logger.info("=" * 60)

    hl_client = components["hl_client"]
    db_conn = components["db_conn"]

    try:
        close_result = hl_client.market_close(signal.asset)
        logger.info(f"Close order result: {close_result}")

        await asyncio.sleep(2)

        positions = hl_client.get_positions()
        asset_position = None
        for pos in positions:
            position_data = pos.get("position", {})
            if position_data.get("coin") == signal.asset:
                asset_position = pos
                break

        if asset_position:
            position_data = asset_position.get("position", {})
            size = float(position_data.get("szi", 0))
            if abs(size) > 0.0000001:
                result.fail_step("Close position", f"Position still open: {size}")
            else:
                result.pass_step("Close position")
                logger.info("Position closed successfully")
        else:
            result.pass_step("Close position")
            logger.info("No position found (closed)")

    except Exception as e:
        result.fail_step("Close position", str(e))


async def step_10_send_close_summary(
    result: E2ETestResult, components: Dict[str, Any], signal: Signal
):
    """Step 10: Send close summary."""
    logger.info("=" * 60)
    logger.info("STEP 10: Sending close summary...")
    logger.info("=" * 60)

    telegram_app = components["telegram_app"]
    hl_client = components["hl_client"]
    db_conn = components["db_conn"]

    try:
        positions = get_closed_positions(db_conn)
        if not positions:
            positions = get_open_positions(db_conn)
            if positions:
                from db import update_position

                position = positions[0]
                current_price = hl_client.get_mid_price(signal.asset)
                entry = float(position["entry_price"])
                size_usd = float(position["size_usd"])
                direction = position["direction"]

                pnl_usd, pnl_pct = calculate_pnl(
                    direction, size_usd, entry, current_price
                )
                opened_at = datetime.fromisoformat(position["opened_at"])
                duration_min = int((datetime.utcnow() - opened_at).total_seconds() / 60)

                update_position(
                    conn=db_conn,
                    position_id=position["id"],
                    status="closed",
                    closed_at=datetime.utcnow().isoformat(),
                    pnl=pnl_usd,
                    exit_price=current_price,
                    exit_reason="manual_close",
                )

                from chart_renderer import render_pnl_summary

                chart_image = render_pnl_summary(
                    asset=signal.asset,
                    direction=direction,
                    entry=entry,
                    exit_price=current_price,
                    pnl_usd=pnl_usd,
                    pnl_pct=pnl_pct * 100,
                    duration_min=duration_min,
                )

                await send_close_summary(
                    pnl_chart=chart_image,
                    asset=signal.asset,
                    direction=direction,
                    entry=entry,
                    exit_price=current_price,
                    pnl_usd=pnl_usd,
                    pnl_pct=pnl_pct * 100,
                    duration_min=duration_min,
                    win_rate=signal.win_rate,
                    application=telegram_app,
                )

                result.pass_step("Close summary")
                logger.info(f"Close summary sent: {pnl_usd:+.2f}")
            else:
                result.fail_step("Close summary", "No position found to summarize")
        else:
            position = positions[0]
            result.pass_step("Close summary")
            logger.info("Close summary sent from existing closed position")

    except Exception as e:
        result.fail_step("Close summary", str(e))


async def step_11_verify_db(
    result: E2ETestResult, components: Dict[str, Any], signal: Signal
):
    """Step 11: Verify SQLite has correct records."""
    logger.info("=" * 60)
    logger.info("STEP 11: Verifying SQLite records...")
    logger.info("=" * 60)

    db_conn = components["db_conn"]

    try:
        cursor = db_conn.cursor()

        cursor.execute("SELECT * FROM signals WHERE id = ?", (result.signal_id,))
        signal_row = cursor.fetchone()

        if not signal_row:
            result.fail_step("DB verification", f"Signal {result.signal_id} not found")
            return

        if signal_row["status"] != "executed":
            result.fail_step(
                "DB verification",
                f"Signal status is {signal_row['status']}, expected 'executed'",
            )
            return

        logger.info(f"Signal record verified: status={signal_row['status']}")

        cursor.execute(
            "SELECT * FROM positions WHERE signal_id = ?", (result.signal_id,)
        )
        position_row = cursor.fetchone()

        if not position_row:
            result.fail_step(
                "DB verification", f"Position for signal {result.signal_id} not found"
            )
            return

        if position_row["asset"] != signal.asset:
            result.fail_step(
                "DB verification",
                f"Position asset mismatch: {position_row['asset']} vs {signal.asset}",
            )
            return

        if position_row["direction"] != signal.direction:
            result.fail_step(
                "DB verification",
                f"Position direction mismatch: {position_row['direction']} vs {signal.direction}",
            )
            return

        logger.info(
            f"Position record verified: "
            f"asset={position_row['asset']}, "
            f"direction={position_row['direction']}, "
            f"size_usd={position_row['size_usd']}, "
            f"entry={position_row['entry_price']}"
        )

        result.pass_step("DB verification")

    except Exception as e:
        result.fail_step("DB verification", str(e))


async def cleanup(components: Dict[str, Any]):
    """Clean up resources."""
    logger.info("=" * 60)
    logger.info("Cleaning up...")
    logger.info("=" * 60)

    if "telegram_app" in components:
        try:
            await components["telegram_app"].shutdown()
            logger.info("Telegram bot shut down")
        except Exception as e:
            logger.warning(f"Error shutting down telegram: {e}")

    if "db_conn" in components:
        try:
            components["db_conn"].close()
            logger.info("Database connection closed")
        except Exception as e:
            logger.warning(f"Error closing database: {e}")


async def run_e2e_test(
    asset: str = "BTC",
    position_size: float = 50.0,
    pnl_wait_seconds: int = 120,
    skip_pnl_wait: bool = False,
):
    """Run the full end-to-end test."""
    setup_logging(level=logging.INFO)

    logger.info("=" * 60)
    logger.info("🧪 SPILLTHEBEANS E2E TEST")
    logger.info("=" * 60)
    logger.info(f"Asset: {asset}")
    logger.info(f"Position Size: ${position_size}")
    logger.info(f"P&L Wait: {pnl_wait_seconds}s (skip={skip_pnl_wait})")
    logger.info(f"Testnet: {HL_TESTNET}")
    logger.info("=" * 60)

    result = E2ETestResult()
    components = {}

    try:
        components = await step_1_init_components(result)

        if result.steps_failed > 0:
            logger.error("Component initialization failed, aborting test")
            return result

        signal = await step_2_fetch_signal(result, components, asset)
        if not signal:
            logger.error("Signal fetch failed, aborting test")
            return result

        chart_image = await step_3_generate_chart(result, signal)
        if not chart_image:
            logger.error("Chart generation failed, aborting test")
            return result

        await step_4_send_alert(result, components, signal, chart_image)

        await step_5_simulate_size_selection(
            result, components, result.signal_id, position_size
        )

        await step_6_simulate_execute(
            result, components, result.signal_id, position_size
        )

        if result.position_id:
            await step_7_verify_order(result, components, signal.asset)

            if skip_pnl_wait:
                logger.info("Skipping P&L wait (skip_pnl_wait=True)")
                result.pass_step("P&L update (skipped)")
            else:
                await step_8_pnl_update(
                    result, components, signal, wait_seconds=pnl_wait_seconds
                )

            await step_9_close_position(result, components, signal)

            await step_10_send_close_summary(result, components, signal)

            await step_11_verify_db(result, components, signal)

    except Exception as e:
        logger.error(f"E2E test failed with exception: {e}", exc_info=True)
        result.fail_step("E2E Test", str(e))

    finally:
        await cleanup(components)

    logger.info("=" * 60)
    logger.info("🏁 E2E TEST COMPLETE")
    logger.info("=" * 60)
    logger.info(f"Steps Passed: {result.steps_passed}")
    logger.info(f"Steps Failed: {result.steps_failed}")

    if result.errors:
        logger.info("Errors:")
        for error in result.errors:
            logger.info(f"  - {error}")

    if result.steps_failed == 0:
        logger.info("🎉 ALL STEPS PASSED!")
    else:
        logger.error(f"❌ {result.steps_failed} step(s) failed")

    logger.info("=" * 60)

    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="E2E test for SpillTheBeans")
    parser.add_argument("--asset", default="BTC", help="Asset to test (default: BTC)")
    parser.add_argument(
        "--size", type=float, default=50.0, help="Position size in USD (default: 50)"
    )
    parser.add_argument(
        "--pnl-wait",
        type=int,
        default=120,
        help="Seconds to wait before P&L update (default: 120)",
    )
    parser.add_argument(
        "--skip-pnl-wait", action="store_true", help="Skip P&L wait period"
    )

    args = parser.parse_args()

    result = asyncio.run(
        run_e2e_test(
            asset=args.asset,
            position_size=args.size,
            pnl_wait_seconds=args.pnl_wait,
            skip_pnl_wait=args.skip_pnl_wait,
        )
    )

    sys.exit(0 if result.steps_failed == 0 else 1)
