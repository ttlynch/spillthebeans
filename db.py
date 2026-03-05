"""SQLite helpers for trading data."""

import json
import sqlite3
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any
import logging

logger = logging.getLogger(__name__)

SCHEMA_SIGNALS = """
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset TEXT NOT NULL,
    direction TEXT NOT NULL,
    entry REAL NOT NULL,
    tp REAL NOT NULL,
    sl REAL NOT NULL,
    win_rate REAL NOT NULL,
    vol_spread REAL NOT NULL,
    timestamp TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    percentiles_snapshot TEXT
);
"""

SCHEMA_POSITIONS = """
CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER NOT NULL,
    asset TEXT NOT NULL,
    direction TEXT NOT NULL,
    size_usd REAL NOT NULL,
    entry_price REAL NOT NULL,
    status TEXT DEFAULT 'open',
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    pnl REAL,
    FOREIGN KEY (signal_id) REFERENCES signals(id)
);
"""


def init_db(db_path: str = "data/trading.db") -> sqlite3.Connection:
    """Initialize database and create tables.

    Creates data/ directory if it doesn't exist.

    Args:
        db_path: Path to SQLite database file

    Returns:
        Database connection
    """
    db_file = Path(db_path)
    db_file.parent.mkdir(parents=True, exist_ok=True)

    logger.info(f"Initializing database at {db_path}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    cursor = conn.cursor()
    cursor.execute(SCHEMA_SIGNALS)
    cursor.execute(SCHEMA_POSITIONS)
    conn.commit()

    logger.info("Database initialized successfully")
    return conn


def save_signal(conn: sqlite3.Connection, signal, status: str = "pending") -> int:
    """Save signal to database.

    Args:
        conn: Database connection
        signal: Signal object
        status: Signal status (default: "pending")

    Returns:
        signal_id
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO signals 
        (asset, direction, entry, tp, sl, win_rate, vol_spread, timestamp, status, percentiles_snapshot)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            signal.asset,
            signal.direction,
            signal.entry_price,
            signal.take_profit,
            signal.stop_loss,
            signal.win_rate,
            signal.vol_spread_pct,
            signal.timestamp.isoformat(),
            status,
            json.dumps(signal.percentiles_snapshot),
        ),
    )
    conn.commit()

    signal_id = cursor.lastrowid
    logger.info(f"Saved signal {signal_id} for {signal.asset} {signal.direction}")
    return signal_id


def save_position(
    conn: sqlite3.Connection,
    signal_id: int,
    asset: str,
    direction: str,
    size_usd: float,
    entry_price: float,
) -> int:
    """Save position to database.

    Args:
        conn: Database connection
        signal_id: Associated signal ID
        asset: Asset symbol
        direction: "long" or "short"
        size_usd: Position size in USD
        entry_price: Entry price

    Returns:
        position_id
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO positions
        (signal_id, asset, direction, size_usd, entry_price, status, opened_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            signal_id,
            asset,
            direction,
            size_usd,
            entry_price,
            "open",
            datetime.utcnow().isoformat(),
        ),
    )
    conn.commit()

    position_id = cursor.lastrowid
    logger.info(f"Saved position {position_id} for {asset} {direction}")
    return position_id


def update_position(
    conn: sqlite3.Connection, position_id: int, status: str, closed_at: str, pnl: float
) -> None:
    """Update position status and PnL.

    Args:
        conn: Database connection
        position_id: Position ID
        status: New status
        closed_at: Closing timestamp
        pnl: Profit/loss
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE positions
        SET status = ?, closed_at = ?, pnl = ?
        WHERE id = ?
        """,
        (status, closed_at, pnl, position_id),
    )
    conn.commit()

    logger.info(f"Updated position {position_id}: status={status}, pnl={pnl}")


def get_open_positions(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """Get all open positions.

    Args:
        conn: Database connection

    Returns:
        List of position dictionaries
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT * FROM positions
        WHERE status = 'open'
        ORDER BY opened_at DESC
        """
    )

    rows = cursor.fetchall()
    return [dict(row) for row in rows]


def get_signal_history(
    conn: sqlite3.Connection, limit: int = 100
) -> List[Dict[str, Any]]:
    """Get signal history.

    Args:
        conn: Database connection
        limit: Maximum number of signals to return

    Returns:
        List of signal dictionaries
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT * FROM signals
        ORDER BY timestamp DESC
        LIMIT ?
        """,
        (limit,),
    )

    rows = cursor.fetchall()
    signals = []
    for row in rows:
        signal_dict = dict(row)
        if signal_dict.get("percentiles_snapshot"):
            signal_dict["percentiles_snapshot"] = json.loads(
                signal_dict["percentiles_snapshot"]
            )
        signals.append(signal_dict)

    return signals


def update_signal_status(conn: sqlite3.Connection, signal_id: int, status: str) -> None:
    """Update signal status.

    Args:
        conn: Database connection
        signal_id: Signal ID
        status: New status ('pending', 'executed', 'passed')
    """
    cursor = conn.cursor()
    cursor.execute("UPDATE signals SET status = ? WHERE id = ?", (status, signal_id))
    conn.commit()
    logger.info(f"Updated signal {signal_id} status to {status}")
