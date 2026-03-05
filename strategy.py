"""Momentum Perp signal evaluator."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import logging

logger = logging.getLogger(__name__)

COOLDOWN_MINUTES = 30
PERCENTILE_KEYS = ["0.05", "0.2", "0.35", "0.5", "0.65", "0.8", "0.95"]

_cooldown_tracker: Dict[str, datetime] = {}


@dataclass
class Signal:
    """Trading signal dataclass."""

    asset: str
    direction: str
    entry_price: float
    take_profit: float
    stop_loss: float
    win_rate: float
    vol_spread_pct: float
    timestamp: datetime
    percentiles_snapshot: Dict[str, Any]
    id: Optional[int] = None


def evaluate_signal(asset: str, percentile_data: Dict[str, Any]) -> Optional[Signal]:
    """Evaluate signal from prediction percentiles.

    LONG condition: percentile "0.35" > current_price
    SHORT condition: percentile "0.65" < current_price
    TP: percentile "0.5" (median at 1h)
    SL: percentile "0.05" for longs, "0.95" for shorts
    Win rate: count how many of [0.05, 0.2, 0.35, 0.5, 0.65, 0.8, 0.95]
              end above current_price (for long) or below (for short), divide by 7
    Vol proxy: (p95 - p05) / current_price as a percentage

    Args:
        asset: Asset symbol
        percentile_data: Response from prediction-percentiles endpoint

    Returns:
        Signal if conditions met, None otherwise
    """
    current_price = percentile_data.get("current_price")
    if not current_price:
        logger.warning("No current_price in response")
        return None

    percentiles = percentile_data.get("forecast_future", {}).get("percentiles", [])
    if not percentiles:
        logger.warning("No percentiles in response")
        return None

    final_percentiles = percentiles[-1]

    now = datetime.utcnow()

    p35 = final_percentiles.get("0.35")
    if p35 and p35 > current_price:
        direction = "long"
        cooldown_key = f"{asset}_{direction}"

        if cooldown_key in _cooldown_tracker:
            if now - _cooldown_tracker[cooldown_key] < timedelta(
                minutes=COOLDOWN_MINUTES
            ):
                logger.info(f"Signal on cooldown: {cooldown_key}")
                return None

        p50 = final_percentiles.get("0.5")
        p05 = final_percentiles.get("0.05")
        p95 = final_percentiles.get("0.95")

        tp = p50
        sl = p05

        count_above = sum(
            1
            for key in PERCENTILE_KEYS
            if final_percentiles.get(key, 0) > current_price
        )
        win_rate = count_above / len(PERCENTILE_KEYS)

        vol_spread = (p95 - p05) / current_price * 100

        _cooldown_tracker[cooldown_key] = now

        logger.info(f"LONG signal generated for {asset} at ${current_price:.2f}")

        return Signal(
            asset=asset,
            direction=direction,
            entry_price=current_price,
            take_profit=tp,
            stop_loss=sl,
            win_rate=win_rate,
            vol_spread_pct=vol_spread,
            timestamp=now,
            percentiles_snapshot=final_percentiles,
        )

    p65 = final_percentiles.get("0.65")
    if p65 and p65 < current_price:
        direction = "short"
        cooldown_key = f"{asset}_{direction}"

        if cooldown_key in _cooldown_tracker:
            if now - _cooldown_tracker[cooldown_key] < timedelta(
                minutes=COOLDOWN_MINUTES
            ):
                logger.info(f"Signal on cooldown: {cooldown_key}")
                return None

        p50 = final_percentiles.get("0.5")
        p05 = final_percentiles.get("0.05")
        p95 = final_percentiles.get("0.95")

        tp = p50
        sl = p95

        count_below = sum(
            1
            for key in PERCENTILE_KEYS
            if final_percentiles.get(key, float("inf")) < current_price
        )
        win_rate = count_below / len(PERCENTILE_KEYS)

        vol_spread = (p95 - p05) / current_price * 100

        _cooldown_tracker[cooldown_key] = now

        logger.info(f"SHORT signal generated for {asset} at ${current_price:.2f}")

        return Signal(
            asset=asset,
            direction=direction,
            entry_price=current_price,
            take_profit=tp,
            stop_loss=sl,
            win_rate=win_rate,
            vol_spread_pct=vol_spread,
            timestamp=now,
            percentiles_snapshot=final_percentiles,
        )

    logger.info(f"No signal for {asset} at ${current_price:.2f}")
    return None


def evaluate_test_signal(asset: str, percentile_data: Dict[str, Any]) -> Signal:
    """Evaluate test signal from prediction percentiles with relaxed thresholds.

    LONG condition: percentile "0.50" > current_price (median above price)
    SHORT condition: percentile "0.50" < current_price (median below price)
    No cooldown checks for test signals.
    Entry price = current_price from Synth response.

    Args:
        asset: Asset symbol
        percentile_data: Response from prediction-percentiles endpoint

    Returns:
        Signal (always produces a signal)
    """
    current_price = percentile_data.get("current_price")
    if not current_price:
        logger.warning("No current_price in response")
        raise ValueError("No current_price in percentile_data")

    percentiles = percentile_data.get("forecast_future", {}).get("percentiles", [])
    if not percentiles:
        logger.warning("No percentiles in response")
        raise ValueError("No percentiles in percentile_data")

    final_percentiles = percentiles[-1]

    now = datetime.utcnow()

    p50 = final_percentiles.get("0.5")
    p05 = final_percentiles.get("0.05")
    p95 = final_percentiles.get("0.95")

    if p50 > current_price:
        direction = "long"
        tp = p50
        sl = p05
        count_above = sum(
            1
            for key in PERCENTILE_KEYS
            if final_percentiles.get(key, 0) > current_price
        )
        win_rate = count_above / len(PERCENTILE_KEYS)
        logger.info(f"Test LONG signal generated for {asset} at ${current_price:.2f}")
    else:
        direction = "short"
        tp = p50
        sl = p95
        count_below = sum(
            1
            for key in PERCENTILE_KEYS
            if final_percentiles.get(key, float("inf")) < current_price
        )
        win_rate = count_below / len(PERCENTILE_KEYS)
        logger.info(f"Test SHORT signal generated for {asset} at ${current_price:.2f}")

    vol_spread = (p95 - p05) / current_price * 100

    return Signal(
        asset=asset,
        direction=direction,
        entry_price=current_price,
        take_profit=tp,
        stop_loss=sl,
        win_rate=win_rate,
        vol_spread_pct=vol_spread,
        timestamp=now,
        percentiles_snapshot=final_percentiles,
    )


def calculate_trade_stats(signal: Signal, position_size_usd: float) -> Dict[str, float]:
    """Calculate trade statistics.

    Args:
        signal: Trading signal
        position_size_usd: Position size in USD

    Returns:
        Dictionary with:
            - expected_profit: position_size * (tp - entry) / entry * win_rate
            - max_loss: position_size * abs(sl - entry) / entry
            - risk_reward_ratio: expected_profit / max_loss
    """
    if signal.direction == "long":
        profit_pct = (signal.take_profit - signal.entry_price) / signal.entry_price
        loss_pct = abs(signal.stop_loss - signal.entry_price) / signal.entry_price
    else:
        profit_pct = (signal.entry_price - signal.take_profit) / signal.entry_price
        loss_pct = abs(signal.entry_price - signal.stop_loss) / signal.entry_price

    expected_profit = position_size_usd * profit_pct * signal.win_rate
    max_loss = position_size_usd * loss_pct

    risk_reward_ratio = expected_profit / max_loss if max_loss > 0 else 0.0

    return {
        "expected_profit": expected_profit,
        "max_loss": max_loss,
        "risk_reward_ratio": risk_reward_ratio,
    }
