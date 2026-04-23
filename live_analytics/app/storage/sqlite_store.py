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
        try:
            conn = sqlite3.connect(str(db_path), check_same_thread=False)
        except sqlite3.OperationalError as exc:
            logger.critical(
                "Cannot open SQLite database at '%s': %s  "
                "(Check that the directory exists and the process has write permission.)",
                db_path, exc,
            )
            raise
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        conn.row_factory = sqlite3.Row
        _pool[key] = conn
        logger.debug("Opened new SQLite connection to '%s'", db_path)
        return conn


def close_pool() -> None:
    """Close all cached connections. Used by tests for clean teardown."""
    with _pool_lock:
        for key, conn in _pool.items():
            try:
                conn.close()
            except Exception as exc:
                logger.debug("Error closing pooled connection '%s' (ignored): %s", key, exc)
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
    try:
        conn = _connect(db_path)
        conn.executescript(_DDL)
        conn.commit()
    except sqlite3.Error as exc:
        logger.critical(
            "Failed to initialise database schema at '%s': %s – "
            "the analytics service cannot start without a working database.",
            db_path, exc,
        )
        raise
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
    results: list[SessionSummary] = []
    for r in rows:
        try:
            results.append(
                SessionSummary(
                    session_id=r["session_id"],
                    start_unix_ms=r["start_unix_ms"],
                    end_unix_ms=r["end_unix_ms"],
                    scenario_id=r["scenario_id"] or "",
                    record_count=r["record_count"] or 0,
                )
            )
        except Exception as exc:
            logger.warning(
                "Skipping malformed session row '%s' in '%s': %s: %s",
                r["session_id"] if "session_id" in r.keys() else "?",
                db_path, type(exc).__name__, exc,
            )
    return results


def get_session(db_path: Path | str, session_id: str) -> Optional[SessionDetail]:
    conn = _connect(db_path)
    r = conn.execute(
        "SELECT session_id, start_unix_ms, end_unix_ms, scenario_id, "
        "record_count, latest_scores FROM sessions WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    if r is None:
        return None
    try:
        scores_raw = json.loads(r["latest_scores"]) if r["latest_scores"] else {}
    except (json.JSONDecodeError, TypeError) as exc:
        logger.warning(
            "Malformed latest_scores JSON for session '%s' in '%s': %s – treating as empty",
            session_id, db_path, exc,
        )
        scores_raw = {}
    try:
        return SessionDetail(
            session_id=r["session_id"],
            start_unix_ms=r["start_unix_ms"],
            end_unix_ms=r["end_unix_ms"],
            scenario_id=r["scenario_id"] or "",
            record_count=r["record_count"] or 0,
            latest_scores=ScoringResult(**scores_raw) if scores_raw else None,
        )
    except Exception as exc:
        logger.error(
            "Failed to construct SessionDetail for session '%s' in '%s': %s: %s  "
            "(row data: start=%r end=%r scenario=%r records=%r)",
            session_id, db_path, type(exc).__name__, exc,
            r["start_unix_ms"], r["end_unix_ms"], r["scenario_id"], r["record_count"],
        )
        raise


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
