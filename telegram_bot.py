"""Telegram bot for trading signal alerts and execution."""

import io
import logging
from datetime import datetime
from typing import Dict, Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from db import get_open_positions, get_signal_history, init_db, save_signal
from strategy import Signal, calculate_trade_stats
from chart_renderer import (
    render_pnl_summary,
    render_signal_chart,
    _generate_mock_candles,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("spillthebeans.log"), logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

_selected_sizes: Dict[int, float] = {}


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
        raise


async def send_pnl_update(
    asset: str,
    direction: str,
    entry: float,
    current_price: float,
    unrealized_pnl: float,
    duration_min: int,
    application: Application = None,
) -> None:
    """Send P&L update message."""
    if application is None:
        logger.error("No application instance provided")
        return

    pnl_sign = "+" if unrealized_pnl >= 0 else ""
    message = (
        f"📊 {asset} {direction.upper()} Update\n"
        f"Entry: ${entry:,.2f} → Current: ${current_price:,.2f}\n"
        f"Unrealized P&L: {pnl_sign}${unrealized_pnl:,.2f}\n"
        f"Duration: {int(duration_min)} min"
    )

    try:
        await application.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)
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
    application: Application = None,
) -> None:
    """Send P&L summary chart."""
    if application is None:
        logger.error("No application instance provided")
        return

    pnl_sign = "+" if pnl_usd >= 0 else ""
    caption = (
        f"📈 Position Closed\n"
        f"{asset} {direction.upper()}\n"
        f"Entry: ${entry:,.2f} → Exit: ${exit_price:,.2f}\n"
        f"P&L: {pnl_sign}${abs(pnl_usd):,.2f} ({pnl_sign}{pnl_pct:.2f}%)\n"
        f"Duration: {int(duration_min)} min"
    )

    try:
        await application.bot.send_photo(
            chat_id=TELEGRAM_CHAT_ID, photo=pnl_chart, caption=caption
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
        await query.edit_message_text("⚠️ Database error. Check logs.")
        logger.error("No database connection in bot_data")
        return

    signal_row = conn.execute(
        "SELECT * FROM signals WHERE id = ?", (signal_id,)
    ).fetchone()

    if not signal_row:
        await query.edit_message_text("⚠️ Signal not found.")
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
        await query.edit_message_text("⚠️ Database error. Check logs.")
        logger.error("No database connection in bot_data")
        return

    signal_row = conn.execute(
        "SELECT * FROM signals WHERE id = ?", (signal_id,)
    ).fetchone()

    if not signal_row:
        await query.edit_message_text("⚠️ Signal not found.")
        return

    try:
        logger.info(
            f"EXECUTE REQUEST: Signal {signal_id}, {signal_row['asset']} {signal_row['direction']}, "
            f"Entry ${signal_row['entry']:.2f}, Size ${size:.0f}"
        )

        conn.execute(
            "UPDATE signals SET status = 'executed' WHERE id = ?", (signal_id,)
        )
        conn.commit()

        await context.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=(
                f"✅ {signal_row['asset']} {signal_row['direction'].upper()} opened on Hyperliquid\n"
                f"Entry: ${signal_row['entry']:,.2f}\n"
                f"Size: ${size:.0f}"
            ),
        )

        logger.info(f"Successfully executed signal {signal_id}")

    except Exception as e:
        logger.error(f"Failed to execute signal {signal_id}: {e}", exc_info=True)

        await context.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID, text="⚠️ Execution failed. Check logs."
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
        "/test_signal - Send mock BTC signal for testing\n\n"
        "*How it works:*\n"
        "1. Bot analyzes Synth API predictions\n"
        "2. Sends signal alerts with charts\n"
        "3. You choose position size and execute\n"
        "4. Bot manages positions on Hyperliquid"
    )

    await update.message.reply_text(message, parse_mode="Markdown")


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /status command - show open positions."""
    if not _validate_chat(update):
        return

    conn = context.application.bot_data.get("db_conn")
    if not conn:
        await update.message.reply_text("⚠️ Database error. Check logs.")
        return

    positions = get_open_positions(conn)

    if not positions:
        await update.message.reply_text("No open positions.")
        return

    message = "📊 *Open Positions*\n\n"

    for pos in positions:
        opened_at = datetime.fromisoformat(pos["opened_at"])
        duration_min = (datetime.utcnow() - opened_at).total_seconds() / 60

        message += (
            f"*{pos['asset']} {pos['direction'].upper()}*\n"
            f"Entry: ${pos['entry_price']:,.2f}\n"
            f"Size: ${pos['size_usd']:,.0f}\n"
            f"Duration: {int(duration_min)} min\n\n"
        )

    await update.message.reply_text(message, parse_mode="Markdown")


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /history command - show last 10 signals."""
    if not _validate_chat(update):
        return

    conn = context.application.bot_data.get("db_conn")
    if not conn:
        await update.message.reply_text("⚠️ Database error. Check logs.")
        return

    signals = get_signal_history(conn, limit=10)

    if not signals:
        await update.message.reply_text("No signal history.")
        return

    message = "📜 *Recent Signals*\n\n"

    for sig in signals:
        status_emoji = {"pending": "⏳", "executed": "✅", "passed": "❌"}.get(
            sig["status"], "❓"
        )

        timestamp = datetime.fromisoformat(sig["timestamp"])
        time_str = timestamp.strftime("%m/%d %H:%M")

        message += (
            f"{status_emoji} *{sig['asset']} {sig['direction'].upper()}*\n"
            f"Entry: ${sig['entry']:,.2f} | {sig['status'].capitalize()}\n"
            f"{time_str}\n\n"
        )

    await update.message.reply_text(message, parse_mode="Markdown")


async def test_signal_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /test_signal command - send mock BTC signal for testing."""
    if not _validate_chat(update):
        return

    conn = context.application.bot_data.get("db_conn")
    if not conn:
        await update.message.reply_text("⚠️ Database error. Check logs.")
        return

    signal = Signal(
        asset="BTC",
        direction="long",
        entry_price=87200.00,
        take_profit=87650.00,
        stop_loss=86400.00,
        win_rate=0.71,
        vol_spread_pct=1.8,
        timestamp=datetime.now(),
        percentiles_snapshot={},
    )

    signal_id = save_signal(conn, signal)
    signal.id = signal_id

    candles = _generate_mock_candles(60, base_price=87200.00)
    chart_image = render_signal_chart(
        candle_data=candles,
        signal=signal,
        percentile_band=(86400.0, 88000.0),
        asset="BTC",
    )

    logger.info(f"Test signal created: BTC LONG, signal_id={signal_id}")

    await send_signal_alert(
        signal, chart_image, default_size=100.0, application=context.application
    )


def create_bot(db_conn=None) -> Application:
    """Create and configure Telegram bot application."""
    if not TELEGRAM_BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN not configured")

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    if db_conn:
        application.bot_data["db_conn"] = db_conn

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("history", history_command))
    application.add_handler(CommandHandler("test_signal", test_signal_command))

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


def run_bot(db_path: str = "data/trading.db") -> None:
    """Initialize and run the bot."""
    conn = init_db(db_path)
    application = create_bot(db_conn=conn)

    logger.info("Starting Telegram bot...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    run_bot()
