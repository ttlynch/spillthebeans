"""SQLite helpers for trading data."""

import json
import sqlite3
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)

SCHEMA_SETTINGS = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

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
    percentiles_snapshot TEXT,
    strength INTEGER DEFAULT 0
);
"""

SCHEMA_POSITIONS = """
CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER NOT NULL,
    asset TEXT NOT NULL,
    direction TEXT NOT NULL,
    size_usd REAL NOT NULL,
    size_tokens REAL NOT NULL,
    entry_price REAL NOT NULL,
    tp_price REAL NOT NULL,
    sl_price REAL NOT NULL,
    tp_order_id INTEGER,
    status TEXT DEFAULT 'open',
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    exit_price REAL,
    exit_reason TEXT,
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
    cursor.execute(SCHEMA_SETTINGS)
    cursor.execute(SCHEMA_SIGNALS)
    cursor.execute(SCHEMA_POSITIONS)

    try:
        cursor.execute("SELECT strength FROM signals LIMIT 1")
    except sqlite3.OperationalError:
        cursor.execute("ALTER TABLE signals ADD COLUMN strength INTEGER DEFAULT 0")
        logger.info("Added strength column to signals table")

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
        (asset, direction, entry, tp, sl, win_rate, vol_spread, timestamp, status, percentiles_snapshot, strength)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            getattr(signal, "strength", 0),
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
    size_tokens: float,
    entry_price: float,
    tp_price: float,
    sl_price: float,
    tp_order_id: Optional[int] = None,
) -> int:
    """Save position to database.

    Args:
        conn: Database connection
        signal_id: Associated signal ID
        asset: Asset symbol
        direction: "long" or "short"
        size_usd: Position size in USD
        size_tokens: Position size in tokens
        entry_price: Entry price
        tp_price: Take profit price
        sl_price: Stop loss price
        tp_order_id: Optional TP order ID

    Returns:
        position_id
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO positions
        (signal_id, asset, direction, size_usd, size_tokens, entry_price, tp_price, sl_price, tp_order_id, status, opened_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            signal_id,
            asset,
            direction,
            size_usd,
            size_tokens,
            entry_price,
            tp_price,
            sl_price,
            tp_order_id,
            "open",
            datetime.utcnow().isoformat(),
        ),
    )
    conn.commit()

    position_id = cursor.lastrowid
    logger.info(f"Saved position {position_id} for {asset} {direction}")
    return position_id


def update_position(
    conn: sqlite3.Connection,
    position_id: int,
    status: str,
    closed_at: str,
    pnl: float,
    exit_price: Optional[float] = None,
    exit_reason: Optional[str] = None,
) -> None:
    """Update position status and PnL.

    Args:
        conn: Database connection
        position_id: Position ID
        status: New status
        closed_at: Closing timestamp
        pnl: Profit/loss
        exit_price: Exit price (optional)
        exit_reason: Reason for exit (optional)
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE positions
        SET status = ?, closed_at = ?, pnl = ?, exit_price = ?, exit_reason = ?
        WHERE id = ?
        """,
        (status, closed_at, pnl, exit_price, exit_reason, position_id),
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


def get_closed_positions(
    conn: sqlite3.Connection, limit: int = 10
) -> List[Dict[str, Any]]:
    """Get last N closed positions.

    Args:
        conn: Database connection
        limit: Maximum number of positions to return

    Returns:
        List of position dictionaries
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT * FROM positions
        WHERE status = 'closed'
        ORDER BY closed_at DESC
        LIMIT ?
        """,
        (limit,),
    )

    rows = cursor.fetchall()
    return [dict(row) for row in rows]


def get_position_stats(conn: sqlite3.Connection) -> Dict[str, Any]:
    """Get all-time position statistics.

    Args:
        conn: Database connection

    Returns:
        Dict with total_pnl, win_count, total_count, avg_duration_min
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT 
            COALESCE(SUM(pnl), 0) as total_pnl,
            COUNT(*) as total_count,
            SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as win_count
        FROM positions
        WHERE status = 'closed'
        """
    )

    row = cursor.fetchone()
    if not row or row["total_count"] == 0:
        return {
            "total_pnl": 0.0,
            "win_count": 0,
            "total_count": 0,
            "avg_duration_min": 0,
        }

    cursor.execute(
        """
        SELECT AVG(
            (julianday(closed_at) - julianday(opened_at)) * 24 * 60
        ) as avg_duration
        FROM positions
        WHERE status = 'closed'
        """
    )

    duration_row = cursor.fetchone()
    avg_duration = duration_row["avg_duration"] if duration_row["avg_duration"] else 0

    return {
        "total_pnl": row["total_pnl"],
        "win_count": row["win_count"],
        "total_count": row["total_count"],
        "avg_duration_min": int(avg_duration) if avg_duration else 0,
    }


def get_signal_by_id(
    conn: sqlite3.Connection, signal_id: int
) -> Optional[Dict[str, Any]]:
    """Get signal by ID.

    Args:
        conn: Database connection
        signal_id: Signal ID

    Returns:
        Signal dictionary or None
    """
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM signals WHERE id = ?", (signal_id,))
    row = cursor.fetchone()
    return dict(row) if row else None
