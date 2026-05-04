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
    # Fast path: read under lock to avoid TOCTOU race between the initial
    # unsynchronised get() and the double-check inside the lock.
    with _pool_lock:
        conn = _pool.get(key)
        if conn is not None:
            return conn
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

CREATE TABLE IF NOT EXISTS pulse_data (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT    NOT NULL,
    unix_ms      INTEGER NOT NULL,
    user_id      INTEGER,
    pulse        INTEGER NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

CREATE INDEX IF NOT EXISTS idx_pulse_data_session ON pulse_data(session_id);
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
    """Insert or update session metadata.

    On conflict (same session_id), updates both scenario_id AND start_unix_ms
    so that a session re-used after a Unity crash reflects the correct start
    time rather than carrying a stale value from the previous run.
    """
    conn = _connect(db_path)
    conn.execute(
        """
        INSERT INTO sessions (session_id, start_unix_ms, scenario_id)
        VALUES (?, ?, ?)
        ON CONFLICT(session_id) DO UPDATE SET
            start_unix_ms = excluded.start_unix_ms,
            scenario_id   = excluded.scenario_id
        """,
        (session_id, start_unix_ms, scenario_id),
    )
    conn.commit()


def increment_record_count(db_path: Path | str, session_id: str, n: int = 1) -> None:
    """Atomically add *n* to the session's ``record_count`` column.

    This is an UPDATE rather than a read-modify-write so it is safe to call
    from multiple coroutines on the same event-loop thread (SQLite serialises
    the write internally).  Called once per received batch rather than once
    per record to minimise round-trips.
    """
    conn = _connect(db_path)
    conn.execute(
        "UPDATE sessions SET record_count = record_count + ? WHERE session_id = ?",
        (n, session_id),
    )
    conn.commit()


def update_latest_scores(
    db_path: Path | str, session_id: str, scores: ScoringResult
) -> None:
    """Persist the latest scoring snapshot for a session.

    Stored as a JSON string in ``latest_scores`` so the API can return it
    without re-running the scoring engine.  Written every
    ``_SCORE_PERSIST_EVERY`` records (20 by default) — not on every batch —
    to reduce write amplification.
    """
    conn = _connect(db_path)
    conn.execute(
        "UPDATE sessions SET latest_scores = ? WHERE session_id = ?",
        (scores.model_dump_json(), session_id),
    )
    conn.commit()


def end_session(db_path: Path | str, session_id: str, end_unix_ms: int) -> None:
    """Record the wall-clock end time of a session.

    Called when Unity sends an explicit session-end event.  If the process
    crashes before this is called, ``end_unix_ms`` will remain NULL in the DB;
    callers should treat NULL as "session still active or ended abnormally".
    """
    conn = _connect(db_path)
    conn.execute(
        "UPDATE sessions SET end_unix_ms = ? WHERE session_id = ?",
        (end_unix_ms, session_id),
    )
    conn.commit()


def list_sessions(db_path: Path | str) -> list[SessionSummary]:
    """Return all sessions ordered newest-first.

    Malformed rows (e.g. corrupt Pydantic fields from an old schema) are
    logged and skipped so a single bad row never breaks the dashboard list.
    """
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
    """Return full session detail including the last persisted scoring snapshot.

    Returns None when the session does not exist.  Malformed ``latest_scores``
    JSON (e.g. from a schema migration) is treated as an empty score rather
    than raising, so the API can still return basic session metadata.
    """
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


# ── Pulse data ────────────────────────────────────────────────────────

def insert_pulse_data(
    db_path: Path | str,
    session_id: str,
    unix_ms: int,
    pulse: int,
    user_id: Optional[int] = None,
) -> None:
    """Persist a single heart-rate sample to the ``pulse_data`` table.

    Parameters
    ----------
    db_path    : path to the SQLite database file.
    session_id : opaque session identifier (matches ``sessions.session_id``).
    unix_ms    : wall-clock timestamp of the sample in milliseconds since epoch.
    pulse      : heart-rate in beats per minute (integer).  Values ≤ 0 are
                 silently rejected — they indicate a missing or invalid reading.
    user_id    : optional foreign key to a ``Users`` table; pass ``None``
                 when user identity is unavailable (column stored as NULL).

    Raises
    ------
    sqlite3.Error
        Propagated on genuine DB failures so the caller can decide whether
        to log-and-continue or bubble up.
    """
    if pulse <= 0:
        logger.debug(
            "insert_pulse_data: ignoring non-positive pulse=%d for session %s",
            pulse, session_id,
        )
        return
    conn = _connect(db_path)
    conn.execute(
        "INSERT INTO pulse_data (session_id, unix_ms, user_id, pulse) VALUES (?, ?, ?, ?)",
        (session_id, unix_ms, user_id, pulse),
    )
    conn.commit()


def get_pulse_data(
    db_path: Path | str, session_id: str, limit: int = 500
) -> list[dict]:
    """Return heart-rate samples for *session_id*, newest first.

    Parameters
    ----------
    db_path    : path to the SQLite database file.
    session_id : the session to query.
    limit      : maximum number of rows to return (default 500).

    Returns
    -------
    list of dicts with keys: ``id``, ``session_id``, ``unix_ms``,
    ``user_id``, ``pulse``.
    """
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT id, session_id, unix_ms, user_id, pulse "
        "FROM pulse_data WHERE session_id = ? ORDER BY unix_ms DESC LIMIT ?",
        (session_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]
