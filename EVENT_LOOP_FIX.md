# Event Loop Issue Fix

## Problem
The bot crashed with:
```
RuntimeError: This event loop is already running
RuntimeError: Cannot close a running event loop
```

## Root Cause
In python-telegram-bot v20+, `application.run_polling()` manages its own event loop internally. Wrapping it with `asyncio.run()` creates a nested event loop conflict.

## Solution

### Before (WRONG):
```python
async def run_bot(db_path: str = "data/trading.db") -> None:
    """Initialize and run the bot."""
    conn = init_db(db_path)
    application = create_bot(db_conn=conn)
    
    logger.info("Starting Telegram bot...")
    await application.run_polling(allowed_updates=Update.ALL_TYPES)  # ❌ Don't await


if __name__ == "__main__":
    import asyncio
    asyncio.run(run_bot())  # ❌ Don't use asyncio.run()
```

### After (CORRECT):
```python
def run_bot(db_path: str = "data/trading.db") -> None:
    """Initialize and run the bot."""
    conn = init_db(db_path)
    application = create_bot(db_conn=conn)
    
    logger.info("Starting Telegram bot...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)  # ✅ Call synchronously


if __name__ == "__main__":
    run_bot()  # ✅ Call directly
```

## Key Changes
1. Removed `async` keyword from `run_bot()` function
2. Removed `await` from `application.run_polling()`
3. Removed `asyncio.run()` wrapper in `__main__`
4. Call `run_bot()` directly

## Status
✅ Fixed and verified

The bot should now start successfully with:
```bash
python telegram_bot.py
```
