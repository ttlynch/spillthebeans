"""Execution helper functions for trading pipeline."""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Tuple, Optional, Dict, Any

from hl_client import HLClient
from strategy import Signal
from db import get_open_positions, save_position, update_position
from config import HL_WALLET_ADDRESS, HL_PRIVATE_KEY, HL_TESTNET

logger = logging.getLogger(__name__)


def calculate_position_size(size_usd: float, price: float, sz_decimals: int) -> float:
    """Calculate position size in tokens from USD amount.

    Args:
        size_usd: Position size in USD
        price: Current asset price
        sz_decimals: Number of decimals for size from asset metadata

    Returns:
        Position size in tokens, rounded to appropriate decimals
    """
    size_tokens = size_usd / price
    size_rounded = round(size_tokens, sz_decimals)
    logger.info(
        f"Calculated position size: ${size_usd} / ${price:.2f} = {size_rounded} tokens (sz_decimals={sz_decimals})"
    )
    return size_rounded


def calculate_pnl(
    direction: str, size_usd: float, entry: float, exit_price: float
) -> Tuple[float, float]:
    """Calculate P&L in USD and percentage.

    Args:
        direction: "long" or "short"
        size_usd: Position size in USD
        entry: Entry price
        exit_price: Exit price

    Returns:
        Tuple of (pnl_usd, pnl_pct)
    """
    if direction == "long":
        pnl_pct = (exit_price - entry) / entry
        pnl_usd = size_usd * pnl_pct
    else:
        pnl_pct = (entry - exit_price) / entry
        pnl_usd = size_usd * pnl_pct

    logger.info(
        f"Calculated P&L: {direction.upper()} ${size_usd:.0f}, entry=${entry:.2f}, exit=${exit_price:.2f}, pnl=${pnl_usd:.2f} ({pnl_pct:+.2%})"
    )
    return pnl_usd, pnl_pct


def check_exit_conditions(
    position: Dict[str, Any], current_price: float
) -> Optional[str]:
    """Check if TP or SL is hit.

    Args:
        position: Position dict from DB (must have: direction, tp_price, sl_price)
        current_price: Current market price

    Returns:
        Exit reason ("TP", "SL") or None
    """
    direction = position.get("direction")
    tp_price = position.get("tp_price")
    sl_price = position.get("sl_price")

    if not direction:
        logger.warning(f"Position missing direction: {position}")
        return None

    if tp_price is None or sl_price is None:
        logger.warning(f"Position missing tp_price or sl_price: {position}")
        return None

    if direction == "long":
        if current_price >= float(tp_price):
            return "TP"
        elif current_price <= float(sl_price):
            return "SL"
    elif direction == "short":
        if current_price <= float(tp_price):
            return "TP"
        elif current_price >= float(sl_price):
            return "SL"

    return None


def check_signal_validity(
    signal: Signal, current_price: float, existing_positions: list
) -> Tuple[bool, str]:
    """Check if signal is still valid for execution.

    Validates:
    1. Entry price hasn't moved more than 0.5% from signal's entry
    2. Signal is less than 10 minutes old
    3. No existing open position for the same asset

    Args:
        signal: Signal object
        current_price: Current market price
        existing_positions: List of open positions from DB

    Returns:
        Tuple of (is_valid, reason)
    """
    now = datetime.utcnow()
    signal_age = (now - signal.timestamp).total_seconds() / 60

    if signal_age > 10:
        reason = f"Signal too old ({signal_age:.1f} minutes > 10 min limit)"
        logger.info(f"Signal invalid: {reason}")
        return False, reason

    entry_diff_pct = abs(current_price - signal.entry_price) / signal.entry_price * 100
    if entry_diff_pct > 0.5:
        reason = f"Price moved too much ({entry_diff_pct:.2f}% > 0.5% limit)"
        logger.info(f"Signal invalid: {reason}")
        return False, reason

    for pos in existing_positions:
        if pos.get("asset") == signal.asset:
            reason = f"Already have open position for {signal.asset}"
            logger.info(f"Signal invalid: {reason}")
            return False, reason

    logger.info(f"Signal valid: {signal.asset} {signal.direction}")
    return True, ""


async def execute_signal(
    signal_id: int,
    position_size_usd: float,
    db_conn,
    hl_client: HLClient,
    telegram_app,
) -> Optional[int]:
    """Execute a trading signal.

    Args:
        signal_id: Signal ID from database
        position_size_usd: Position size in USD
        db_conn: Database connection
        hl_client: Hyperliquid client
        telegram_app: Telegram bot application

    Returns:
        Position ID if successful, None otherwise
    """
    from config import TELEGRAM_CHAT_ID

    try:
        cursor = db_conn.cursor()
        signal_row = cursor.execute(
            "SELECT * FROM signals WHERE id = ?", (signal_id,)
        ).fetchone()

        if not signal_row:
            logger.error(f"Signal {signal_id} not found in database")
            return None

        signal = Signal(
            asset=signal_row["asset"],
            direction=signal_row["direction"],
            entry_price=signal_row["entry"],
            take_profit=signal_row["tp"],
            stop_loss=signal_row["sl"],
            win_rate=signal_row["win_rate"],
            vol_spread_pct=signal_row["vol_spread"],
            timestamp=datetime.fromisoformat(signal_row["timestamp"]),
            percentiles_snapshot={},
        )
        signal.id = signal_id

        logger.info(
            f"EXECUTING: {signal.asset} {signal.direction.upper()}, "
            f"Entry ${signal.entry_price:.2f}, Size ${position_size_usd:.0f}"
        )

        current_price = hl_client.get_mid_price(signal.asset)

        existing_positions = get_open_positions(db_conn)
        is_valid, reason = check_signal_validity(
            signal, current_price, existing_positions
        )

        if not is_valid:
            logger.warning(f"Signal execution failed: {reason}")
            await telegram_app.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=f"⚠️ {signal.asset} {signal.direction.upper()} execution failed:\n{reason}",
            )
            return None

        is_buy = signal.direction == "long"

        asset_meta = hl_client.get_asset_meta(signal.asset)
        sz_decimals = asset_meta["szDecimals"]
        size_tokens = calculate_position_size(
            position_size_usd, current_price, sz_decimals
        )

        result = hl_client.market_open(signal.asset, is_buy, size_tokens)
        logger.info(f"Market order executed: {result}")

        tp_is_buy = not is_buy
        tp_result = hl_client.limit_order(
            signal.asset,
            tp_is_buy,
            size_tokens,
            signal.take_profit,
            reduce_only=True,
        )
        logger.info(f"TP limit order placed: {tp_result}")

        position_id = save_position(
            conn=db_conn,
            signal_id=signal_id,
            asset=signal.asset,
            direction=signal.direction,
            size_usd=position_size_usd,
            size_tokens=size_tokens,
            entry_price=current_price,
            tp_price=signal.take_profit,
            sl_price=signal.stop_loss,
        )

        cursor.execute(
            "UPDATE signals SET status = 'executed' WHERE id = ?", (signal_id,)
        )
        db_conn.commit()

        await telegram_app.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=(
                f"✅ {signal.asset} {signal.direction.upper()} Position Opened\n"
                f"Entry: ${current_price:,.2f}\n"
                f"Size: ${position_size_usd:.0f} ({size_tokens:.4f} tokens)\n"
                f"TP: ${signal.take_profit:,.2f}\n"
                f"SL: ${signal.stop_loss:,.2f}"
            ),
        )

        logger.info(
            f"Successfully executed signal {signal_id}, position_id={position_id}"
        )
        return position_id

    except Exception as e:
        logger.error(f"Failed to execute signal {signal_id}: {e}", exc_info=True)
        await telegram_app.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=f"⚠️ Execution failed for signal {signal_id}. Check logs.",
        )
        return None

        signal = Signal(
            asset=signal_row["asset"],
            direction=signal_row["direction"],
            entry_price=signal_row["entry"],
            take_profit=signal_row["tp"],
            stop_loss=signal_row["sl"],
            win_rate=signal_row["win_rate"],
            vol_spread_pct=signal_row["vol_spread"],
            timestamp=datetime.fromisoformat(signal_row["timestamp"]),
            percentiles_snapshot={},
        )
        signal.id = signal_id

        logger.info(
            f"EXECUTING: {signal.asset} {signal.direction.upper()}, "
            f"Entry ${signal.entry_price:.2f}, Size ${position_size_usd:.0f}"
        )

        current_price = hl_client.get_mid_price(signal.asset)

        existing_positions = get_open_positions(db_conn)
        is_valid, reason = check_signal_validity(
            signal, current_price, existing_positions
        )

        if not is_valid:
            logger.warning(f"Signal execution failed: {reason}")
            await telegram_app.bot.send_message(
                chat_id=db_conn.execute(
                    "SELECT value FROM config WHERE key='telegram_chat_id'"
                ).fetchone()["value"],
                text=f"⚠️ {signal.asset} {signal.direction.upper()} execution failed:\n{reason}",
            )
            return None

        is_buy = signal.direction == "long"

        asset_meta = hl_client.get_asset_meta(signal.asset)
        sz_decimals = asset_meta["szDecimals"]
        size_tokens = calculate_position_size(
            position_size_usd, current_price, sz_decimals
        )

        result = hl_client.market_open(signal.asset, is_buy, size_tokens)
        logger.info(f"Market order executed: {result}")

        tp_is_buy = not is_buy
        tp_result = hl_client.limit_order(
            signal.asset,
            tp_is_buy,
            size_tokens,
            signal.take_profit,
            reduce_only=True,
        )
        logger.info(f"TP limit order placed: {tp_result}")

        position_id = save_position(
            conn=db_conn,
            signal_id=signal_id,
            asset=signal.asset,
            direction=signal.direction,
            size_usd=position_size_usd,
            size_tokens=size_tokens,
            entry_price=current_price,
            tp_price=signal.take_profit,
            sl_price=signal.stop_loss,
        )

        cursor.execute(
            "UPDATE signals SET status = 'executed' WHERE id = ?", (signal_id,)
        )
        db_conn.commit()

        await telegram_app.bot.send_message(
            chat_id=db_conn.execute(
                "SELECT value FROM config WHERE key='telegram_chat_id'"
            ).fetchone()["value"],
            text=(
                f"✅ {signal.asset} {signal.direction.upper()} Position Opened\n"
                f"Entry: ${current_price:,.2f}\n"
                f"Size: ${position_size_usd:.0f} ({size_tokens:.4f} tokens)\n"
                f"TP: ${signal.take_profit:,.2f}\n"
                f"SL: ${signal.stop_loss:,.2f}"
            ),
        )

        logger.info(
            f"Successfully executed signal {signal_id}, position_id={position_id}"
        )
        return position_id

    except Exception as e:
        logger.error(f"Failed to execute signal {signal_id}: {e}", exc_info=True)
        await telegram_app.bot.send_message(
            chat_id=db_conn.execute(
                "SELECT value FROM config WHERE key='telegram_chat_id'"
            ).fetchone()["value"],
            text=f"⚠️ Execution failed for signal {signal_id}. Check logs.",
        )
        return None


async def position_monitor(
    db_conn,
    hl_client: HLClient,
    telegram_app,
    check_interval: int = 60,
    update_interval: int = 300,
) -> None:
    """Monitor open positions and manage TP/SL.

    Args:
        db_conn: Database connection
        hl_client: Hyperliquid client
        telegram_app: Telegram bot application
        check_interval: How often to check positions (seconds)
        update_interval: How often to send P&L updates (seconds)
    """
    logger.info("Starting position monitor loop")
    last_update_time = datetime.utcnow()

    while True:
        try:
            now = datetime.utcnow()
            should_send_update = (
                now - last_update_time
            ).total_seconds() >= update_interval

            positions = get_open_positions(db_conn)

            if not positions:
                logger.debug("No open positions to monitor")
                await asyncio.sleep(check_interval)
                continue

            logger.info(f"Monitoring {len(positions)} open positions")

            for position in positions:
                try:
                    asset = position["asset"]
                    direction = position["direction"]
                    entry = position["entry_price"]
                    tp_price = position["tp_price"]
                    sl_price = position["sl_price"]
                    size_usd = position["size_usd"]

                    current_price = hl_client.get_mid_price(asset)
                    pnl_usd, pnl_pct = calculate_pnl(
                        direction, size_usd, entry, current_price
                    )

                    exit_reason = check_exit_conditions(position, current_price)

                    if exit_reason:
                        logger.info(
                            f"Exit condition triggered for {asset}: {exit_reason}"
                        )

                        result = hl_client.market_close(asset)
                        logger.info(f"Closed position: {result}")

                        opened_at = datetime.fromisoformat(position["opened_at"])
                        duration_min = (now - opened_at).total_seconds() / 60

                        update_position(
                            conn=db_conn,
                            position_id=position["id"],
                            status="closed",
                            closed_at=now.isoformat(),
                            pnl=pnl_usd,
                        )

                        from chart_renderer import render_pnl_summary
                        from telegram_bot import send_close_summary

                        chart_image = render_pnl_summary(
                            asset=asset,
                            direction=direction,
                            entry=entry,
                            exit_price=current_price,
                            pnl_usd=pnl_usd,
                            pnl_pct=pnl_pct * 100,
                            duration_min=int(duration_min),
                        )

                        await send_close_summary(
                            pnl_chart=chart_image,
                            asset=asset,
                            direction=direction,
                            entry=entry,
                            exit_price=current_price,
                            pnl_usd=pnl_usd,
                            pnl_pct=pnl_pct * 100,
                            duration_min=int(duration_min),
                            application=telegram_app,
                        )

                    elif should_send_update:
                        opened_at = datetime.fromisoformat(position["opened_at"])
                        duration_min = (now - opened_at).total_seconds() / 60

                        from telegram_bot import send_pnl_update

                        await send_pnl_update(
                            asset=asset,
                            direction=direction,
                            entry=entry,
                            current_price=current_price,
                            unrealized_pnl=pnl_usd,
                            duration_min=int(duration_min),
                            application=telegram_app,
                        )

                except Exception as e:
                    logger.error(
                        f"Error monitoring position {position['id']}: {e}",
                        exc_info=True,
                    )
                    continue

            if should_send_update and positions:
                last_update_time = now

        except Exception as e:
            logger.error(f"Error in position monitor loop: {e}", exc_info=True)

        await asyncio.sleep(check_interval)


async def synth_poller(
    db_conn,
    synth_client,
    telegram_app,
    interval: int = 180,
) -> None:
    """Poll Synth API for trading signals.

    Args:
        db_conn: Database connection
        synth_client: Synth API client
        telegram_app: Telegram bot application
        interval: Polling interval in seconds (default 3 min)
    """
    logger.info("Starting Synth API poller loop")

    from strategy import evaluate_signal
    from db import save_signal
    from chart_renderer import fetch_candles, render_signal_chart
    from telegram_bot import send_signal_alert

    assets = ["BTC", "ETH", "SOL"]

    while True:
        try:
            logger.info(f"Polling Synth API for {len(assets)} assets")

            for asset in assets:
                try:
                    async with synth_client:
                        percentile_data = await synth_client.get_prediction_percentiles(
                            asset, horizon="1h"
                        )

                    signal = evaluate_signal(asset, percentile_data)

                    if signal:
                        signal_id = save_signal(db_conn, signal, status="pending")
                        signal.id = signal_id

                        logger.info(
                            f"Generated signal: {asset} {signal.direction.upper()}"
                        )

                        from hl_client import HLClient
                        from config import HL_WALLET_ADDRESS, HL_PRIVATE_KEY, HL_TESTNET

                        hl_client = HLClient(
                            HL_WALLET_ADDRESS, HL_PRIVATE_KEY, HL_TESTNET
                        )

                        candle_data = fetch_candles(asset, num_candles=60)
                        percentile_band = (
                            signal.stop_loss,
                            signal.take_profit,
                        )

                        chart_image = render_signal_chart(
                            candle_data=candle_data,
                            signal=signal,
                            percentile_band=percentile_band,
                            asset=asset,
                        )

                        await send_signal_alert(
                            signal=signal,
                            chart_image=chart_image,
                            default_size=100.0,
                            application=telegram_app,
                        )

                except Exception as e:
                    logger.error(f"Error polling {asset}: {e}", exc_info=True)
                    continue

        except Exception as e:
            logger.error(f"Error in synth poller loop: {e}", exc_info=True)

        await asyncio.sleep(interval)
