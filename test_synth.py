"""Test script for Synth API integration."""

import asyncio
import sys
import logging

from config import SYNTH_API_KEY
from synth_client import SynthClient
from strategy import evaluate_signal, calculate_trade_stats
from db import init_db, save_signal

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def main():
    """Test Synth API integration and signal evaluation."""
    print("=" * 80)
    print("Synth API Integration Test")
    print("=" * 80)
    print()

    if not SYNTH_API_KEY:
        print("❌ SYNTH_API_KEY not set in environment")
        sys.exit(1)

    print(f"API Key: {SYNTH_API_KEY[:10]}...")
    print()

    print("-" * 80)
    print("Initializing database...")
    print("-" * 80)
    try:
        conn = init_db()
        print("✅ Database initialized at data/trading.db")
    except Exception as e:
        print(f"❌ Failed to initialize database: {e}")
        sys.exit(1)
    print()

    print("-" * 80)
    print("Fetching BTC prediction percentiles (1h horizon)...")
    print("-" * 80)

    async with SynthClient(SYNTH_API_KEY) as client:
        try:
            data = await client.get_prediction_percentiles("BTC", horizon="1h")
            current_price = data.get("current_price")
            print(f"✅ Current BTC price: ${current_price:,.2f}")

            percentiles = data.get("forecast_future", {}).get("percentiles", [])
            if percentiles:
                final = percentiles[-1]
                print(f"   p05: ${final.get('0.05'):,.2f}")
                print(f"   p35: ${final.get('0.35'):,.2f}")
                print(f"   p50: ${final.get('0.5'):,.2f}")
                print(f"   p65: ${final.get('0.65'):,.2f}")
                print(f"   p95: ${final.get('0.95'):,.2f}")
        except Exception as e:
            print(f"❌ Failed to fetch percentiles: {e}")
            sys.exit(1)
    print()

    print("-" * 80)
    print("Evaluating signal...")
    print("-" * 80)

    signal = evaluate_signal("BTC", data)

    if signal:
        print(f"✅ SIGNAL GENERATED: {signal.direction.upper()}")
        print(f"   Asset: {signal.asset}")
        print(f"   Entry: ${signal.entry_price:,.2f}")
        print(f"   Take Profit: ${signal.take_profit:,.2f}")
        print(f"   Stop Loss: ${signal.stop_loss:,.2f}")
        print(f"   Win Rate: {signal.win_rate:.2%}")
        print(f"   Vol Spread: {signal.vol_spread_pct:.2f}%")
        print(f"   Timestamp: {signal.timestamp.isoformat()}")
        print()

        print("-" * 80)
        print("Trade Stats (Position Size: $100)")
        print("-" * 80)
        stats = calculate_trade_stats(signal, 100.0)
        print(f"   Expected Profit: ${stats['expected_profit']:.2f}")
        print(f"   Max Loss: ${stats['max_loss']:.2f}")
        print(f"   Risk/Reward Ratio: {stats['risk_reward_ratio']:.2f}")
        print()

        print("-" * 80)
        print("Saving signal to database...")
        print("-" * 80)
        try:
            signal_id = save_signal(conn, signal)
            print(f"✅ Signal saved with ID: {signal_id}")
        except Exception as e:
            print(f"❌ Failed to save signal: {e}")
            sys.exit(1)
    else:
        print("❌ No signal generated")
        print("   Conditions not met:")
        print("   - LONG requires p35 > current_price")
        print("   - SHORT requires p65 < current_price")

    print()
    print("=" * 80)
    print("✅ TEST COMPLETE")
    print("=" * 80)

    conn.close()


if __name__ == "__main__":
    asyncio.run(main())
