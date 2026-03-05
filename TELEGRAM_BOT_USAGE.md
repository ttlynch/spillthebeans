# Telegram Bot Usage Guide

## Setup

### 1. Install Dependencies

```bash
pip install python-telegram-bot>=20.0
```

### 2. Configure Environment Variables

Add to your `.env` file:

```bash
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
```

To get your bot token:
1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. Create a new bot with `/newbot`
3. Copy the API token

To get your chat ID:
1. Start a chat with your bot
2. Visit `https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates`
3. Look for `"chat":{"id":YOUR_CHAT_ID}`

### 3. Run the Bot

```bash
python telegram_bot.py
```

The bot will start polling for updates and respond to commands.

## Usage in Your Trading System

### Initialize the Bot

```python
from telegram_bot import create_bot
from db import init_db

# Initialize database and bot
conn = init_db("data/trading.db")
bot_app = create_bot(db_conn=conn)
await bot_app.initialize()
```

### Send Signal Alert

```python
from telegram_bot import send_signal_alert
from strategy import Signal
from chart_renderer import render_signal_chart, fetch_candles
from db import save_signal

# Generate signal
signal = Signal(
    asset="BTC",
    direction="long",
    entry_price=89000.0,
    take_profit=90500.0,
    stop_loss=88000.0,
    win_rate=0.71,
    vol_spread_pct=2.5,
    timestamp=datetime.utcnow(),
    percentiles_snapshot={...}
)

# Save to database and get ID
signal_id = save_signal(conn, signal)
signal.id = signal_id

# Generate chart
candles = fetch_candles(signal.asset, num_candles=60, base_price=signal.entry_price)
p05 = signal.percentiles_snapshot.get("0.05")
p95 = signal.percentiles_snapshot.get("0.95")
chart_buf = render_signal_chart(candles, signal, (p05, p95), signal.asset)

# Send alert
await send_signal_alert(signal, chart_buf, default_size=100.0, application=bot_app)
```

### Send P&L Update

```python
from telegram_bot import send_pnl_update

# During position monitoring
await send_pnl_update(
    asset="BTC",
    direction="long",
    entry=89000.0,
    current_price=89500.0,
    unrealized_pnl=50.0,
    duration_min=45,
    application=bot_app
)
```

### Send Close Summary

```python
from telegram_bot import send_close_summary
from chart_renderer import render_pnl_summary
from datetime import datetime

# When closing position
opened_at = datetime.fromisoformat(position['opened_at'])
duration_min = (datetime.utcnow() - opened_at).total_seconds() / 60

pnl_chart = render_pnl_summary(
    asset="BTC",
    direction="long",
    entry=89000.0,
    exit_price=90500.0,
    pnl_usd=150.0,
    pnl_pct=1.69,
    duration_min=int(duration_min)
)

await send_close_summary(
    pnl_chart=pnl_chart,
    asset="BTC",
    direction="long",
    entry=89000.0,
    exit_price=90500.0,
    pnl_usd=150.0,
    pnl_pct=1.69,
    duration_min=int(duration_min),
    application=bot_app
)
```

## Bot Commands

### `/start`

Shows bot introduction and available commands.

### `/status`

Shows all currently open positions with:
- Asset and direction
- Entry price
- Position size
- Duration

### `/history`

Shows last 10 signals with:
- Status (⏳ pending, ✅ executed, ❌ passed)
- Asset and direction
- Entry price
- Timestamp

## Interactive Features

### Size Selection

When a signal alert is sent, users can:
1. Tap size buttons: [$50] [$100] [$250] [$500]
2. Caption updates with recalculated expected profit/max loss
3. EXECUTE button shows selected amount

### Execute

When user taps "✅ EXECUTE $X":
1. Keyboard shows "⏳ Executing..."
2. Bot logs execution request
3. Signal status updates to "executed" in database
4. Success message sent to chat

### Pass

When user taps "❌ Pass":
1. "❌ Passed" appended to caption
2. Keyboard removed
3. Signal status updates to "passed" in database

## Architecture

### State Management

- **Size selection**: In-memory dictionary `_selected_sizes: Dict[int, float]`
- **Signal status**: SQLite database (`pending`, `executed`, `passed`)
- **Position tracking**: SQLite database with `opened_at` timestamp

### Validation

- Chat ID validation on every handler
- Silently ignores messages from unauthorized chats
- No rejection messages sent

### Error Handling

- Full stack traces logged to `spillthebeans.log`
- User-friendly error messages sent to Telegram
- Database errors handled gracefully

## Testing

### Test Signal Alert

```bash
python test_telegram_bot.py signal
```

### Test P&L Update

```bash
python test_telegram_bot.py pnl
```

### Test Close Summary

```bash
python test_telegram_bot.py close
```

### Test Commands

```bash
python test_telegram_bot.py commands
```

## Integration with Main Trading Loop

```python
import asyncio
from telegram_bot import create_bot, send_signal_alert
from db import init_db, save_signal
from synth_client import get_prediction_percentiles
from strategy import evaluate_signal

async def main():
    conn = init_db()
    bot_app = create_bot(db_conn=conn)
    await bot_app.initialize()
    
    while True:
        for asset in ["BTC", "ETH", "SOL"]:
            try:
                data = await get_prediction_percentiles(asset)
                signal = evaluate_signal(asset, data)
                
                if signal:
                    signal_id = save_signal(conn, signal)
                    signal.id = signal_id
                    
                    candles = fetch_candles(asset, 60, signal.entry_price)
                    p05 = signal.percentiles_snapshot.get("0.05")
                    p95 = signal.percentiles_snapshot.get("0.95")
                    chart = render_signal_chart(candles, signal, (p05, p95), asset)
                    
                    await send_signal_alert(signal, chart, application=bot_app)
                    
            except Exception as e:
                logger.error(f"Error processing {asset}: {e}", exc_info=True)
        
        await asyncio.sleep(300)  # Check every 5 minutes

if __name__ == "__main__":
    asyncio.run(main())
```

## Phase 5: Hyperliquid Integration

In Phase 5, update `handle_execute_callback()` to:

```python
from hl_client import place_market_order

try:
    order_result = place_market_order(
        asset=signal_row['asset'],
        direction=signal_row['direction'],
        size_usd=size
    )
    
    if order_result['status'] == 'ok':
        save_position(
            conn, signal_id, 
            signal_row['asset'],
            signal_row['direction'],
            size,
            order_result['entry_price']
        )
        
        await context.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=f"✅ {signal_row['asset']} {signal_row['direction'].upper()} opened on Hyperliquid\n"
                 f"Entry: ${order_result['entry_price']:,.2f}\n"
                 f"Size: ${size:.0f}"
        )
    else:
        raise Exception(order_result.get('error', 'Unknown error'))
        
except Exception as e:
    logger.error(f"Failed to execute: {e}", exc_info=True)
    await context.bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=f"⚠️ Execution failed: {str(e)}"
    )
```
