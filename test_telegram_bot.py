"""Test Telegram bot functionality."""

import asyncio
import io
from datetime import datetime

from telegram_bot import (
    create_bot,
    send_signal_alert,
    send_pnl_update,
    send_close_summary,
)
from strategy import Signal
from db import init_db
from chart_renderer import render_signal_chart, fetch_candles, render_pnl_summary


async def test_signal_alert():
    """Test sending a signal alert."""
    conn = init_db("data/test_trading.db")
    bot_app = create_bot(db_conn=conn)
    await bot_app.initialize()

    signal = Signal(
        asset="BTC",
        direction="long",
        entry_price=89000.0,
        take_profit=90500.0,
        stop_loss=88000.0,
        win_rate=0.71,
        vol_spread_pct=2.5,
        timestamp=datetime.utcnow(),
        percentiles_snapshot={},
        id=1,
    )

    candles = fetch_candles("BTC", num_candles=60, base_price=signal.entry_price)
    chart_buf = render_signal_chart(
        candles, signal, (signal.stop_loss, signal.take_profit), "BTC"
    )

    print("Sending signal alert...")
    await send_signal_alert(signal, chart_buf, default_size=100.0, application=bot_app)
    print("Signal alert sent!")

    await bot_app.shutdown()


async def test_pnl_update():
    """Test sending P&L update."""
    conn = init_db("data/test_trading.db")
    bot_app = create_bot(db_conn=conn)
    await bot_app.initialize()

    print("Sending P&L update...")
    await send_pnl_update(
        asset="BTC",
        direction="long",
        entry=89000.0,
        current_price=89500.0,
        unrealized_pnl=50.0,
        duration_min=45,
        application=bot_app,
    )
    print("P&L update sent!")

    await bot_app.shutdown()


async def test_close_summary():
    """Test sending close summary."""
    conn = init_db("data/test_trading.db")
    bot_app = create_bot(db_conn=conn)
    await bot_app.initialize()

    pnl_chart = render_pnl_summary(
        asset="BTC",
        direction="long",
        entry=89000.0,
        exit_price=90500.0,
        pnl_usd=150.0,
        pnl_pct=1.69,
        duration_min=120,
    )

    print("Sending close summary...")
    await send_close_summary(
        pnl_chart=pnl_chart,
        asset="BTC",
        direction="long",
        entry=89000.0,
        exit_price=90500.0,
        pnl_usd=150.0,
        pnl_pct=1.69,
        duration_min=120,
        application=bot_app,
    )
    print("Close summary sent!")

    await bot_app.shutdown()


async def test_commands():
    """Test command handlers."""
    conn = init_db("data/test_trading.db")
    bot_app = create_bot(db_conn=conn)

    print("Bot created successfully!")
    print("Available commands:")
    print("  /start - Show bot info")
    print("  /status - Show open positions")
    print("  /history - Show recent signals")
    print("\nRun the bot with: python telegram_bot.py")


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python test_telegram_bot.py [signal|pnl|close|commands]")
        sys.exit(1)

    test_type = sys.argv[1]

    if test_type == "signal":
        asyncio.run(test_signal_alert())
    elif test_type == "pnl":
        asyncio.run(test_pnl_update())
    elif test_type == "close":
        asyncio.run(test_close_summary())
    elif test_type == "commands":
        asyncio.run(test_commands())
    else:
        print(f"Unknown test type: {test_type}")
        print("Available: signal, pnl, close, commands")
        sys.exit(1)
