"""Candlestick chart renderer with trade signal overlays."""

import io
import time
from typing import List, Dict, Any, Optional, Tuple
import logging

import mplfinance as mpf
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.figure import Figure
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


def fetch_candles(
    asset: str, num_candles: int = 60, base_price: Optional[float] = None
) -> List[Dict[str, Any]]:
    """Fetch recent 1-minute candle data from Hyperliquid.

    Args:
        asset: Asset ticker (e.g., "BTC", "ETH")
        num_candles: Number of candles to fetch (default 60)
        base_price: Base price for mock data fallback (default 67000.0)

    Returns:
        List of dicts with keys: timestamp, open, high, low, close
    """
    try:
        from hyperliquid.info import Info
        from hyperliquid.utils.constants import MAINNET_API_URL

        info = Info(MAINNET_API_URL, skip_ws=True)

        end_time = int(time.time() * 1000)
        start_time = end_time - (num_candles * 60 * 1000)

        logger.info(f"Fetching {num_candles} candles for {asset} from Hyperliquid")
        candles = info.candles_snapshot(asset, "1m", start_time, end_time)

        if not candles:
            logger.warning(f"No candles returned for {asset}, using mock data")
            return _generate_mock_candles(num_candles, base_price=base_price)

        result = []
        for c in candles:
            result.append(
                {
                    "timestamp": pd.to_datetime(c["t"], unit="ms"),
                    "open": float(c["o"]),
                    "high": float(c["h"]),
                    "low": float(c["l"]),
                    "close": float(c["c"]),
                }
            )

        logger.info(f"Fetched {len(result)} candles for {asset}")
        return result

    except Exception as e:
        logger.warning(f"Failed to fetch candles for {asset}: {e}, using mock data")
        return _generate_mock_candles(num_candles, base_price=base_price)


def _generate_mock_candles(
    num_candles: int, base_price: Optional[float] = None
) -> List[Dict[str, Any]]:
    """Generate mock candle data for testing.

    Args:
        num_candles: Number of candles to generate
        base_price: Starting price (default 67000.0)

    Returns:
        List of candle dicts
    """
    if base_price is None:
        base_price = 67000.0
    np.random.seed(42)
    timestamps = pd.date_range(end=pd.Timestamp.now(), periods=num_candles, freq="1min")

    candles = []
    price = base_price

    for ts in timestamps:
        change = np.random.uniform(-0.002, 0.002)
        open_price = price
        close_price = price * (1 + change)
        high_price = max(open_price, close_price) * (1 + np.random.uniform(0, 0.001))
        low_price = min(open_price, close_price) * (1 - np.random.uniform(0, 0.001))

        candles.append(
            {
                "timestamp": ts,
                "open": round(open_price, 2),
                "high": round(high_price, 2),
                "low": round(low_price, 2),
                "close": round(close_price, 2),
            }
        )
        price = close_price

    return candles


def render_signal_chart(
    candle_data: List[Dict[str, Any]],
    signal: Any,
    percentile_band: Tuple[float, float],
    asset: str,
) -> io.BytesIO:
    """Render candlestick chart with trade signal overlays.

    Args:
        candle_data: List of dicts with keys: timestamp, open, high, low, close
        signal: Signal dataclass with entry_price, take_profit, stop_loss, direction
        percentile_band: Tuple of (lower_bound, upper_bound) for 5th/95th percentile
        asset: Asset symbol for title

    Returns:
        BytesIO buffer containing PNG image
    """
    df = pd.DataFrame(candle_data)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df.set_index("timestamp", inplace=True)
    df.columns = ["Open", "High", "Low", "Close"]

    mc = mpf.make_marketcolors(
        up="#00d4aa",
        down="#ff6b6b",
        edge="inherit",
        wick="inherit",
        volume="in",
    )
    s = mpf.make_mpf_style(
        marketcolors=mc,
        facecolor="#1a1a2e",
        edgecolor="#1a1a2e",
        figcolor="#1a1a2e",
        gridcolor="#2d2d4a",
        gridstyle="--",
        y_on_right=True,
    )

    lower_band, upper_band = percentile_band

    fill_between = dict(
        y1=[lower_band] * len(df),
        y2=[upper_band] * len(df),
        alpha=0.15,
        color="#6b5b95",
    )

    title = f"{asset} — {signal.direction.upper()} Signal"
    title_color = "white"

    fig, axes = mpf.plot(
        df,
        type="candle",
        style=s,
        title=title,
        figsize=(10, 6),
        returnfig=True,
        fill_between=fill_between,
        volume=False,
        tight_layout=True,
        scale_padding=dict(left=0.1, right=0.1, top=0.1, bottom=0.1),
        datetime_format="",
        xrotation=0,
    )

    ax = axes[0]
    ax.tick_params(axis="x", labelbottom=False)
    ax.set_xlabel("")

    ax.axhline(
        y=signal.entry_price, color="white", linestyle="--", linewidth=1.5, alpha=0.9
    )
    ax.axhline(
        y=signal.take_profit, color="#00d4aa", linestyle="-", linewidth=1.5, alpha=0.9
    )
    ax.axhline(
        y=signal.stop_loss, color="#ff6b6b", linestyle="-", linewidth=1.5, alpha=0.9
    )

    ax.fill_between(
        range(len(df)),
        [lower_band] * len(df),
        [upper_band] * len(df),
        alpha=0.15,
        color="#6b5b95",
    )

    candle_low = df["Low"].min()
    candle_high = df["High"].max()
    y_min = min(candle_low, signal.stop_loss, lower_band)
    y_max = max(candle_high, signal.take_profit, upper_band)
    padding = (y_max - y_min) * 0.05
    ax.set_ylim(y_min - padding, y_max + padding)

    entry_patch = mpatches.Patch(
        color="white", label=f"Entry: ${signal.entry_price:,.2f}"
    )
    tp_patch = mpatches.Patch(color="#00d4aa", label=f"TP: ${signal.take_profit:,.2f}")
    sl_patch = mpatches.Patch(color="#ff6b6b", label=f"SL: ${signal.stop_loss:,.2f}")
    band_patch = mpatches.Patch(
        color="#6b5b95",
        alpha=0.3,
        label=f"5th-95th %ile: ${lower_band:,.0f} - ${upper_band:,.0f}",
    )

    legend = ax.legend(
        handles=[entry_patch, tp_patch, sl_patch, band_patch],
        loc="upper left",
        fontsize=9,
        facecolor="#1a1a2e",
        edgecolor="#2d2d4a",
        labelcolor="white",
        framealpha=0.9,
    )

    buf = io.BytesIO()
    fig.savefig(
        buf,
        format="png",
        dpi=150,
        facecolor="#1a1a2e",
        edgecolor="none",
        bbox_inches="tight",
    )
    buf.seek(0)
    plt.close(fig)

    return buf


def render_pnl_summary(
    asset: str,
    direction: str,
    entry: float,
    exit_price: float,
    pnl_usd: float,
    pnl_pct: float,
    duration_min: int,
) -> io.BytesIO:
    """Render P&L summary card.

    Args:
        asset: Asset symbol
        direction: "long" or "short"
        entry: Entry price
        exit_price: Exit price
        pnl_usd: P&L in USD
        pnl_pct: P&L as percentage
        duration_min: Trade duration in minutes

    Returns:
        BytesIO buffer containing PNG image
    """
    fig = Figure(figsize=(8, 4), facecolor="#1a1a2e", dpi=150)
    ax = fig.add_subplot(111)
    ax.set_facecolor("#1a1a2e")
    ax.axis("off")

    is_profit = pnl_usd >= 0
    pnl_color = "#00d4aa" if is_profit else "#ff6b6b"
    pnl_sign = "+" if is_profit else ""

    fig.text(
        0.5,
        0.88,
        f"{asset} {direction.upper()}",
        fontsize=16,
        color="white",
        ha="center",
        fontweight="bold",
    )

    fig.text(
        0.5,
        0.68,
        f"{pnl_sign}${abs(pnl_usd):,.2f}",
        fontsize=28,
        color=pnl_color,
        ha="center",
        fontweight="bold",
    )
    fig.text(
        0.5,
        0.50,
        f"({pnl_sign}{pnl_pct:.2f}%)",
        fontsize=16,
        color=pnl_color,
        ha="center",
    )

    details_y = 0.30
    fig.text(
        0.25,
        details_y,
        f"Entry: ${entry:,.2f}",
        fontsize=11,
        color="#888899",
        ha="center",
    )
    fig.text(
        0.75,
        details_y,
        f"Exit: ${exit_price:,.2f}",
        fontsize=11,
        color="#888899",
        ha="center",
    )

    fig.text(
        0.5,
        0.15,
        f"Duration: {duration_min} min",
        fontsize=10,
        color="#666677",
        ha="center",
    )

    border = mpatches.FancyBboxPatch(
        (0.02, 0.02),
        0.96,
        0.96,
        boxstyle="round,pad=0.02,rounding_size=0.02",
        facecolor="#1a1a2e",
        edgecolor="#2d2d4a",
        linewidth=2,
        transform=fig.transFigure,
        zorder=0,
    )
    fig.patches.append(border)

    buf = io.BytesIO()
    fig.savefig(
        buf,
        format="png",
        facecolor="#1a1a2e",
        edgecolor="none",
        bbox_inches="tight",
        pad_inches=0.1,
    )
    buf.seek(0)

    return buf
