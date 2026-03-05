# Telegram Bot Setup Checklist

## Prerequisites
- [x] Python 3.9+ installed
- [x] Existing codebase structure in place
- [x] Database schema supports signal status tracking

## Implementation Status
- [x] `telegram_bot.py` created (371 lines)
- [x] `config.py` updated with TELEGRAM vars
- [x] `db.py` updated with `update_signal_status()`
- [x] `strategy.py` updated with optional `id` field
- [x] `test_telegram_bot.py` created
- [x] Documentation created (USAGE.md, IMPLEMENTATION.md)
- [x] All syntax validated

## Setup Steps

### 1. Install Dependencies
```bash
pip install python-telegram-bot>=20.0
```

### 2. Create Telegram Bot
1. Open Telegram and search for [@BotFather](https://t.me/BotFather)
2. Send `/newbot` command
3. Follow prompts to name your bot
4. Copy the API token (looks like: `1234567890:ABCdefGHIjklMNOpqrsTUVwxyz`)

### 3. Get Your Chat ID
1. Start a conversation with your new bot
2. Send any message to it
3. Visit this URL in your browser (replace TOKEN):
   ```
   https://api.telegram.org/bot<TOKEN>/getUpdates
   ```
4. Look for: `"chat":{"id":YOUR_CHAT_ID}`
5. Copy the chat ID (usually a number like `123456789`)

### 4. Configure Environment
Add to your `.env` file:
```bash
TELEGRAM_BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz
TELEGRAM_CHAT_ID=123456789
```

### 5. Test the Bot
```bash
# Test syntax and imports
python3 -c "from telegram_bot import create_bot; print('✓ Imports OK')"

# Test individual features (requires valid credentials)
python test_telegram_bot.py commands
```

### 6. Run the Bot
```bash
python telegram_bot.py
```

You should see:
```
2026-03-05 10:30:00 - __main__ - INFO - Database initialized successfully
2026-03-05 10:30:00 - __main__ - INFO - Telegram bot application created
2026-03-05 10:30:00 - __main__ - INFO - Starting Telegram bot...
```

### 7. Test in Telegram
Open your bot in Telegram and send:
- `/start` - Should show bot introduction
- `/status` - Should show "No open positions"
- `/history` - Should show "No signal history"

## Integration with Trading System

### Add to Main Loop
```python
from telegram_bot import create_bot, send_signal_alert

async def main():
    conn = init_db()
    bot_app = create_bot(db_conn=conn)
    await bot_app.initialize()
    
    # Your trading loop here
    # When signal is generated:
    # await send_signal_alert(signal, chart_buf, application=bot_app)
```

## Troubleshooting

### Import Errors
```
ImportError: cannot import name 'telegram'
```
**Solution**: Install dependency
```bash
pip install python-telegram-bot>=20.0
```

### Bot Not Responding
1. Check bot token is correct in `.env`
2. Check chat ID is correct
3. Verify bot is running: `python telegram_bot.py`
4. Check logs in `spillthebeans.log`

### Unauthorized Chat
If you send a command and nothing happens:
- Bot silently ignores messages from other chats
- Verify `TELEGRAM_CHAT_ID` matches your chat

### Database Errors
```
⚠️ Database error. Check logs.
```
Check `spillthebeans.log` for full error details

## Next Phase

After testing, proceed to Phase 5:
- Wire `handle_execute_callback()` to Hyperliquid client
- Replace logging with actual order placement
- Add position monitoring loop
- Send real-time P&L updates

## Files Reference

- **`telegram_bot.py`** - Main bot implementation
- **`test_telegram_bot.py`** - Test suite
- **`TELEGRAM_BOT_USAGE.md`** - Detailed usage guide
- **`TELEGRAM_BOT_IMPLEMENTATION.md`** - Implementation summary
- **`config.py`** - Configuration (updated)
- **`db.py`** - Database helpers (updated)
- **`strategy.py`** - Signal dataclass (updated)
