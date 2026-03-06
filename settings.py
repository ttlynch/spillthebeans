"""Settings manager with SQLite persistence."""

import json
import logging
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULTS = {
    "auto_scan": True,
    "assets": '["BTC", "ETH", "SOL"]',
    "risk_preset": "conservative",
    "long_percentile": "0.35",
    "short_percentile": "0.65",
    "synth_credits_total": 20000,
    "synth_credits_used": 0,
    "synth_cycle_reset_day": 1,
    "poll_interval_override": 0,
}

RISK_PRESETS = {
    "conservative": ("0.35", "0.65"),
    "moderate": ("0.45", "0.55"),
    "aggressive": ("0.50", "0.50"),
}

SCHEMA_SETTINGS = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class SettingsManager:
    """Manages user settings with SQLite persistence."""

    def __init__(self, db_path: str = "data/trading.db"):
        self.db_path = db_path
        self._conn = None
        self._ensure_initialized()
        logger.info("SettingsManager initialized")

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _ensure_initialized(self) -> None:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(SCHEMA_SETTINGS)
        conn.commit()

        for key, value in DEFAULTS.items():
            existing = cursor.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
            if existing is None:
                cursor.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?)",
                    (key, str(value)),
                )
                logger.info(f"Initialized setting: {key} = {value}")

        conn.commit()

    def get(self, key: str) -> Any:
        """Get a setting value with type inference."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()

        if row is None:
            return DEFAULTS.get(key)

        value = row["value"]

        if value.lower() == "true":
            return True
        if value.lower() == "false":
            return False

        if value.isdigit() or (value.startswith("-") and value[1:].isdigit()):
            return int(value)

        try:
            return float(value)
        except ValueError:
            return value

    def set(self, key: str, value: Any) -> None:
        """Save a setting to SQLite."""
        conn = self._get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, str(value)),
        )
        conn.commit()
        logger.info(f"Setting updated: {key} = {value}")

    def get_active_assets(self) -> List[str]:
        """Parse the JSON assets list."""
        assets_json = self.get("assets")
        try:
            return json.loads(assets_json)
        except json.JSONDecodeError:
            logger.warning(f"Failed to parse assets JSON: {assets_json}")
            return json.loads(DEFAULTS["assets"])

    def get_percentiles(self) -> Tuple[str, str]:
        """Return (long_percentile, short_percentile)."""
        risk_preset = self.get("risk_preset")

        if risk_preset != "custom" and risk_preset in RISK_PRESETS:
            return RISK_PRESETS[risk_preset]

        return (
            self.get("long_percentile"),
            self.get("short_percentile"),
        )

    def get_optimal_poll_interval(self) -> int:
        """Calculate optimal poll interval in seconds."""
        override = self.get("poll_interval_override")
        if override > 0:
            return override

        credits_total = self.get("synth_credits_total")
        credits_used = self.get("synth_credits_used")

        now = datetime.utcnow()
        reset_day = self.get("synth_cycle_reset_day")

        if now.day >= reset_day:
            days_remaining = 31 - now.day + reset_day
        else:
            days_remaining = reset_day - now.day

        days_remaining = max(days_remaining, 1)

        daily_budget = (credits_total - credits_used) / days_remaining

        assets = self.get_active_assets()
        calls_per_poll = len(assets)

        if daily_budget <= 0:
            return 900

        polls_per_day = daily_budget / calls_per_poll
        if polls_per_day <= 0:
            return 900

        interval = 86400 / polls_per_day

        return max(60, min(900, int(interval)))

    def increment_credits_used(self, count: int = 1) -> None:
        """Atomically increment credits_used."""
        conn = self._get_conn()
        conn.execute(
            "UPDATE settings SET value = value + ? WHERE key = ?",
            (count, "synth_credits_used"),
        )
        conn.commit()

    def reset_credits_if_new_cycle(self) -> bool:
        """Check if new cycle and reset credits if needed. Returns True if reset."""
        now = datetime.utcnow()
        reset_day = self.get("synth_cycle_reset_day")

        last_reset_str = self.get("_last_credits_reset")
        if last_reset_str:
            try:
                last_reset = datetime.strptime(last_reset_str, "%Y-%m-%d")
                if last_reset.month == now.month and last_reset.year == now.year:
                    return False
            except ValueError:
                pass

        if now.day >= reset_day:
            conn = self._get_conn()
            conn.execute(
                "UPDATE settings SET value = 0 WHERE key = ?", ("synth_credits_used",)
            )
            conn.execute(
                "UPDATE settings SET value = ? WHERE key = ?",
                (now.strftime("%Y-%m-%d"), "_last_credits_reset"),
            )
            conn.commit()
            logger.info(f"Credits reset for new cycle (day {reset_day})")
            return True

        return False

    def get_budget_summary(self) -> Dict[str, Any]:
        """Get budget information summary."""
        credits_total = self.get("synth_credits_total")
        credits_used = self.get("synth_credits_used")
        reset_day = self.get("synth_cycle_reset_day")

        now = datetime.utcnow()
        if now.day >= reset_day:
            days_remaining = 31 - now.day + reset_day
        else:
            days_remaining = reset_day - now.day
        days_remaining = max(days_remaining, 1)

        daily_budget = (credits_total - credits_used) / days_remaining

        projected_total_usage = daily_budget * days_remaining

        return {
            "credits_used": credits_used,
            "credits_total": credits_total,
            "daily_budget": daily_budget,
            "days_remaining": days_remaining,
            "projected_total_usage": projected_total_usage,
            "will_exceed": projected_total_usage > credits_total,
        }
