"""Test script for chart renderer with mock signal data."""

import sys
from datetime import datetime

from chart_renderer import render_signal_chart, fetch_candles, render_pnl_summary
from strategy import Signal


def main():
    print("Fetching candle data...")
    candle_data = fetch_candles("BTC", num_candles=60)

    if not candle_data:
        print("ERROR: No candle data returned")
        return 1

    print(f"Got {len(candle_data)} candles")
    print(f"First candle: {candle_data[0]}")
    print(f"Last candle: {candle_data[-1]}")

    entry_price = candle_data[-1]["close"]
    take_profit = entry_price * 1.002
    stop_loss = entry_price * 0.998
    percentile_band = (entry_price * 0.997, entry_price * 1.003)

    print(f"\nDerived signal prices from candle data:")
    print(f"  Entry: ${entry_price:,.2f}")
    print(f"  TP: ${take_profit:,.2f} (+0.2%)")
    print(f"  SL: ${stop_loss:,.2f} (-0.2%)")
    print(f"  Band: ${percentile_band[0]:,.2f} - ${percentile_band[1]:,.2f}")

    signal = Signal(
        asset="BTC",
        direction="long",
        entry_price=entry_price,
        take_profit=take_profit,
        stop_loss=stop_loss,
        win_rate=0.71,
        vol_spread_pct=0.89,
        timestamp=datetime.utcnow(),
        percentiles_snapshot={
            "0.05": percentile_band[0],
            "0.5": entry_price,
            "0.95": percentile_band[1],
        },
    )

    print("\nRendering signal chart...")
    buf = render_signal_chart(candle_data, signal, percentile_band, "BTC")

    output_path = "test_signal.png"
    with open(output_path, "wb") as f:
        f.write(buf.getvalue())

    print(f"Chart saved to {output_path}")

    print("\n=== P&L Summary Test 1: Winning Trade ===")
    print("  asset: BTC, direction: long")
    print("  entry: $72,700.00, exit: $72,845.00")
    print("  pnl_usd: +$1.99, pnl_pct: +0.20%")
    print("  duration: 34 min")

    buf_pnl = render_pnl_summary(
        asset="BTC",
        direction="long",
        entry=72700.00,
        exit_price=72845.00,
        pnl_usd=1.99,
        pnl_pct=0.20,
        duration_min=34,
    )

    pnl_path = "test_pnl.png"
    with open(pnl_path, "wb") as f:
        f.write(buf_pnl.getvalue())
    print(f"  Saved to {pnl_path}")

    print("\n=== P&L Summary Test 2: Losing Trade ===")
    print("  asset: ETH, direction: short")
    print("  entry: $3,850.00, exit: $3,872.00")
    print("  pnl_usd: -$5.71, pnl_pct: -0.57%")
    print("  duration: 12 min")

    buf_pnl_loss = render_pnl_summary(
        asset="ETH",
        direction="short",
        entry=3850.00,
        exit_price=3872.00,
        pnl_usd=-5.71,
        pnl_pct=-0.57,
        duration_min=12,
    )

    pnl_loss_path = "test_pnl_loss.png"
    with open(pnl_loss_path, "wb") as f:
        f.write(buf_pnl_loss.getvalue())
    print(f"  Saved to {pnl_loss_path}")

    print("\nAll tests completed!")
    print(f"  - {output_path}")
    print(f"  - {pnl_path}")
    print(f"  - {pnl_loss_path}")
    print("Done!")

    return 0


if __name__ == "__main__":
    sys.exit(main())
