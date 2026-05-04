"""
SQLite storage for the questionnaire service.

Schema
------
participants     – test-person registry (links to analytics sessions)
questionnaire_responses – individual answers, keyed by (participant_id, phase, question_id)
                          phase = "pre" | "post" (before/after cycling)

Resume support: every answer is persisted immediately.  When the user
comes back after cycling they simply open the same participant_id and
the frontend loads all previously saved answers so they can continue.

Connection pooling
------------------
Like the analytics sqlite_store, this module keeps one open connection per
DB path.  Opening a new connection for every API call would add ~1 ms of
overhead per request and is unnecessary for a single-process service.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("questionnaire.db")

# ── Connection pool ───────────────────────────────────────────────────

_pool: dict[str, sqlite3.Connection] = {}
_pool_lock = threading.Lock()


def _connect(db_path: Path | str) -> sqlite3.Connection:
    """Return (or create) a cached SQLite connection for *db_path*.

    The outer check-without-lock is intentional: under normal operation the
    pool already holds the connection after the first request and we avoid
    acquiring the lock on every call.  The inner double-check (``if key in
    _pool``) prevents two threads that both saw ``None`` on the outer read
    from both creating a new connection — only the first one through the lock
    proceeds to open the DB.
    """
    key = str(db_path)
    conn = _pool.get(key)
    if conn is not None:
        return conn
    with _pool_lock:
        if key in _pool:
            return _pool[key]
        try:
            conn = sqlite3.connect(str(db_path), check_same_thread=False)
        except sqlite3.OperationalError as exc:
            logger.critical(
                "Cannot open questionnaire SQLite database at '%s': %s  "
                "(Check that the directory exists and is writable.)",
                db_path, exc,
            )
            raise
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        conn.row_factory = sqlite3.Row
        _pool[key] = conn
        logger.debug("Opened new questionnaire DB connection to '%s'", db_path)
        return conn


def close_pool() -> None:
    """Close all cached connections. Used by tests for clean teardown."""
    with _pool_lock:
        for key, conn in _pool.items():
            try:
                conn.close()
            except Exception as exc:
                logger.debug("Error closing questionnaire DB connection '%s' (ignored): %s", key, exc)
        _pool.clear()


# ── DDL ───────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS participants (
    participant_id  TEXT PRIMARY KEY,
    display_name    TEXT DEFAULT '',
    session_id      TEXT DEFAULT '',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    metadata        TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS questionnaire_responses (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    participant_id  TEXT NOT NULL,
    phase           TEXT NOT NULL DEFAULT 'pre',
    question_id     TEXT NOT NULL,
    answer          TEXT NOT NULL DEFAULT '',
    answered_at     TEXT NOT NULL,
    FOREIGN KEY (participant_id) REFERENCES participants(participant_id),
    UNIQUE(participant_id, phase, question_id)
);

CREATE INDEX IF NOT EXISTS idx_resp_participant
    ON questionnaire_responses(participant_id, phase);

CREATE TABLE IF NOT EXISTS pulse_data (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT    NOT NULL,
    participant_id  TEXT,
    unix_ms         INTEGER NOT NULL,
    pulse           INTEGER NOT NULL,
    created_at      TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pulse_session
    ON pulse_data(session_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Init ──────────────────────────────────────────────────────────────

def init_db(db_path: Path | str) -> None:
    try:
        conn = _connect(db_path)
        conn.executescript(_DDL)
        conn.commit()
    except sqlite3.Error as exc:
        logger.critical(
            "Failed to initialise questionnaire DB schema at '%s': %s – "
            "the questionnaire service cannot start without a working database.",
            db_path, exc,
        )
        raise
    logger.info("Questionnaire DB initialised at %s", db_path)


# ── Participants ──────────────────────────────────────────────────────

def create_participant(
    db_path: Path | str,
    participant_id: str,
    display_name: str = "",
    session_id: str = "",
    metadata: dict | None = None,
) -> dict:
    now = _now()
    conn = _connect(db_path)
    conn.execute(
        """INSERT INTO participants (participant_id, display_name, session_id, created_at, updated_at, metadata)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(participant_id) DO UPDATE SET
               display_name = excluded.display_name,
               session_id = CASE WHEN excluded.session_id != '' THEN excluded.session_id ELSE participants.session_id END,
               updated_at = excluded.updated_at,
               metadata = excluded.metadata""",
        (participant_id, display_name, session_id, now, now, json.dumps(metadata or {})),
    )
    conn.commit()
    return get_participant(db_path, participant_id)  # type: ignore[return-value]


def get_participant(db_path: Path | str, participant_id: str) -> Optional[dict]:
    conn = _connect(db_path)
    row = conn.execute(
        "SELECT * FROM participants WHERE participant_id = ?", (participant_id,)
    ).fetchone()
    return dict(row) if row else None


def list_participants(db_path: Path | str) -> list[dict]:
    conn = _connect(db_path)
    rows = conn.execute("SELECT * FROM participants ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


def get_participant_by_session(db_path: Path | str, session_id: str) -> Optional[dict]:
    """Return the participant linked to an analytics session_id, or None."""
    conn = _connect(db_path)
    row = conn.execute(
        "SELECT * FROM participants WHERE session_id = ? LIMIT 1", (session_id,)
    ).fetchone()
    return dict(row) if row else None


def link_session(db_path: Path | str, participant_id: str, session_id: str) -> None:
    conn = _connect(db_path)
    conn.execute(
        "UPDATE participants SET session_id = ?, updated_at = ? WHERE participant_id = ?",
        (session_id, _now(), participant_id),
    )
    conn.commit()


# ── Responses ─────────────────────────────────────────────────────────

def save_answer(
    db_path: Path | str,
    participant_id: str,
    phase: str,
    question_id: str,
    answer: Any,
) -> None:
    """Upsert a single answer (auto-saves / resume-friendly)."""
    conn = _connect(db_path)
    try:
        conn.execute(
            """INSERT INTO questionnaire_responses (participant_id, phase, question_id, answer, answered_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(participant_id, phase, question_id)
               DO UPDATE SET answer = excluded.answer, answered_at = excluded.answered_at""",
            (participant_id, phase, question_id, json.dumps(answer), _now()),
        )
        conn.commit()
    except sqlite3.Error as exc:
        logger.error(
            "DB error saving answer participant='%s' phase='%s' question='%s': %s",
            participant_id, phase, question_id, exc,
        )
        raise


def save_answers_bulk(
    db_path: Path | str,
    participant_id: str,
    phase: str,
    answers: dict[str, Any],
) -> None:
    """Upsert many answers at once using executemany (one round-trip)."""
    now = _now()
    conn = _connect(db_path)
    try:
        conn.executemany(
            """INSERT INTO questionnaire_responses (participant_id, phase, question_id, answer, answered_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(participant_id, phase, question_id)
               DO UPDATE SET answer = excluded.answer, answered_at = excluded.answered_at""",
            [(participant_id, phase, qid, json.dumps(val), now) for qid, val in answers.items()],
        )
        conn.commit()
    except sqlite3.Error as exc:
        logger.error(
            "DB error bulk-saving %d answers participant='%s' phase='%s': %s",
            len(answers), participant_id, phase, exc,
        )
        raise


def get_answers(
    db_path: Path | str,
    participant_id: str,
    phase: str | None = None,
) -> list[dict]:
    """Load saved answers for resume.  If phase is None, returns all phases."""
    conn = _connect(db_path)
    if phase:
        rows = conn.execute(
            "SELECT * FROM questionnaire_responses WHERE participant_id = ? AND phase = ? ORDER BY question_id",
            (participant_id, phase),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM questionnaire_responses WHERE participant_id = ? ORDER BY phase, question_id",
            (participant_id,),
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        try:
            d["answer"] = json.loads(d["answer"])
        except (json.JSONDecodeError, TypeError):
            pass
        result.append(d)
    return result


def get_progress(db_path: Path | str, participant_id: str) -> dict:
    """Return count of answers per phase."""
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT phase, COUNT(*) as cnt FROM questionnaire_responses WHERE participant_id = ? GROUP BY phase",
        (participant_id,),
    ).fetchall()
    return {r["phase"]: r["cnt"] for r in rows}


def delete_participant_data(db_path: Path | str, participant_id: str) -> None:
    """Delete all data for a participant (GDPR-friendly)."""
    conn = _connect(db_path)
    conn.execute("DELETE FROM questionnaire_responses WHERE participant_id = ?", (participant_id,))
    conn.execute("DELETE FROM participants WHERE participant_id = ?", (participant_id,))
    conn.commit()


# ── Pulse data ────────────────────────────────────────────────────────

def insert_pulse_data(
    db_path: Path | str,
    session_id: str,
    unix_ms: int,
    pulse: int,
) -> dict:
    """Persist one heart-rate sample to the ``pulse_data`` table.

    Automatically resolves the ``participant_id`` by looking up which
    participant is currently linked to *session_id*.  If no participant has
    been linked to this session yet, ``participant_id`` is stored as NULL —
    the row can be updated later when the questionnaire is filled in.

    Parameters
    ----------
    db_path    : path to the questionnaire SQLite database.
    session_id : the analytics session identifier (from Unity).
    unix_ms    : wall-clock timestamp of the sample (ms since Unix epoch, UTC).
    pulse      : heart-rate in beats per minute.  Values ≤ 0 are rejected with
                 a ValueError so callers can guard against missing sensors.

    Returns
    -------
    dict with keys ``id``, ``session_id``, ``participant_id``, ``unix_ms``,
    ``pulse``, ``created_at``.

    Raises
    ------
    ValueError  if ``pulse <= 0`` (invalid reading — caller should skip).
    sqlite3.Error on genuine DB failures.
    """
    if pulse <= 0:
        raise ValueError(
            f"insert_pulse_data: pulse={pulse} is not a valid heart-rate value "
            f"(session_id={session_id!r}). Zero/negative values indicate a "
            "missing or disconnected sensor — call not persisted."
        )

    # Resolve participant for this session (may be None if not yet linked).
    conn = _connect(db_path)
    row = conn.execute(
        "SELECT participant_id FROM participants WHERE session_id = ? LIMIT 1",
        (session_id,),
    ).fetchone()
    participant_id: str | None = row["participant_id"] if row else None

    now = _now()
    cur = conn.execute(
        "INSERT INTO pulse_data (session_id, participant_id, unix_ms, pulse, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (session_id, participant_id, unix_ms, pulse, now),
    )
    conn.commit()
    row_id = cur.lastrowid
    return {
        "id": row_id,
        "session_id": session_id,
        "participant_id": participant_id,
        "unix_ms": unix_ms,
        "pulse": pulse,
        "created_at": now,
    }


def get_pulse_data(
    db_path: Path | str,
    session_id: str,
    limit: int = 500,
) -> list[dict]:
    """Return heart-rate samples for *session_id*, newest first.

    Parameters
    ----------
    db_path    : path to the questionnaire SQLite database.
    session_id : the analytics session to query.
    limit      : maximum number of rows to return (default 500).
    """
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT id, session_id, participant_id, unix_ms, pulse, created_at"
        " FROM pulse_data WHERE session_id = ? ORDER BY unix_ms DESC LIMIT ?",
        (session_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]

