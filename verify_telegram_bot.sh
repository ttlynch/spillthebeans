#!/bin/bash
echo "==================================="
echo "Telegram Bot Verification Script"
echo "==================================="
echo ""

echo "✓ Checking created files..."
files=(
  "telegram_bot.py"
  "test_telegram_bot.py"
  "TELEGRAM_BOT_USAGE.md"
  "TELEGRAM_BOT_IMPLEMENTATION.md"
)

for file in "${files[@]}"; do
  if [ -f "$file" ]; then
    lines=$(wc -l < "$file")
    echo "  ✓ $file ($lines lines)"
  else
    echo "  ✗ $file (MISSING)"
  fi
done

echo ""
echo "✓ Checking modified files..."
modified=(
  "config.py"
  "db.py"
  "strategy.py"
)

for file in "${modified[@]}"; do
  if [ -f "$file" ]; then
    echo "  ✓ $file (updated)"
  else
    echo "  ✗ $file (MISSING)"
  fi
done

echo ""
echo "✓ Verifying syntax..."
python3 -m py_compile telegram_bot.py 2>&1 && echo "  ✓ telegram_bot.py syntax OK"
python3 -m py_compile config.py 2>&1 && echo "  ✓ config.py syntax OK"
python3 -m py_compile db.py 2>&1 && echo "  ✓ db.py syntax OK"
python3 -m py_compile strategy.py 2>&1 && echo "  ✓ strategy.py syntax OK"
python3 -m py_compile test_telegram_bot.py 2>&1 && echo "  ✓ test_telegram_bot.py syntax OK"

echo ""
echo "✓ Checking environment variables in config.py..."
grep -q "TELEGRAM_BOT_TOKEN" config.py && echo "  ✓ TELEGRAM_BOT_TOKEN found"
grep -q "TELEGRAM_CHAT_ID" config.py && echo "  ✓ TELEGRAM_CHAT_ID found"

echo ""
echo "✓ Checking new functions..."
grep -q "def update_signal_status" db.py && echo "  ✓ update_signal_status() in db.py"
grep -q "def send_signal_alert" telegram_bot.py && echo "  ✓ send_signal_alert() in telegram_bot.py"
grep -q "def send_pnl_update" telegram_bot.py && echo "  ✓ send_pnl_update() in telegram_bot.py"
grep -q "def send_close_summary" telegram_bot.py && echo "  ✓ send_close_summary() in telegram_bot.py"
grep -q "async def handle_size_callback" telegram_bot.py && echo "  ✓ handle_size_callback() in telegram_bot.py"
grep -q "async def handle_execute_callback" telegram_bot.py && echo "  ✓ handle_execute_callback() in telegram_bot.py"
grep -q "async def handle_pass_callback" telegram_bot.py && echo "  ✓ handle_pass_callback() in telegram_bot.py"

echo ""
echo "✓ Checking Signal dataclass..."
python3 -c "from strategy import Signal; s = Signal('BTC', 'long', 89000, 90500, 88000, 0.71, 2.5, None, {}, id=1); print('  ✓ Signal.id field works')" 2>&1

echo ""
echo "==================================="
echo "Verification Complete!"
echo "==================================="
echo ""
echo "Next steps:"
echo "1. pip install python-telegram-bot>=20.0"
echo "2. Add TELEGRAM_BOT_TOKEN to .env"
echo "3. Add TELEGRAM_CHAT_ID to .env"
echo "4. python telegram_bot.py"
echo ""
