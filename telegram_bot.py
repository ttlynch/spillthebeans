"""Telegram bot for trading signal alerts and execution."""

import asyncio
import io
import logging
import time
from datetime import datetime
from typing import Dict, Optional

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    HL_WALLET_ADDRESS,
    HL_PRIVATE_KEY,
    HL_TESTNET,
)
from db import (
    get_open_positions,
    get_signal_history,
    get_closed_positions,
    get_position_stats,
    init_db,
    save_signal,
)
from strategy import (
    Signal,
    calculate_trade_stats,
    evaluate_test_signal,
    evaluate_signal,
)
from chart_renderer import (
    render_pnl_summary,
    render_signal_chart,
    fetch_candles,
)
from hl_client import HLClient
from execution import execute_signal, calculate_pnl

logger = logging.getLogger(__name__)

_selected_sizes: Dict[int, float] = {}
_scan_cooldown_until: float = 0.0


def _get_main_keyboard() -> ReplyKeyboardMarkup:
    """Get persistent reply keyboard."""
    return ReplyKeyboardMarkup(
        [["📊 Status", "📜 History"], ["💰 Balance", "⚡ Scan Now"]],
        resize_keyboard=True,
    )


def _calculate_percentages(signal: Signal) -> tuple[float, float]:
    """Calculate TP and SL percentages from entry."""
    if signal.direction == "long":
        tp_pct = ((signal.take_profit - signal.entry_price) / signal.entry_price) * 100
        sl_pct = ((signal.stop_loss - signal.entry_price) / signal.entry_price) * 100
    else:
        tp_pct = ((signal.entry_price - signal.take_profit) / signal.entry_price) * 100
        sl_pct = ((signal.entry_price - signal.stop_loss) / signal.entry_price) * 100
    return tp_pct, sl_pct


def _format_signal_caption(signal: Signal, size: float) -> str:
    """Format signal alert caption with trade stats."""
    tp_pct, sl_pct = _calculate_percentages(signal)
    stats = calculate_trade_stats(signal, size)

    return (
        f"🔔 {signal.asset} {signal.direction.upper()} Signal\n"
        f"Entry: ${signal.entry_price:,.2f}\n"
        f"Take Profit: ${signal.take_profit:,.2f} ({tp_pct:+.2f}%)\n"
        f"Stop Loss: ${signal.stop_loss:,.2f} ({sl_pct:+.2f}%)\n"
        f"Win Rate: {signal.win_rate:.0%}\n"
        f"💰 ${size:.0f} position:\n"
        f"Expected Profit: ${stats['expected_profit']:.2f}\n"
        f"Max Loss: ${stats['max_loss']:.2f}"
    )


def _build_size_keyboard(signal_id: int, selected_size: float) -> InlineKeyboardMarkup:
    """Build inline keyboard with size buttons."""
    sizes = [50, 100, 250, 500]

    size_buttons = []
    for size in sizes:
        if size == selected_size:
            button_text = f"✓ ${size}"
        else:
            button_text = f"${size}"
        size_buttons.append(
            InlineKeyboardButton(button_text, callback_data=f"size:{signal_id}:{size}")
        )

    action_buttons = [
        InlineKeyboardButton(
            f"✅ EXECUTE ${selected_size:.0f}",
            callback_data=f"execute:{signal_id}:{selected_size}",
        ),
        InlineKeyboardButton("❌ Pass", callback_data=f"pass:{signal_id}"),
    ]

    return InlineKeyboardMarkup([size_buttons, action_buttons])


def _validate_chat(update: Update) -> bool:
    """Validate that message is from authorized chat."""
    if not update.effective_chat:
        return False
    return update.effective_chat.id == int(TELEGRAM_CHAT_ID)


async def send_signal_alert(
    signal: Signal,
    chart_image: io.BytesIO,
    default_size: float = 100.0,
    application: Application = None,
) -> None:
    """Send signal alert with chart and interactive buttons."""
    if application is None:
        logger.error("No application instance provided")
        return

    _selected_sizes[signal.id] = default_size

    caption = _format_signal_caption(signal, default_size)
    keyboard = _build_size_keyboard(signal.id, default_size)

    try:
        await application.bot.send_photo(
            chat_id=TELEGRAM_CHAT_ID,
            photo=chart_image,
            caption=caption,
            reply_markup=keyboard,
        )
        logger.info(f"Sent signal alert for {signal.asset} {signal.direction}")
    except Exception as e:
        logger.error(f"Failed to send signal alert: {e}", exc_info=True)
        try:
            await application.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text="⚠️ Failed to send signal chart. Check logs.",
                reply_markup=_get_main_keyboard(),
            )
        except Exception:
            pass
        raise


async def send_pnl_update(
    asset: str,
    direction: str,
    entry: float,
    current_price: float,
    pnl_usd: float,
    pnl_pct: float,
    duration_min: int,
    application: Application = None,
) -> None:
    """Send P&L update message."""
    if application is None:
        logger.error("No application instance provided")
        return

    is_profit = pnl_usd >= 0
    pnl_sign = "+" if is_profit else ""
    emoji = "✅" if is_profit else "❌"

    message = (
        f"📊 {asset} {direction.upper()} — {duration_min} min open\n"
        f"Entry: ${entry:,.2f} → Now: ${current_price:,.2f}\n"
        f"P&L: {pnl_sign}${abs(pnl_usd):.2f} ({pnl_sign}{pnl_pct:.2f}%) {emoji}"
    )

    try:
        await application.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID, text=message, reply_markup=_get_main_keyboard()
        )
        logger.info(f"Sent P&L update for {asset}")
    except Exception as e:
        logger.error(f"Failed to send P&L update: {e}", exc_info=True)
        raise


async def send_close_summary(
    pnl_chart: io.BytesIO,
    asset: str,
    direction: str,
    entry: float,
    exit_price: float,
    pnl_usd: float,
    pnl_pct: float,
    duration_min: int,
    win_rate: float,
    application: Application = None,
) -> None:
    """Send P&L summary chart."""
    if application is None:
        logger.error("No application instance provided")
        return

    is_profit = pnl_usd >= 0
    pnl_sign = "+" if is_profit else ""
    emoji = "✅" if is_profit else "❌"

    caption = (
        f"🏁 {asset} {direction.upper()} Closed\n"
        f"Entry: ${entry:,.2f} → Exit: ${exit_price:,.2f}\n"
        f"P&L: {pnl_sign}${abs(pnl_usd):.2f} ({pnl_sign}{pnl_pct:.2f}%) {emoji}\n"
        f"Duration: {duration_min} minutes\n"
        f"Signal Win Rate was: {win_rate:.0%}"
    )

    try:
        await application.bot.send_photo(
            chat_id=TELEGRAM_CHAT_ID,
            photo=pnl_chart,
            caption=caption,
            reply_markup=_get_main_keyboard(),
        )
        logger.info(f"Sent close summary for {asset}")
    except Exception as e:
        logger.error(f"Failed to send close summary: {e}", exc_info=True)
        raise


async def handle_size_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle size button press."""
    if not _validate_chat(update):
        return

    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    signal_id = int(parts[1])
    selected_size = float(parts[2])

    _selected_sizes[signal_id] = selected_size

    conn = context.application.bot_data.get("db_conn")
    if not conn:
        await query.edit_message_caption(caption="⚠️ Database error. Check logs.")
        logger.error("No database connection in bot_data")
        return

    signal_row = conn.execute(
        "SELECT * FROM signals WHERE id = ?", (signal_id,)
    ).fetchone()

    if not signal_row:
        await query.edit_message_caption(caption="⚠️ Signal not found.")
        return

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

    caption = _format_signal_caption(signal, selected_size)
    keyboard = _build_size_keyboard(signal_id, selected_size)

    await query.edit_message_caption(caption=caption, reply_markup=keyboard)

    logger.info(f"Updated size selection to ${selected_size} for signal {signal_id}")


async def handle_execute_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle EXECUTE button press."""
    if not _validate_chat(update):
        return

    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    signal_id = int(parts[1])
    size = float(parts[2])

    await query.edit_message_reply_markup(
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("⏳ Executing...", callback_data="none")]]
        )
    )

    conn = context.application.bot_data.get("db_conn")
    if not conn:
        await query.edit_message_caption(caption="⚠️ Database error. Check logs.")
        logger.error("No database connection in bot_data")
        return

    try:
        logger.info(f"EXECUTE REQUEST: Signal {signal_id}, Size ${size:.0f}")

        hl_client = context.application.bot_data.get("hl_client")
        if not hl_client:
            await query.edit_message_caption(
                caption="⚠️ Hyperliquid client not initialized."
            )
            logger.error("No Hyperliquid client in bot_data")
            return

        position_id = await execute_signal(
            signal_id=signal_id,
            position_size_usd=size,
            db_conn=conn,
            hl_client=hl_client,
            telegram_app=context.application,
        )

        if position_id:
            logger.info(
                f"Successfully executed signal {signal_id}, position_id={position_id}"
            )
        else:
            await query.edit_message_caption(caption="⚠️ Execution failed. Check logs.")

    except Exception as e:
        logger.error(f"Failed to execute signal {signal_id}: {e}", exc_info=True)

        await context.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text="⚠️ Execution failed. Check logs.",
            reply_markup=_get_main_keyboard(),
        )


async def handle_pass_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle PASS button press."""
    if not _validate_chat(update):
        return

    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    signal_id = int(parts[1])

    current_caption = query.message.caption
    new_caption = current_caption + "\n\n❌ Passed"

    await query.edit_message_caption(caption=new_caption, reply_markup=None)

    conn = context.application.bot_data.get("db_conn")
    if conn:
        conn.execute("UPDATE signals SET status = 'passed' WHERE id = ?", (signal_id,))
        conn.commit()

    if signal_id in _selected_sizes:
        del _selected_sizes[signal_id]

    logger.info(f"Passed on signal {signal_id}")


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    if not _validate_chat(update):
        return

    message = (
        "🤖 *SpillTheBeans Trading Bot*\n\n"
        "This bot sends trading signals from the Synth API and executes them on Hyperliquid.\n\n"
        "*Commands:*\n"
        "/start - Show this message\n"
        "/status - View open positions\n"
        "/history - View last 10 signals\n"
        "/balance - View account balance\n"
        "/scan - Trigger immediate signal scan\n"
        "/test_signal [BTC|ETH|SOL] - Send test signal for asset (default: BTC)\n\n"
        "*How it works:*\n"
        "1. Bot analyzes Synth API predictions\n"
        "2. Sends signal alerts with charts\n"
        "3. You choose position size and execute\n"
        "4. Bot manages positions on Hyperliquid"
    )

    await update.message.reply_text(
        message, parse_mode="Markdown", reply_markup=_get_main_keyboard()
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /status command - show open positions."""
    if not _validate_chat(update):
        return

    conn = context.application.bot_data.get("db_conn")
    if not conn:
        await update.message.reply_text(
            "⚠️ Database error. Check logs.", reply_markup=_get_main_keyboard()
        )
        return

    hl_client = context.application.bot_data.get("hl_client")
    if not hl_client:
        await update.message.reply_text(
            "⚠️ Hyperliquid client not initialized.", reply_markup=_get_main_keyboard()
        )
        return

    positions = get_open_positions(conn)

    if not positions:
        await update.message.reply_text(
            "📊 No open positions.\nWatching BTC, ETH, SOL for signals...",
            reply_markup=_get_main_keyboard(),
        )
        return

    message = f"📊 Open Positions: {len(positions)}\n\n"

    for pos in positions:
        asset = pos["asset"]
        direction = pos["direction"]
        entry = float(pos["entry_price"])
        size_usd = float(pos["size_usd"])

        current_price = await asyncio.to_thread(hl_client.get_mid_price, asset)
        pnl_usd, pnl_pct = calculate_pnl(direction, size_usd, entry, current_price)

        opened_at = datetime.fromisoformat(pos["opened_at"])
        duration_min = int((datetime.utcnow() - opened_at).total_seconds() / 60)

        is_profit = pnl_usd >= 0
        pnl_sign = "+" if is_profit else ""
        emoji = "✅" if is_profit else "❌"

        message += (
            f"*{asset} {direction.upper()}*\n"
            f"Entry: ${entry:,.2f} → Now: ${current_price:,.2f}\n"
            f"Unrealized P&L: {pnl_sign}${abs(pnl_usd):.2f} ({pnl_sign}{pnl_pct * 100:.2f}%) {emoji}\n"
            f"Duration: {duration_min} minutes\n\n"
        )

    await update.message.reply_text(
        message, parse_mode="Markdown", reply_markup=_get_main_keyboard()
    )


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /history command - show last 10 closed positions."""
    if not _validate_chat(update):
        return

    conn = context.application.bot_data.get("db_conn")
    if not conn:
        await update.message.reply_text(
            "⚠️ Database error. Check logs.", reply_markup=_get_main_keyboard()
        )
        return

    positions = get_closed_positions(conn, limit=10)
    stats = get_position_stats(conn)

    if not positions:
        await update.message.reply_text(
            "📜 No closed positions yet.", reply_markup=_get_main_keyboard()
        )
        return

    message = f"📜 Recent Closed Positions ({len(positions)})\n\n"

    for pos in positions:
        asset = pos["asset"]
        direction = pos["direction"]
        pnl = float(pos["pnl"]) if pos["pnl"] else 0.0

        opened_at = datetime.fromisoformat(pos["opened_at"])
        closed_at = datetime.fromisoformat(pos["closed_at"])
        duration_min = int((closed_at - opened_at).total_seconds() / 60)

        is_profit = pnl >= 0
        pnl_sign = "+" if is_profit else ""
        emoji = "✅" if is_profit else "❌"

        entry = float(pos["entry_price"])
        exit_price = float(pos["exit_price"]) if pos["exit_price"] else entry
        pnl_pct = (
            ((exit_price - entry) / entry * 100)
            if direction == "long"
            else ((entry - exit_price) / entry * 100)
        )

        message += (
            f"{emoji} {asset} {direction.upper()} — {pnl_sign}${abs(pnl):.2f} ({pnl_sign}{pnl_pct:.2f}%)\n"
            f"Duration: {duration_min} minutes\n\n"
        )

    total_pnl = stats["total_pnl"]
    win_count = stats["win_count"]
    total_count = stats["total_count"]
    avg_duration = stats["avg_duration_min"]

    total_sign = "+" if total_pnl >= 0 else ""
    total_emoji = "✅" if total_pnl >= 0 else "❌"
    win_rate = (win_count / total_count * 100) if total_count > 0 else 0

    message += (
        f"📊 *Overall Stats:*\n"
        f"Total P&L: {total_sign}${abs(total_pnl):.2f} {total_emoji}\n"
        f"Win Rate: {win_count}/{total_count} ({win_rate:.0f}%)\n"
        f"Avg Duration: {avg_duration} minutes"
    )

    await update.message.reply_text(
        message, parse_mode="Markdown", reply_markup=_get_main_keyboard()
    )


async def test_signal_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /test_signal command - send test signal using real Synth data."""
    if not _validate_chat(update):
        return

    conn = context.application.bot_data.get("db_conn")
    if not conn:
        await update.message.reply_text(
            "⚠️ Database error. Check logs.", reply_markup=_get_main_keyboard()
        )
        return

    synth_client = context.application.bot_data.get("synth_client")
    if not synth_client:
        await update.message.reply_text(
            "⚠️ Synth client not initialized.", reply_markup=_get_main_keyboard()
        )
        return

    allowed_assets = ["BTC", "ETH", "SOL"]
    asset = "BTC"
    if context.args and len(context.args) > 0:
        arg = context.args[0].upper()
        if arg in allowed_assets:
            asset = arg
        else:
            await update.message.reply_text(
                f"⚠️ Invalid asset. Allowed: {', '.join(allowed_assets)}",
                reply_markup=_get_main_keyboard(),
            )
            return

    try:
        percentile_data = await synth_client.get_prediction_percentiles(asset, "1h")
    except Exception as e:
        logger.error(f"Failed to fetch Synth data: {e}", exc_info=True)
        await update.message.reply_text(
            "⚠️ Failed to fetch Synth data.", reply_markup=_get_main_keyboard()
        )
        return

    try:
        signal = evaluate_test_signal(asset, percentile_data)
    except ValueError as e:
        logger.error(f"Failed to evaluate test signal: {e}", exc_info=True)
        await update.message.reply_text(
            f"⚠️ Failed to evaluate signal: {e}", reply_markup=_get_main_keyboard()
        )
        return

    signal_id = save_signal(conn, signal)
    signal.id = signal_id

    p05 = signal.percentiles_snapshot.get("0.05", signal.stop_loss)
    p95 = signal.percentiles_snapshot.get("0.95", signal.take_profit)
    percentile_band = (p05, p95)

    candles = fetch_candles(asset, num_candles=60)
    chart_image = render_signal_chart(
        candle_data=candles,
        signal=signal,
        percentile_band=percentile_band,
        asset=asset,
    )

    logger.info(
        f"Test signal created: {asset} {signal.direction.upper()}, signal_id={signal_id}"
    )

    await send_signal_alert(
        signal, chart_image, default_size=100.0, application=context.application
    )


async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /balance command - show account balance."""
    if not _validate_chat(update):
        return

    hl_client = context.application.bot_data.get("hl_client")
    if not hl_client:
        await update.message.reply_text(
            "⚠️ Hyperliquid client not initialized.", reply_markup=_get_main_keyboard()
        )
        return

    try:
        user_state = await asyncio.to_thread(
            hl_client.info.user_state, hl_client.wallet_address
        )
        margin = user_state.get("marginSummary", {})

        account_value = float(margin.get("accountValue", 0))
        margin_used = float(margin.get("totalMarginUsed", 0))
        withdrawable = float(margin.get("withdrawable", 0))

        message = (
            f"💰 Account Balance\n"
            f"Total Value: ${account_value:,.2f}\n"
            f"Margin Used: ${margin_used:,.2f}\n"
            f"Available: ${withdrawable:,.2f}"
        )

        await update.message.reply_text(message, reply_markup=_get_main_keyboard())
        logger.info(f"Sent balance info: account_value=${account_value:.2f}")

    except Exception as e:
        logger.error(f"Failed to fetch balance: {e}", exc_info=True)
        await update.message.reply_text(
            "⚠️ Failed to fetch balance. Check logs.", reply_markup=_get_main_keyboard()
        )


async def scan_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /scan command - trigger immediate signal scan."""
    global _scan_cooldown_until

    if not _validate_chat(update):
        return

    now = time.time()
    if now < _scan_cooldown_until:
        remaining = int(_scan_cooldown_until - now)
        await update.message.reply_text(
            f"⏳ Scan on cooldown. Try again in {remaining} seconds.",
            reply_markup=_get_main_keyboard(),
        )
        return

    conn = context.application.bot_data.get("db_conn")
    if not conn:
        await update.message.reply_text(
            "⚠️ Database error. Check logs.", reply_markup=_get_main_keyboard()
        )
        return

    synth_client = context.application.bot_data.get("synth_client")
    if not synth_client:
        await update.message.reply_text(
            "⚠️ Synth client not initialized.", reply_markup=_get_main_keyboard()
        )
        return

    _scan_cooldown_until = now + 60

    assets = ["BTC", "ETH", "SOL"]
    signals_found = []

    await update.message.reply_text(
        "⚡ Scanning BTC, ETH, SOL for signals...", reply_markup=_get_main_keyboard()
    )

    for asset in assets:
        try:
            percentile_data = await synth_client.get_prediction_percentiles(asset, "1h")
            signal = evaluate_signal(asset, percentile_data)

            if signal:
                signal_id = save_signal(conn, signal, status="pending")
                signal.id = signal_id

                p05 = signal.percentiles_snapshot.get("0.05", signal.stop_loss)
                p95 = signal.percentiles_snapshot.get("0.95", signal.take_profit)
                percentile_band = (p05, p95)

                candles = fetch_candles(asset, num_candles=60)
                chart_image = render_signal_chart(
                    candle_data=candles,
                    signal=signal,
                    percentile_band=percentile_band,
                    asset=asset,
                )

                await send_signal_alert(
                    signal,
                    chart_image,
                    default_size=100.0,
                    application=context.application,
                )
                signals_found.append(signal)
                logger.info(f"Scan found signal: {asset} {signal.direction.upper()}")

        except Exception as e:
            logger.error(f"Error scanning {asset}: {e}", exc_info=True)

    if not signals_found:
        await context.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text="No signals right now. Watching BTC, ETH, SOL...",
            reply_markup=_get_main_keyboard(),
        )


async def handle_button_text(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle persistent keyboard button presses."""
    if not _validate_chat(update):
        return

    text = update.message.text

    if text == "📊 Status":
        await status_command(update, context)
    elif text == "📜 History":
        await history_command(update, context)
    elif text == "💰 Balance":
        await balance_command(update, context)
    elif text == "⚡ Scan Now":
        await scan_command(update, context)


def create_bot(db_conn=None, hl_client=None, synth_client=None) -> Application:
    """Create and configure Telegram bot application."""
    if not TELEGRAM_BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN not configured")

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    if db_conn:
        application.bot_data["db_conn"] = db_conn

    if hl_client:
        application.bot_data["hl_client"] = hl_client

    if synth_client:
        application.bot_data["synth_client"] = synth_client

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("history", history_command))
    application.add_handler(CommandHandler("balance", balance_command))
    application.add_handler(CommandHandler("scan", scan_command))
    application.add_handler(CommandHandler("test_signal", test_signal_command))

    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_button_text)
    )

    application.add_handler(
        CallbackQueryHandler(handle_size_callback, pattern=r"^size:")
    )
    application.add_handler(
        CallbackQueryHandler(handle_execute_callback, pattern=r"^execute:")
    )
    application.add_handler(
        CallbackQueryHandler(handle_pass_callback, pattern=r"^pass:")
    )

    logger.info("Telegram bot application created")
    return application


def run_bot(db_path: str = "data/trading.db", hl_client=None) -> None:
    """Initialize and run the bot."""
    conn = init_db(db_path)
    application = create_bot(db_conn=conn, hl_client=hl_client)

    logger.info("Starting Telegram bot...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    run_bot()
