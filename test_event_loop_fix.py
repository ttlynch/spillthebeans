#!/usr/bin/env python3
"""Quick test to verify event loop fix."""

import sys

print("Testing telegram_bot.py imports...")
try:
    from telegram_bot import create_bot, run_bot
    print("✓ Imports successful")
except ImportError as e:
    print(f"✗ Import error: {e}")
    print("\nPlease install: pip install python-telegram-bot>=20.0")
    sys.exit(1)

print("\nChecking run_bot function...")
import inspect
sig = inspect.signature(run_bot)
is_async = inspect.iscoroutinefunction(run_bot)

if is_async:
    print("✗ ERROR: run_bot is still async!")
    sys.exit(1)
else:
    print("✓ run_bot is synchronous (correct)")

print("\n✅ Event loop fix verified!")
print("\nYou can now run: python telegram_bot.py")
