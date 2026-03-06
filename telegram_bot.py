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
        [["📊 Status", "📜 History"], ["💰 Balance", "⚡ Scan Now"], ["⚙️ Settings"]],
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

    settings = context.application.bot_data.get("settings_manager")
    if settings:
        assets = settings.get_active_assets()
        long_pct, short_pct = settings.get_percentiles()
    else:
        assets = ["BTC", "ETH", "SOL"]
        long_pct, short_pct = "0.35", "0.65"

    signals_found = []

    await update.message.reply_text(
        f"⚡ Scanning {', '.join(assets)} for signals...",
        reply_markup=_get_main_keyboard(),
    )

    for asset in assets:
        try:
            percentile_data = await synth_client.get_prediction_percentiles(asset, "1h")
            signal = evaluate_signal(
                asset, percentile_data, long_pct=long_pct, short_pct=short_pct
            )

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
            text=f"No signals right now. Watching {', '.join(assets)}...",
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
    elif text == "⚙️ Settings":
        await settings_command(update, context)


def _build_settings_keyboard(settings) -> InlineKeyboardMarkup:
    """Build the main settings menu keyboard."""
    auto_scan = "ON" if settings.get("auto_scan") else "OFF"
    auto_scan_icon = "📡" if settings.get("auto_scan") else "📵"

    assets = settings.get_active_assets()
    all_assets = ["BTC", "ETH", "SOL"]
    asset_str = ", ".join(a for a in all_assets if a in assets)

    risk_preset = settings.get("risk_preset")
    risk_icon = {
        "conservative": "🔒",
        "moderate": "⚖️",
        "aggressive": "🔥",
        "custom": "🎯",
    }.get(risk_preset, "🎯")
    long_pct, short_pct = settings.get_percentiles()
    risk_str = f"{risk_icon} {risk_preset.capitalize()} (p{long_pct.replace('0.', '')}/p{short_pct.replace('0.', '')})"

    poll_override = settings.get("poll_interval_override")
    if poll_override > 0:
        interval_str = f"{poll_override // 60}m {poll_override % 60}s (manual)"
    else:
        optimal = settings.get_optimal_poll_interval()
        interval_str = f"{optimal // 60}m {optimal % 60}s (auto)"

    budget = settings.get_budget_summary()
    reset_day = settings.get("synth_cycle_reset_day")
    credits_remaining = budget["credits_total"] - budget["credits_used"]
    budget_str = f"resets day {reset_day} | {credits_remaining:,}/{budget['credits_total']:,} remaining"

    keyboard = [
        [
            InlineKeyboardButton(
                "📡 Toggle Auto-Scan", callback_data="settings:autoscan"
            )
        ],
        [InlineKeyboardButton("🪙 Select Assets", callback_data="settings:assets")],
        [InlineKeyboardButton("🎯 Risk Tolerance", callback_data="settings:risk")],
        [InlineKeyboardButton("⏱ Poll Interval", callback_data="settings:poll")],
        [InlineKeyboardButton("📅 Synth Budget", callback_data="settings:budget")],
        [InlineKeyboardButton("❌ Close", callback_data="settings:close")],
    ]

    message_text = (
        f"⚙️ Settings\n\n"
        f"{auto_scan_icon} Auto-Scan: {auto_scan}\n"
        f"🪙 Assets: {asset_str}\n"
        f"🎯 Risk: {risk_str}\n"
        f"⏱ Poll Interval: {interval_str}\n"
        f"📅 Synth Cycle: {budget_str}"
    )

    return message_text, InlineKeyboardMarkup(keyboard)


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle ⚙️ Settings button press."""
    if not _validate_chat(update):
        return

    settings = context.application.bot_data.get("settings_manager")
    if not settings:
        await update.message.reply_text(
            "⚠️ Settings not initialized.", reply_markup=_get_main_keyboard()
        )
        return

    message_text, keyboard = _build_settings_keyboard(settings)

    if update.callback_query:
        await update.callback_query.edit_message_text(
            text=message_text, reply_markup=keyboard
        )
    else:
        await update.message.reply_text(text=message_text, reply_markup=keyboard)


async def handle_settings_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle settings callback queries."""
    if not _validate_chat(update):
        return

    query = update.callback_query
    await query.answer()

    settings = context.application.bot_data.get("settings_manager")
    if not settings:
        await query.edit_message_text("⚠️ Settings not initialized.")
        return

    parts = query.data.split(":")
    action = parts[1] if len(parts) > 1 else ""

    if action == "close":
        await query.edit_message_text("Settings closed.")
        return

    elif action == "autoscan":
        current = settings.get("auto_scan")
        settings.set("auto_scan", not current)
        logger.info(f"Auto-scan toggled: {not current}")

    elif action == "assets":
        all_assets = ["BTC", "ETH", "SOL"]
        current_assets = settings.get_active_assets()

        keyboard = []
        row = []
        for asset in all_assets:
            icon = "✅" if asset in current_assets else "❌"
            row.append(
                InlineKeyboardButton(
                    f"{asset} {icon}", callback_data=f"settings:asset_toggle:{asset}"
                )
            )
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="settings:back")])

        await query.edit_message_text(
            "🪙 Select Assets (at least 1 required):",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    elif action == "asset_toggle":
        asset = parts[2] if len(parts) > 2 else ""
        current_assets = settings.get_active_assets()

        if asset in current_assets:
            if len(current_assets) > 1:
                current_assets.remove(asset)
        else:
            current_assets.append(asset)

        import json

        settings.set("assets", json.dumps(current_assets))
        logger.info(f"Asset toggled: {asset}, now: {current_assets}")

        all_assets_list = ["BTC", "ETH", "SOL"]
        keyboard = []
        row = []
        for a in all_assets_list:
            icon = "✅" if a in current_assets else "❌"
            row.append(
                InlineKeyboardButton(
                    f"{a} {icon}", callback_data=f"settings:asset_toggle:{a}"
                )
            )
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="settings:back")])

        await query.edit_message_text(
            "🪙 Select Assets (at least 1 required):",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    elif action == "risk":
        current_preset = settings.get("risk_preset")

        keyboard = [
            [
                InlineKeyboardButton(
                    f"🔒 Conservative (p35/p65){' ✓' if current_preset == 'conservative' else ''}",
                    callback_data="settings:risk_preset:conservative",
                )
            ],
            [
                InlineKeyboardButton(
                    f"⚖️ Moderate (p45/p55){' ✓' if current_preset == 'moderate' else ''}",
                    callback_data="settings:risk_preset:moderate",
                )
            ],
            [
                InlineKeyboardButton(
                    f"🔥 Aggressive (p50/p50){' ✓' if current_preset == 'aggressive' else ''}",
                    callback_data="settings:risk_preset:aggressive",
                )
            ],
            [
                InlineKeyboardButton(
                    f"🎯 Custom...{' ✓' if current_preset == 'custom' else ''}",
                    callback_data="settings:risk_custom",
                )
            ],
            [InlineKeyboardButton("⬅️ Back", callback_data="settings:back")],
        ]

        await query.edit_message_text(
            "🎯 Select Risk Tolerance:", reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    elif action == "risk_preset":
        preset = parts[2] if len(parts) > 2 else "conservative"
        long_pct, short_pct = {
            "conservative": ("0.35", "0.65"),
            "moderate": ("0.45", "0.55"),
            "aggressive": ("0.50", "0.50"),
        }.get(preset, ("0.35", "0.65"))

        settings.set("risk_preset", preset)
        settings.set("long_percentile", long_pct)
        settings.set("short_percentile", short_pct)
        logger.info(f"Risk preset set to {preset}: long={long_pct}, short={short_pct}")

    elif action == "risk_custom":
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Enter two percentile values (e.g., 0.40 0.60):\n\nFirst value is for LONG signals, second for SHORT.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("Cancel", callback_data="settings:back")]]
            ),
        )
        context.user_data["waiting_for_custom_risk"] = True
        return

    elif action == "poll":
        poll_override = settings.get("poll_interval_override")
        optimal = settings.get_optimal_poll_interval()

        daily_budget = settings.get_budget_summary()["daily_budget"]
        assets_count = len(settings.get_active_assets())
        polls_per_day = daily_budget / assets_count if daily_budget > 0 else 0

        keyboard = [
            [
                InlineKeyboardButton(
                    f"Auto (recommended) {'✓' if poll_override == 0 else ''}",
                    callback_data="settings:poll_auto",
                )
            ],
            [
                InlineKeyboardButton(
                    f"Every 2 min {'✓' if poll_override == 120 else ''}",
                    callback_data="settings:poll_manual:120",
                )
            ],
            [
                InlineKeyboardButton(
                    f"Every 3 min {'✓' if poll_override == 180 else ''}",
                    callback_data="settings:poll_manual:180",
                )
            ],
            [
                InlineKeyboardButton(
                    f"Every 5 min {'✓' if poll_override == 300 else ''}",
                    callback_data="settings:poll_manual:300",
                )
            ],
            [
                InlineKeyboardButton(
                    f"Every 10 min {'✓' if poll_override == 600 else ''}",
                    callback_data="settings:poll_manual:600",
                )
            ],
            [InlineKeyboardButton("⬅️ Back", callback_data="settings:back")],
        ]

        message = (
            f"⏱ Poll Interval\n"
            f"Current: {poll_override // 60}m {poll_override % 60}s "
            f"({'auto-calculated' if poll_override == 0 else 'manual'})\n"
            f"Based on: {daily_budget:.0f} daily budget ÷ {assets_count} assets = {polls_per_day:.0f} polls/day"
        )

        await query.edit_message_text(
            message, reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    elif action == "poll_auto":
        settings.set("poll_interval_override", 0)
        logger.info("Poll interval set to auto")

    elif action == "poll_manual":
        interval = int(parts[2]) if len(parts) > 2 else 300
        settings.set("poll_interval_override", interval)
        logger.info(f"Poll interval set to {interval}s")

    elif action == "budget":
        budget = settings.get_budget_summary()
        reset_day = settings.get("synth_cycle_reset_day")

        will_exceed = (
            "✅ within budget" if not budget["will_exceed"] else "⚠️ will exceed"
        )

        keyboard = [
            [
                InlineKeyboardButton(
                    "Edit Total Credits", callback_data="settings:budget_edit_total"
                )
            ],
            [
                InlineKeyboardButton(
                    "Edit Reset Day", callback_data="settings:budget_edit_day"
                )
            ],
            [
                InlineKeyboardButton(
                    "Reset Used Credits", callback_data="settings:budget_reset_used"
                )
            ],
            [InlineKeyboardButton("⬅️ Back", callback_data="settings:back")],
        ]

        message = (
            f"📅 Synth API Budget\n\n"
            f"Total credits: {budget['credits_total']:,}\n"
            f"Used this cycle: {budget['credits_used']:,}\n"
            f"Remaining: {budget['credits_total'] - budget['credits_used']:,}\n"
            f"Cycle resets: day {reset_day} of each month\n"
            f"Daily budget: ~{budget['daily_budget']:.0f}/day\n"
            f"Projected: {budget['projected_total_usage']:.0f} total ({will_exceed})"
        )

        await query.edit_message_text(
            message, reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    elif action == "budget_edit_total":
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Enter new total credit limit (e.g., 20000):",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("Cancel", callback_data="settings:back")]]
            ),
        )
        context.user_data["waiting_for_budget_total"] = True
        return

    elif action == "budget_edit_day":
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Enter day of month for cycle reset (1-28):",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("Cancel", callback_data="settings:back")]]
            ),
        )
        context.user_data["waiting_for_reset_day"] = True
        return

    elif action == "budget_reset_used":
        settings.set("synth_credits_used", 0)
        settings.set("_last_credits_reset", datetime.utcnow().strftime("%Y-%m-%d"))
        logger.info("Credits used reset to 0")
        budget = settings.get_budget_summary()
        await query.answer("Credits reset to 0", show_alert=True)

    elif action == "back":
        message_text, keyboard = _build_settings_keyboard(settings)
        await query.edit_message_text(text=message_text, reply_markup=keyboard)
        return

    message_text, keyboard = _build_settings_keyboard(settings)
    await query.edit_message_text(text=message_text, reply_markup=keyboard)


async def handle_settings_text_input(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle text input for custom settings."""
    settings = context.application.bot_data.get("settings_manager")
    if not settings:
        await handle_button_text(update, context)
        return

    if not _validate_chat(update):
        return

    text = update.message.text

    if context.user_data.get("waiting_for_custom_risk"):
        context.user_data.pop("waiting_for_custom_risk", None)
        parts = text.split()
        if len(parts) >= 2:
            try:
                long_pct = str(float(parts[0]))
                short_pct = str(float(parts[1]))
                settings.set("risk_preset", "custom")
                settings.set("long_percentile", long_pct)
                settings.set("short_percentile", short_pct)
                logger.info(f"Custom risk set: long={long_pct}, short={short_pct}")
                await update.message.reply_text(
                    f"✅ Custom percentiles set: LONG p{long_pct}, SHORT p{short_pct}",
                    reply_markup=_get_main_keyboard(),
                )
            except ValueError:
                await update.message.reply_text(
                    "⚠️ Invalid values. Use format: 0.40 0.60",
                    reply_markup=_get_main_keyboard(),
                )
        else:
            await update.message.reply_text(
                "⚠️ Enter two numbers (e.g., 0.40 0.60)",
                reply_markup=_get_main_keyboard(),
            )
        return

    if context.user_data.get("waiting_for_budget_total"):
        context.user_data.pop("waiting_for_budget_total", None)
        try:
            total = int(text)
            if total > 0:
                settings.set("synth_credits_total", total)
                logger.info(f"Budget total set to {total}")
                await update.message.reply_text(
                    f"✅ Total credits set to {total:,}",
                    reply_markup=_get_main_keyboard(),
                )
            else:
                await update.message.reply_text(
                    "⚠️ Must be a positive number.", reply_markup=_get_main_keyboard()
                )
        except ValueError:
            await update.message.reply_text(
                "⚠️ Enter a valid number.", reply_markup=_get_main_keyboard()
            )
        return

    if context.user_data.get("waiting_for_reset_day"):
        context.user_data.pop("waiting_for_reset_day", None)
        try:
            day = int(text)
            if 1 <= day <= 28:
                settings.set("synth_cycle_reset_day", day)
                logger.info(f"Reset day set to {day}")
                await update.message.reply_text(
                    f"✅ Cycle reset day set to {day}",
                    reply_markup=_get_main_keyboard(),
                )
            else:
                await update.message.reply_text(
                    "⚠️ Enter a day between 1 and 28.", reply_markup=_get_main_keyboard()
                )
        except ValueError:
            await update.message.reply_text(
                "⚠️ Enter a valid number.", reply_markup=_get_main_keyboard()
            )
        return

    # No pending input, fall through to regular button text handler
    await handle_button_text(update, context)


def create_bot(
    db_conn=None, hl_client=None, synth_client=None, settings_manager=None
) -> Application:
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

    if settings_manager:
        application.bot_data["settings_manager"] = settings_manager

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("history", history_command))
    application.add_handler(CommandHandler("balance", balance_command))
    application.add_handler(CommandHandler("scan", scan_command))
    application.add_handler(CommandHandler("test_signal", test_signal_command))

    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_settings_text_input)
    )

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

    application.add_handler(
        CallbackQueryHandler(handle_settings_callback, pattern=r"^settings:")
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
