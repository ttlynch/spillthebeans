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
    strength: int = 0


def evaluate_signal(
    asset: str,
    percentile_data: Dict[str, Any],
    long_pct: str = "0.35",
    short_pct: str = "0.65",
) -> Optional[Signal]:
    """Evaluate signal from prediction percentiles.

    LONG condition: percentile long_pct > current_price (default "0.35")
    SHORT condition: percentile short_pct < current_price (default "0.65")
    TP: percentile "0.5" (median at 1h)
    SL: percentile "0.05" for longs, "0.95" for shorts
    Win rate: count how many of [0.05, 0.2, 0.35, 0.5, 0.65, 0.8, 0.95]
              end above current_price (for long) or below (for short), divide by 7
    Vol proxy: (p95 - p05) / current_price as a percentage

    Args:
        asset: Asset symbol
        percentile_data: Response from prediction-percentiles endpoint
        long_pct: Percentile key for long trigger (default "0.35")
        short_pct: Percentile key for short trigger (default "0.65")

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

    p_long = final_percentiles.get(long_pct)
    if p_long and p_long > current_price:
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

        strength = calculate_signal_strength(
            direction,
            current_price,
            final_percentiles,
            long_pct=long_pct,
            short_pct=short_pct,
        )

        logger.info(
            f"LONG signal generated for {asset} at ${current_price:.2f}, strength={strength}"
        )

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
            strength=strength,
        )

    p_short = final_percentiles.get(short_pct)
    if p_short and p_short < current_price:
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

        strength = calculate_signal_strength(
            direction,
            current_price,
            final_percentiles,
            long_pct=long_pct,
            short_pct=short_pct,
        )

        logger.info(
            f"SHORT signal generated for {asset} at ${current_price:.2f}, strength={strength}"
        )

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
            strength=strength,
        )

    logger.info(f"No signal for {asset} at ${current_price:.2f}")
    return None


def evaluate_test_signal(
    asset: str,
    percentile_data: Dict[str, Any],
    long_pct: str = "0.35",
    short_pct: str = "0.65",
) -> Signal:
    """Evaluate test signal from prediction percentiles with relaxed thresholds.

    LONG condition: percentile "0.50" > current_price (median above price)
    SHORT condition: percentile "0.50" < current_price (median below price)
    No cooldown checks for test signals.
    Entry price = current_price from Synth response.

    Args:
        asset: Asset symbol
        percentile_data: Response from prediction-percentiles endpoint
        long_pct: Percentile key for long trigger (default "0.35")
        short_pct: Percentile key for short trigger (default "0.65")

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

    strength = calculate_signal_strength(
        direction, current_price, final_percentiles, long_pct="0.5", short_pct="0.5"
    )

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
        strength=strength,
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


def calculate_signal_strength(
    direction: str,
    current_price: float,
    final_percentiles: Dict[str, float],
    long_pct: str = "0.35",
    short_pct: str = "0.65",
) -> int:
    """Calculate signal strength score (0-100).

    Component 1: Threshold Overshoot (0-40 points)
    Component 2: Percentile Agreement (0-35 points)
    Component 3: Forecast Tightness (0-25 points)

    Args:
        direction: "long" or "short"
        current_price: Current asset price
        final_percentiles: Percentile values from Synth API
        long_pct: Percentile key for long trigger
        short_pct: Percentile key for short trigger

    Returns:
        Strength score (0-100)
    """
    p05 = final_percentiles.get("0.05", current_price)
    p95 = final_percentiles.get("0.95", current_price)

    trigger_percentile_value = final_percentiles.get(
        long_pct if direction == "long" else short_pct, current_price
    )

    component1 = 0.0
    if direction == "long" and trigger_percentile_value > current_price:
        overshoot = (trigger_percentile_value - current_price) / current_price
        component1 = min(overshoot / 0.02, 1.0) * 40
    elif direction == "short" and trigger_percentile_value < current_price:
        overshoot = (current_price - trigger_percentile_value) / current_price
        component1 = min(overshoot / 0.02, 1.0) * 40

    component2 = 0.0
    standard_percentiles = ["0.05", "0.2", "0.35", "0.5", "0.65", "0.8", "0.95"]
    if direction == "long":
        count_above = sum(
            1
            for key in standard_percentiles
            if final_percentiles.get(key, 0) > current_price
        )
        component2 = (count_above / 7) * 35
    else:
        count_below = sum(
            1
            for key in standard_percentiles
            if final_percentiles.get(key, float("inf")) < current_price
        )
        component2 = (count_below / 7) * 35

    component3 = 0.0
    vol_spread = (p95 - p05) / current_price
    component3 = max(0, (1 - vol_spread / 0.05)) * 25

    total = component1 + component2 + component3
    strength = int(min(100, max(0, total)))

    logger.info(
        f"Signal strength: {strength}/100 "
        f"(overshoot={component1:.1f}, agreement={component2:.1f}, tightness={component3:.1f})"
    )

    return strength


def strength_to_dots(strength: int) -> str:
    """Convert strength score to visual dot indicator.

    Args:
        strength: Strength score (0-100)

    Returns:
        String of emoji dots
    """
    if strength < 20:
        return "⚪⚪⚪⚪⚪"
    elif strength < 40:
        return "🟢⚪⚪⚪⚪"
    elif strength < 60:
        return "🟢🟢⚪⚪⚪"
    elif strength < 80:
        return "🟢🟢🟢⚪⚪"
    elif strength < 90:
        return "🟢🟢🟢🟢⚪"
    else:
        return "🟢🟢🟢🟢🟢"


def evaluate_signal_multi_horizon(
    asset: str,
    data_primary: Dict[str, Any],
    data_secondary: Dict[str, Any],
    long_pct: str = "0.35",
    short_pct: str = "0.65",
) -> Optional[Signal]:
    """Evaluate signal with multi-horizon confirmation.

    Runs evaluate_signal() on primary (1h) data. If signal exists, checks
    if secondary horizon agrees with direction. Returns None if secondary
    disagrees, otherwise returns signal with +10 strength bonus (capped at 100).

    Args:
        asset: Asset symbol
        data_primary: Primary (1h) percentile data from Synth API
        data_secondary: Secondary horizon percentile data from Synth API
        long_pct: Percentile key for long trigger
        short_pct: Percentile key for short trigger

    Returns:
        Signal with strength bonus if confirmed, None if filtered out
    """
    primary_signal = evaluate_signal(
        asset, data_primary, long_pct=long_pct, short_pct=short_pct
    )

    if primary_signal is None:
        return None

    secondary_percentiles = data_secondary.get("forecast_future", {}).get(
        "percentiles", []
    )
    if not secondary_percentiles:
        logger.info(f"Secondary horizon: no percentiles, filtering signal")
        return None

    secondary_final = secondary_percentiles[-1]
    secondary_p50 = secondary_final.get("0.5")
    secondary_current = data_secondary.get("current_price")

    if not secondary_p50 or not secondary_current:
        logger.info(f"Secondary horizon: missing data, filtering signal")
        return None

    confirmed = False
    if primary_signal.direction == "long" and secondary_p50 > secondary_current:
        confirmed = True
    elif primary_signal.direction == "short" and secondary_p50 < secondary_current:
        confirmed = True

    if not confirmed:
        logger.info(
            f"Multi-horizon filter: secondary horizon disagrees with {primary_signal.direction}"
        )
        return None

    new_strength = min(100, primary_signal.strength + 10)
    primary_signal.strength = new_strength

    logger.info(
        f"Multi-horizon confirmed: {asset} {primary_signal.direction.upper()}, "
        f"strength bonus +10 -> {new_strength}"
    )

    return primary_signal
