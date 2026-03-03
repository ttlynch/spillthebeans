"""Test script for Hyperliquid client."""

import sys
import time

from config import HL_PRIVATE_KEY, HL_WALLET_ADDRESS, HL_TESTNET, validate_config
from hl_client import HLClient, logger


def main():
    """Run test sequence for HLClient."""
    print("=" * 80)
    print("Hyperliquid Client Test Script")
    print("=" * 80)
    print()

    try:
        validate_config()
    except ValueError as e:
        print(f"❌ Configuration error: {e}")
        sys.exit(1)

    print(f"Environment: {'TESTNET' if HL_TESTNET else 'MAINNET'}")
    print(f"Wallet: {HL_WALLET_ADDRESS}")
    print()

    try:
        client = HLClient(HL_WALLET_ADDRESS, HL_PRIVATE_KEY, testnet=HL_TESTNET)
    except Exception as e:
        print(f"❌ Failed to initialize client: {e}")
        sys.exit(1)

    print("✅ Client initialized successfully")
    print()

    print("-" * 80)
    print("TEST 1: Fetch BTC mid price")
    print("-" * 80)
    try:
        btc_price = client.get_mid_price("BTC")
        print(f"✅ BTC mid price: ${btc_price:,.2f}")
    except Exception as e:
        print(f"❌ Failed to fetch BTC price: {e}")
        sys.exit(1)
    print()

    print("-" * 80)
    print("TEST 2: Fetch all positions")
    print("-" * 80)
    try:
        positions = client.get_positions()
        if positions:
            print(f"✅ Found {len(positions)} position(s):")
            for pos in positions:
                position_data = pos.get("position", {})
                coin = position_data.get("coin", "Unknown")
                szi = position_data.get("szi", "0")
                entry_px = position_data.get("entryPx", "0")
                unrealized_pnl = position_data.get("unrealizedPnl", "0")
                print(
                    f"  - {coin}: size={szi}, entry=${entry_px}, PnL=${unrealized_pnl}"
                )
        else:
            print("✅ No open positions")
    except Exception as e:
        print(f"❌ Failed to fetch positions: {e}")
        sys.exit(1)
    print()

    print("-" * 80)
    print("TEST 3: Place limit buy order for BTC (50% below market)")
    print("-" * 80)

    # Get BTC metadata for proper price rounding
    try:
        btc_meta = client.get_asset_meta("BTC")
        print(f"BTC tick size: ${btc_meta['tickSize']}")
    except Exception as e:
        print(f"❌ Failed to get BTC metadata: {e}")
        sys.exit(1)

    # Calculate and round limit price
    limit_price_raw = btc_price * 0.5
    limit_price = client._round_price(limit_price_raw, btc_meta["tickSize"])
    size = 0.001

    print(f"Market price: ${btc_price:,.2f}")
    print(f"Limit price: ${limit_price:,.2f} (rounded from ${limit_price_raw:,.2f})")
    print(f"Size: {size} BTC")
    print()

    order_result = None
    order_id = None
    try:
        order_result = client.limit_order(
            "BTC", is_buy=True, size=size, price=limit_price
        )
        print(f"✅ Limit order placed successfully")

        # Validate and extract order info
        success, oid, error = client._validate_order_response(order_result)

        if success:
            order_id = oid
            if order_id:
                print(f"Order ID: {order_id}")
                print(f"Status: Resting on order book")
            else:
                print(f"Status: Filled immediately")
        else:
            print(f"❌ Order rejected: {error}")
            if "Insufficient" in str(error):
                print("❌ Insufficient balance to place order")
            print("⚠️  Skipping remaining tests due to order failure")
            sys.exit(1)

    except Exception as e:
        error_str = str(e)
        print(f"❌ Failed to place limit order: {e}")
        if "Insufficient" in error_str or "rejected" in error_str.lower():
            print("❌ Order was rejected")
            print("⚠️  Skipping remaining tests")
            sys.exit(1)
        sys.exit(1)
    print()

    time.sleep(1)

    print("-" * 80)
    print("TEST 4: Fetch open orders to confirm order exists")
    print("-" * 80)
    try:
        open_orders = client.get_open_orders()
        btc_orders = [order for order in open_orders if order["coin"] == "BTC"]
        if btc_orders:
            print(f"✅ Found {len(btc_orders)} BTC order(s):")
            for order in btc_orders:
                oid = order.get("oid")
                side = "BUY" if order.get("side") == "B" else "SELL"
                sz = order.get("sz")
                limit_px = order.get("limitPx")
                print(f"  - Order #{oid}: {side} {sz} @ ${limit_px}")
        else:
            print("⚠️  No BTC orders found (order may have filled or failed)")
    except Exception as e:
        print(f"❌ Failed to fetch open orders: {e}")
        sys.exit(1)
    print()

    print("-" * 80)
    print("TEST 5: Cancel the order")
    print("-" * 80)
    if order_id:
        try:
            cancel_result = client.cancel_order("BTC", order_id)
            print(f"✅ Order cancelled successfully")
            print(f"Response: {cancel_result}")
        except Exception as e:
            print(f"❌ Failed to cancel order: {e}")
            sys.exit(1)
    else:
        print("⚠️  No order ID available, skipping cancellation")
        try:
            print("Attempting to cancel all BTC orders...")
            cancel_results = client.cancel_all("BTC")
            print(f"✅ Cancelled {len(cancel_results)} order(s)")
        except Exception as e:
            print(f"❌ Failed to cancel orders: {e}")
            sys.exit(1)
    print()

    time.sleep(1)

    print("-" * 80)
    print("TEST 6: Fetch open orders to confirm cancellation")
    print("-" * 80)
    try:
        open_orders = client.get_open_orders()
        btc_orders = [order for order in open_orders if order["coin"] == "BTC"]
        if btc_orders:
            print(f"⚠️  Still have {len(btc_orders)} BTC order(s):")
            for order in btc_orders:
                oid = order.get("oid")
                side = "BUY" if order.get("side") == "B" else "SELL"
                sz = order.get("sz")
                limit_px = order.get("limitPx")
                print(f"  - Order #{oid}: {side} {sz} @ ${limit_px}")
        else:
            print("✅ No BTC orders found - cancellation confirmed")
    except Exception as e:
        print(f"❌ Failed to fetch open orders: {e}")
        sys.exit(1)
    print()

    print("=" * 80)
    print("✅ ALL TESTS PASSED")
    print("=" * 80)


if __name__ == "__main__":
    main()
