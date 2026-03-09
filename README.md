# SpillTheBeans Trading Bot

An automated trading bot that leverages the Synth API for probabilistic price forecasts and executes trades on Hyperliquid with Telegram-based controls.

## Features

- **AI-Powered Signals**: Uses Synth API's ensemble predictions (1,000 simulated paths) to generate trading signals
- **Hyperliquid Integration**: Automated execution on Hyperliquid perps with market orders and TP/SL management
- **Telegram Interface**: Interactive bot for reviewing signals, choosing position sizes, and monitoring positions
- **Position Monitoring**: Real-time P&L tracking with automatic TP/SL execution
- **Multi-Asset Support**: BTC, ETH, SOL (configurable)
- **Risk Management**: Signal validation checks (price movement, age, existing positions)

## Architecture

```
Synth API (poll every 3 min)
  → Generate signals via evaluate_signal()
  → Save to SQLite
  → Send alert via Telegram

User interacts via Telegram
  → Review signal with chart
  → Choose position size ($50/$100/$250/$500)
  → Tap EXECUTE or PASS

Execution Pipeline
  → Validate signal (price moved < 0.5%, < 10 min old, no existing position)
  → Place market order on Hyperliquid
  → Place TP limit order (reduce_only)
  → Save position to SQLite

Position Monitor (every 60s, updates every 5 min)
  → Fetch current prices
  → Calculate unrealized P&L
  → Check TP/SL conditions
  → Close positions via market_close() when hit
  → Send P&L updates every 5 minutes
  → Send close summary with chart when position closes
```

## Installation

1. Clone the repository
2. **Ensure Python 3.10 or higher is installed**:
   ```bash
   python3 --version  # Should be 3.10+
   ```
   The hyperliquid-python-sdk requires Python 3.10+ type hint syntax.
3. Create virtual environment and install dependencies:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```
4. Configure environment variables:
   ```bash
   cp .env.example .env
   # Edit .env with your API keys
   ```

## Configuration

Required environment variables (in `.env`):

```bash
SYNTH_API_KEY=your_synth_api_key           # Get from https://dashboard.synthdata.co/
HL_WALLET_ADDRESS=0x...                   # Your Hyperliquid wallet
HL_PRIVATE_KEY=0x...                      # Your private key (never commit this!)
TELEGRAM_BOT_TOKEN=your_bot_token         # Get from @BotFather
TELEGRAM_CHAT_ID=your_chat_id             # Your Telegram chat ID
HL_TESTNET=true                            # Set to false for mainnet
```

## Usage

### Start the Bot

```bash
python3 main.py
```

This will start three concurrent tasks:
1. **Synth Poller** (every 3 minutes): Fetches predictions and generates signals
2. **Position Monitor** (every 60s): Monitors open positions and manages TP/SL
3. **Telegram Bot**: Handles user interactions and notifications

### Telegram Commands

- `/start` - Show bot information
- `/status` - View open positions
- `/history` - View last 10 signals
- `/test_signal` - Send a mock BTC signal for testing

### Execution Workflow

1. Bot sends a signal alert with:
   - Asset and direction (LONG/SHORT)
   - Entry price, TP, SL
   - Win rate and expected profit
   - Price chart with percentile bands

2. You choose position size via inline buttons ($50/$100/$250/$500)

3. Tap **EXECUTE** to:
   - Validate signal (price, age, existing positions)
   - Open market position on Hyperliquid
   - Place TP limit order
   - Save position to database
   - Send confirmation

4. Bot monitors the position:
   - Sends P&L updates every 5 minutes
   - Closes automatically when TP or SL is hit
   - Sends close summary with P&L chart

## Signal Strategy

Signals are generated when:
- **LONG**: 35th percentile > current price (bullish bias)
- **SHORT**: 65th percentile < current price (bearish bias)

Trade parameters:
- **Entry**: Current price
- **TP**: Median (50th percentile at 1-hour horizon)
- **SL**: 5th percentile (longs) or 95th percentile (shorts)
- **Win Rate**: Based on percentile distribution
- **Cooldown**: 30 minutes per asset/direction

## Risk Management

- **Position size**: User-selectable ($50-$500)
- **Signal validation**: Checks price movement (< 0.5%), age (< 10 min), and existing positions
- **SL handling**: Monitors price and closes via market_close() when hit
- **TP handling**: Places limit order (reduce_only) at TP price
- **P&L updates**: Every 5 minutes to avoid spam while keeping you informed

## Database Schema

### signals table
- `id`: Primary key
- `asset`: Asset symbol (BTC, ETH, SOL)
- `direction`: "long" or "short"
- `entry`: Entry price
- `tp`: Take profit price
- `sl`: Stop loss price
- `win_rate`: Win rate (0-1)
- `vol_spread`: Volatility spread percentage
- `timestamp`: Signal generation time
- `status`: "pending", "executed", "passed"
- `percentiles_snapshot`: JSON snapshot of percentile data

### positions table
- `id`: Primary key
- `signal_id`: Reference to signals table
- `asset`: Asset symbol
- `direction`: "long" or "short"
- `size_usd`: Position size in USD
- `size_tokens`: Position size in tokens
- `entry_price`: Entry price
- `tp_price`: Take profit price
- `sl_price`: Stop loss price
- `tp_order_id`: TP limit order ID
- `status`: "open" or "closed"
- `opened_at`: Position open time
- `closed_at`: Position close time
- `exit_price`: Exit price
- `exit_reason`: "TP", "SL", or manual
- `pnl`: Profit/loss in USD

## Testing

Test individual components via Telegram using the `/test_signal` command, or run the bot in dry-run mode:
```bash
python3 main.py --dry-run
```

## Troubleshooting

### "Configuration error"
- Check that all required environment variables are set in `.env`
- Verify API keys and wallet addresses are correct

### "Execution failed"
- Check that signal hasn't expired (> 10 min old)
- Verify price hasn't moved > 0.5% from signal entry
- Ensure you don't have an existing position for the same asset
- Check Hyperliquid testnet/mainnet status

### "Signal not found"
- Signal may have been deleted or corrupted
- Check database at `data/trading.db`

### Telegram bot not responding
- Verify `TELEGRAM_BOT_TOKEN` is correct
- Check that your chat ID is whitelisted in `TELEGRAM_CHAT_ID`
- Ensure the bot is running (`python3 main.py`)

### Debugging Signal Evaluation
To monitor signal evaluation in real-time and see why signals are/aren't triggering:
```bash
tail -f spillthebeans.log | grep "SIGNAL DEBUG"
```
This shows current price vs. key percentiles (p50, p05, p95) and trigger thresholds for each asset on every poll.

## Safety Notes

⚠️ **IMPORTANT SECURITY WARNINGS**:

- Never commit `.env` file or any private keys to version control
- Use testnet for initial testing (`HL_TESTNET=true`)
- Start with small position sizes ($50-$100)
- Monitor positions closely initially
- The bot executes automatically - ensure you understand the risks
- TP/SL are managed by the bot - ensure it stays running

## License

MIT License - See LICENSE file for details

## Disclaimer

This software is provided as-is for educational purposes. Trading cryptocurrencies involves significant risk. You are solely responsible for any losses incurred. Use at your own risk.
