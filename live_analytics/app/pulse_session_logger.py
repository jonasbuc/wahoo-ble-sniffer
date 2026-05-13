"""
PulseSessionLogger – dedicated per-participant pulse log files.

Each participant's active cycling session gets its own JSONL file under
``PULSE_LOG_DIR`` (default ``<repo>/logs/pulse/``).  The file name encodes
both the participant ID and the session-start timestamp so files are
self-describing and never overwrite each other:

    logs/pulse/<participant_id>_<YYYYMMDD_HHMMSS>_pulse_log.jsonl

File format
-----------
Each line is a JSON object with a ``type`` field:

    {"type": "session_start", "participant_id": "TP_001", "session_id": "…",
     "started_at": "2025-01-01T10:00:00+00:00", "local_time": "…"}

    {"type": "pulse", "participant_id": "TP_001", "session_id": "…",
     "unix_ms": 1735000000000, "pulse": 87, "recorded_at": "…"}

    {"type": "session_end", "participant_id": "TP_001", "session_id": "…",
     "ended_at": "2025-01-01T10:45:00+00:00", "local_time": "…",
     "record_count": 540}

Edge cases
----------
- No active participant: ``write_pulse`` / ``close_session`` log a warning and
  return silently.
- New ``start_session`` while a session is already open for that participant:
  the old session is closed automatically with a ``session_end`` marker before
  the new one is opened.
- Unity crash / missing ``close_session``: the file is left open (no end marker).
  ``start_session`` detects this and auto-closes.
- Multiple participants may have simultaneous active sessions (different IDs).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

_TZ = ZoneInfo("Europe/Copenhagen")

logger = logging.getLogger("live_analytics.pulse_session_logger")


class _ActiveSession:
    """Internal bookkeeping for one open pulse-log session."""

    __slots__ = ("participant_id", "session_id", "log_path", "_fh", "record_count")

    def __init__(
        self,
        participant_id: str,
        session_id: str,
        log_path: Path,
    ) -> None:
        self.participant_id = participant_id
        self.session_id = session_id
        self.log_path = log_path
        self.record_count = 0
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = log_path.open("a", encoding="utf-8")

    def write(self, record: dict[str, Any]) -> None:
        self._fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._fh.flush()

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass


class PulseSessionLogger:
    """Manages per-participant pulse log files.

    A single shared instance (``_pulse_session_logger`` at module level) is
    used across the analytics server.  Unit tests may create isolated instances
    by passing an explicit ``log_dir``.

    Thread / async safety
    ---------------------
    The analytics server runs in a single asyncio event loop.  All mutations
    happen inside that loop so no explicit locking is required.  Do NOT call
    these methods from a separate thread without external synchronisation.
    """

    def __init__(self, log_dir: Path | str) -> None:
        self._log_dir = Path(log_dir)
        # participant_id → _ActiveSession
        self._sessions: dict[str, _ActiveSession] = {}

    # ── Public API ────────────────────────────────────────────────────

    def start_session(
        self,
        participant_id: str,
        session_id: str,
        *,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Open a new pulse log file for *participant_id*.

        If a session is already open for this participant it is closed first
        (auto-close with a ``session_end`` marker) so the old file is tidy.

        Parameters
        ----------
        participant_id:
            Questionnaire / test-person ID (e.g. ``"TP_001"``).
        session_id:
            Unity session ID (unix-ms string) that started this session.
        extra:
            Optional additional fields merged into the ``session_start`` record
            (e.g. ``{"scenario_id": "forest_01"}``).
        """
        if not participant_id or not session_id:
            logger.warning(
                "PulseSessionLogger.start_session: invalid arguments "
                "(participant_id=%r, session_id=%r) — ignoring",
                participant_id, session_id,
            )
            return

        # ── Idempotency guard: same participant + same session_id ─────
        # _resolve_and_link_participant may be called twice for the same
        # session (e.g. a trigger_relink fires while the original retry
        # loop is between iterations).  The _resolve_running guard in
        # ws_ingest prevents duplicate tasks, but defensive handling here
        # ensures that even if start_session is somehow called twice, the
        # second call is a clean no-op rather than auto-closing and
        # re-opening the log file (which would produce a spurious
        # session_end + session_start pair in the JSONL).
        existing = self._sessions.get(participant_id)
        if existing is not None and existing.session_id == session_id:
            logger.debug(
                "PulseSessionLogger.start_session: session %r already open for "
                "participant %r — idempotent call, ignoring",
                session_id, participant_id,
            )
            return

        # Auto-close any stale open session for this participant.
        if participant_id in self._sessions:
            logger.warning(
                "PulseSessionLogger.start_session: participant %r already has an open "
                "session (%r) — auto-closing before starting new session %r",
                participant_id,
                self._sessions[participant_id].session_id,
                session_id,
            )
            self._close_internal(participant_id, reason="auto_close_on_new_start")

        now = datetime.now(_TZ)
        started_at = now.isoformat()
        local_time = now.strftime("%Y-%m-%d %H:%M:%S %Z")
        ts_str = now.strftime("%Y%m%d_%H%M%S_%f")

        safe_pid = _safe_filename(participant_id)
        filename = f"{safe_pid}_{ts_str}_pulse_log.jsonl"
        log_path = self._log_dir / filename

        session = _ActiveSession(participant_id, session_id, log_path)
        self._sessions[participant_id] = session

        record: dict[str, Any] = {
            "type": "session_start",
            "participant_id": participant_id,
            "session_id": session_id,
            "started_at": started_at,
            "local_time": local_time,
        }
        if extra:
            record.update(extra)
        session.write(record)

        logger.info(
            "PulseSessionLogger: session_start for participant=%r session=%r → %s",
            participant_id, session_id, log_path.name,
        )

    def write_pulse(
        self,
        participant_id: str,
        session_id: str,
        unix_ms: int,
        pulse: int,
        *,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Append a single pulse measurement to the participant's open log file.

        Silently ignored if there is no open session for *participant_id*.
        """
        session = self._sessions.get(participant_id)
        if session is None:
            logger.debug(
                "PulseSessionLogger.write_pulse: no open session for participant=%r "
                "(session_id=%r) — pulse sample dropped",
                participant_id, session_id,
            )
            return

        if session.session_id != session_id:
            logger.debug(
                "PulseSessionLogger.write_pulse: session_id mismatch for participant=%r "
                "(open=%r, incoming=%r) — pulse sample dropped",
                participant_id, session.session_id, session_id,
            )
            return

        now = datetime.now(_TZ)
        record: dict[str, Any] = {
            "type": "pulse",
            "participant_id": participant_id,
            "session_id": session_id,
            "unix_ms": unix_ms,
            "pulse": pulse,
            "recorded_at": now.isoformat(),
        }
        if extra:
            record.update(extra)
        session.write(record)
        session.record_count += 1

    def close_session(
        self,
        participant_id: str,
        *,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Write a ``session_end`` marker and close the log file.

        Safe to call even if no session is open (logs a debug message and returns).
        """
        if participant_id not in self._sessions:
            logger.debug(
                "PulseSessionLogger.close_session: no open session for participant=%r — no-op",
                participant_id,
            )
            return
        self._close_internal(participant_id, extra=extra)

    def active_sessions(self) -> dict[str, dict[str, Any]]:
        """Return a snapshot of currently open sessions (for API introspection).

        Returns a plain dict mapping participant_id → info dict so callers
        never get direct access to internal ``_ActiveSession`` objects.
        """
        return {
            pid: {
                "participant_id": pid,
                "session_id": s.session_id,
                "log_file": s.log_path.name,
                "pulse_records": s.record_count,
            }
            for pid, s in self._sessions.items()
        }

    def close_all(self, *, extra: dict[str, Any] | None = None) -> None:
        """Close all open sessions – called during server shutdown."""
        for pid in list(self._sessions):
            self._close_internal(pid, reason="server_shutdown", extra=extra)

    # ── Internal helpers ──────────────────────────────────────────────

    def _close_internal(
        self,
        participant_id: str,
        *,
        reason: str = "normal",
        extra: dict[str, Any] | None = None,
    ) -> None:
        session = self._sessions.pop(participant_id, None)
        if session is None:
            return

        now = datetime.now(_TZ)
        ended_at = now.isoformat()
        local_time = now.strftime("%Y-%m-%d %H:%M:%S %Z")

        record: dict[str, Any] = {
            "type": "session_end",
            "participant_id": participant_id,
            "session_id": session.session_id,
            "ended_at": ended_at,
            "local_time": local_time,
            "pulse_record_count": session.record_count,
            "close_reason": reason,
        }
        if extra:
            record.update(extra)
        try:
            session.write(record)
        except Exception:
            logger.exception(
                "PulseSessionLogger: could not write session_end for participant=%r",
                participant_id,
            )
        finally:
            session.close()

        logger.info(
            "PulseSessionLogger: session_end for participant=%r session=%r "
            "(pulse_records=%d, reason=%r) → %s",
            participant_id, session.session_id,
            session.record_count, reason, session.log_path.name,
        )


# ── Module-level singleton ────────────────────────────────────────────
# Initialised lazily by ``init_pulse_logger(log_dir)`` called from main.py
# lifespan.  Before initialisation all methods are no-ops (None check in callers).
_pulse_logger: PulseSessionLogger | None = None


def init_pulse_logger(log_dir: Path | str) -> PulseSessionLogger:
    """Create (or replace) the module-level singleton and return it.

    Called once from the FastAPI lifespan context manager in ``main.py``.
    """
    global _pulse_logger
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    _pulse_logger = PulseSessionLogger(log_dir)
    logger.info("PulseSessionLogger initialised — log_dir=%s", log_dir)
    return _pulse_logger


def get_pulse_logger() -> PulseSessionLogger | None:
    """Return the module-level singleton (may be None before initialisation)."""
    return _pulse_logger


# ── Utility ───────────────────────────────────────────────────────────

def _safe_filename(value: str) -> str:
    """Strip or replace characters that are unsafe in file names."""
    safe = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in value)
    return safe[:64]  # cap length to avoid OS path-length limits
