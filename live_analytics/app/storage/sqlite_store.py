"""
SQLite storage layer for live analytics.

Uses WAL mode for concurrent read/write access.
All public functions accept a pathlib.Path to the DB file so that tests can
easily pass `:memory:` or a temp file.

A module-level connection cache (_pool) keeps one open connection per DB path
to avoid the overhead of opening/closing on every call (~40+ times/sec at
20 Hz ingest).  Connections use check_same_thread=False because the ingest
server may call from different async tasks on the same event loop thread.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Optional

from live_analytics.app.models import ScoringResult, SessionDetail, SessionSummary

logger = logging.getLogger("live_analytics.storage")

# ── Connection pool (one connection per DB path) ──────────────────────

_pool: dict[str, sqlite3.Connection] = {}
_pool_lock = threading.Lock()


def _connect(db_path: Path | str) -> sqlite3.Connection:
    key = str(db_path)
    conn = _pool.get(key)
    if conn is not None:
        return conn

    with _pool_lock:
        # Double-check after acquiring lock
        if key in _pool:
            return _pool[key]
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        conn.row_factory = sqlite3.Row
        _pool[key] = conn
        return conn


def close_pool() -> None:
    """Close all cached connections. Used by tests for clean teardown."""
    with _pool_lock:
        for key, conn in _pool.items():
            try:
                conn.close()
            except Exception:
                logger.debug("Error closing pooled connection %s (ignored)", key)
        _pool.clear()

# ── Schema DDL ────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id   TEXT PRIMARY KEY,
    start_unix_ms INTEGER NOT NULL,
    end_unix_ms  INTEGER,
    scenario_id  TEXT DEFAULT '',
    record_count INTEGER DEFAULT 0,
    latest_scores TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL,
    unix_ms      INTEGER NOT NULL,
    event_type   TEXT NOT NULL,
    payload      TEXT DEFAULT '{}',
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);
"""


def init_db(db_path: Path | str) -> None:
    """Create tables if they don't exist."""
    conn = _connect(db_path)
    conn.executescript(_DDL)
    conn.commit()
    logger.info("SQLite DB initialised at %s", db_path)


# ── Session CRUD ──────────────────────────────────────────────────────

def upsert_session(
    db_path: Path | str,
    session_id: str,
    start_unix_ms: int,
    scenario_id: str = "",
) -> None:
    """Insert or update session metadata."""
    conn = _connect(db_path)
    conn.execute(
        """
        INSERT INTO sessions (session_id, start_unix_ms, scenario_id)
        VALUES (?, ?, ?)
        ON CONFLICT(session_id) DO UPDATE SET scenario_id = excluded.scenario_id
        """,
        (session_id, start_unix_ms, scenario_id),
    )
    conn.commit()


def increment_record_count(db_path: Path | str, session_id: str, n: int = 1) -> None:
    conn = _connect(db_path)
    conn.execute(
        "UPDATE sessions SET record_count = record_count + ? WHERE session_id = ?",
        (n, session_id),
    )
    conn.commit()


def update_latest_scores(
    db_path: Path | str, session_id: str, scores: ScoringResult
) -> None:
    conn = _connect(db_path)
    conn.execute(
        "UPDATE sessions SET latest_scores = ? WHERE session_id = ?",
        (scores.model_dump_json(), session_id),
    )
    conn.commit()


def end_session(db_path: Path | str, session_id: str, end_unix_ms: int) -> None:
    conn = _connect(db_path)
    conn.execute(
        "UPDATE sessions SET end_unix_ms = ? WHERE session_id = ?",
        (end_unix_ms, session_id),
    )
    conn.commit()


def list_sessions(db_path: Path | str) -> list[SessionSummary]:
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT session_id, start_unix_ms, end_unix_ms, scenario_id, record_count "
        "FROM sessions ORDER BY start_unix_ms DESC"
    ).fetchall()
    return [
        SessionSummary(
            session_id=r["session_id"],
            start_unix_ms=r["start_unix_ms"],
            end_unix_ms=r["end_unix_ms"],
            scenario_id=r["scenario_id"],
            record_count=r["record_count"],
        )
        for r in rows
    ]


def get_session(db_path: Path | str, session_id: str) -> Optional[SessionDetail]:
    conn = _connect(db_path)
    r = conn.execute(
        "SELECT session_id, start_unix_ms, end_unix_ms, scenario_id, "
        "record_count, latest_scores FROM sessions WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    if r is None:
        return None
    scores_raw = json.loads(r["latest_scores"]) if r["latest_scores"] else {}
    return SessionDetail(
        session_id=r["session_id"],
        start_unix_ms=r["start_unix_ms"],
        end_unix_ms=r["end_unix_ms"],
        scenario_id=r["scenario_id"],
        record_count=r["record_count"],
        latest_scores=ScoringResult(**scores_raw) if scores_raw else None,
    )


# ── Events ────────────────────────────────────────────────────────────

def insert_event(
    db_path: Path | str,
    session_id: str,
    unix_ms: int,
    event_type: str,
    payload: dict | None = None,
) -> None:
    conn = _connect(db_path)
    conn.execute(
        "INSERT INTO events (session_id, unix_ms, event_type, payload) VALUES (?, ?, ?, ?)",
        (session_id, unix_ms, event_type, json.dumps(payload or {})),
    )
    conn.commit()


def get_recent_events(
    db_path: Path | str, session_id: str, limit: int = 50
) -> list[dict]:
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT id, session_id, unix_ms, event_type, payload "
        "FROM events WHERE session_id = ? ORDER BY unix_ms DESC LIMIT ?",
        (session_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]
