# Hyperliquid Client Usage Guide

## Files Created

1. **config.py** - Configuration management
2. **hl_client.py** - Hyperliquid SDK wrapper client
3. **test_hl.py** - Test script

## Setup

1. Install dependencies:
   ```bash
   pip install hyperliquid-python-sdk python-dotenv eth-account
   ```

2. Update `.env` file with your credentials:
   ```
   HL_WALLET_ADDRESS=0x...  # Your Ethereum wallet address
   HL_PRIVATE_KEY=0x...     # Your private key (keep secure!)
   HL_TESTNET=true          # Use testnet (recommended for testing)
   ```

## Usage Examples

### Basic Usage

```python
from config import HL_WALLET_ADDRESS, HL_PRIVATE_KEY, HL_TESTNET
from hl_client import HLClient

# Initialize client
client = HLClient(HL_WALLET_ADDRESS, HL_PRIVATE_KEY, testnet=HL_TESTNET)

# Get BTC price
btc_price = client.get_mid_price("BTC")
print(f"BTC Price: ${btc_price:,.2f}")

# Get all mid prices
all_mids = client.get_all_mids()
for asset, price in all_mids.items():
    print(f"{asset}: ${price}")

# Get open positions
positions = client.get_positions()
for pos in positions:
    print(f"{pos['position']['coin']}: {pos['position']['szi']}")

# Get open orders
orders = client.get_open_orders()
for order in orders:
    print(f"{order['coin']}: {order['side']} {order['sz']} @ ${order['limitPx']}")
```

### Trading Operations

```python
# Open a long position (market order)
client.market_open("BTC", is_buy=True, size=0.001)

# Open a short position (market order)
client.market_open("ETH", is_buy=False, size=0.01)

# Close a position
client.market_close("BTC")

# Place a limit buy order
client.limit_order("BTC", is_buy=True, size=0.001, price=50000.0)

# Place a limit sell order (reduce only)
client.limit_order("ETH", is_buy=False, size=0.01, price=3000.0, reduce_only=True)

# Cancel a specific order
client.cancel_order("BTC", order_id=12345)

# Cancel all orders for an asset
client.cancel_all("BTC")
```

## Running Tests

```bash
python3 test_hl.py
```

The test script will:
1. Fetch BTC mid price
2. Fetch all positions
3. Place a small limit buy order for BTC at 50% of market price (won't fill)
4. Fetch open orders to confirm the order exists
5. Cancel the order
6. Confirm the order is cancelled

**Note:** Make sure you have sufficient testnet balance before running tests.

## Error Handling

All methods include comprehensive error handling and logging. Errors are logged with context and re-raised for handling by the calling code.

```python
try:
    client.market_open("BTC", is_buy=True, size=0.001)
except Exception as e:
    print(f"Failed to open position: {e}")
```

## Important Notes

1. **Testnet vs Mainnet**: Always test on testnet first (`HL_TESTNET=true`)
2. **Private Key Security**: Never commit your private key to version control
3. **Asset Names**: Use simple tickers like "BTC", "ETH", "SOL" (not "BTC-PERP")
4. **Order Types**: The SDK handles all signing internally - no session keys needed
5. **Cancel Scope**: `cancel_all(asset)` only cancels orders for that specific asset

## Available Assets

Configured in `config.py`:
- BTC
- ETH
- SOL

You can add more assets by updating the `ASSETS` list in `config.py`.

## Logging

The client uses Python's logging module at INFO level by default. Logs include:
- Client initialization
- API calls and responses
- Errors with context

To change log level:
```python
import logging
logging.getLogger("hl_client").setLevel(logging.DEBUG)
```
