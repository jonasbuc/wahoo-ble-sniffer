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
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("questionnaire.db")

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
"""


def _connect(db_path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.row_factory = sqlite3.Row
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Init ──────────────────────────────────────────────────────────────

def init_db(db_path: Path | str) -> None:
    conn = _connect(db_path)
    try:
        conn.executescript(_DDL)
        conn.commit()
    finally:
        conn.close()
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
    try:
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
    finally:
        conn.close()
    return get_participant(db_path, participant_id)  # type: ignore[return-value]


def get_participant(db_path: Path | str, participant_id: str) -> Optional[dict]:
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM participants WHERE participant_id = ?", (participant_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_participants(db_path: Path | str) -> list[dict]:
    conn = _connect(db_path)
    try:
        rows = conn.execute("SELECT * FROM participants ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def link_session(db_path: Path | str, participant_id: str, session_id: str) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            "UPDATE participants SET session_id = ?, updated_at = ? WHERE participant_id = ?",
            (session_id, _now(), participant_id),
        )
        conn.commit()
    finally:
        conn.close()


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
    finally:
        conn.close()


def save_answers_bulk(
    db_path: Path | str,
    participant_id: str,
    phase: str,
    answers: dict[str, Any],
) -> None:
    """Upsert many answers at once."""
    now = _now()
    conn = _connect(db_path)
    try:
        for qid, val in answers.items():
            conn.execute(
                """INSERT INTO questionnaire_responses (participant_id, phase, question_id, answer, answered_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(participant_id, phase, question_id)
                   DO UPDATE SET answer = excluded.answer, answered_at = excluded.answered_at""",
                (participant_id, phase, qid, json.dumps(val), now),
            )
        conn.commit()
    finally:
        conn.close()


def get_answers(
    db_path: Path | str,
    participant_id: str,
    phase: str | None = None,
) -> list[dict]:
    """Load saved answers for resume.  If phase is None, returns all phases."""
    conn = _connect(db_path)
    try:
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
    finally:
        conn.close()


def get_progress(db_path: Path | str, participant_id: str) -> dict:
    """Return count of answers per phase."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT phase, COUNT(*) as cnt FROM questionnaire_responses WHERE participant_id = ? GROUP BY phase",
            (participant_id,),
        ).fetchall()
        return {r["phase"]: r["cnt"] for r in rows}
    finally:
        conn.close()


def delete_participant_data(db_path: Path | str, participant_id: str) -> None:
    """Delete all data for a participant (GDPR-friendly)."""
    conn = _connect(db_path)
    try:
        conn.execute("DELETE FROM questionnaire_responses WHERE participant_id = ?", (participant_id,))
        conn.execute("DELETE FROM participants WHERE participant_id = ?", (participant_id,))
        conn.commit()
    finally:
        conn.close()
