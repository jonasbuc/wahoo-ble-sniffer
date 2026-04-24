"""
Raw JSONL writer – appends every telemetry record to a per-session JSONL file.

File layout:
    <SESSIONS_DIR>/<session_id>/telemetry.jsonl

Each line is a complete JSON object (one ``TelemetryRecord`` serialised via
``model_dump_json()``).  Partial lines at the end of the file (from a crash
mid-write) are silently skipped by readers that iterate with ``json.loads``
per line.

The writer is intentionally simple: open-append-close on each flush to
ensure durability even if the process crashes mid-session.  There is no
in-memory buffer — every ``append_many`` call results in one file open/write/close
per session, which is acceptable at 20 Hz / batch_size 10 (≈ 2 file ops per second).

Backfill: if the DB is lost, the JSONL files are the ground truth and can be
replayed by ``live_analytics/scripts/backfill_from_jsonl.py``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from live_analytics.app.models import TelemetryRecord

logger = logging.getLogger("live_analytics.raw_writer")


class RawWriter:
    """Writes telemetry records as newline-delimited JSON (JSONL).

    Prefer ``append_many()`` over calling ``append()`` in a loop — it groups
    records by session and performs a single file open/close per session per batch.
    """

    def __init__(self, sessions_dir: Path) -> None:
        self._sessions_dir = sessions_dir

    def _session_path(self, session_id: str) -> Path:
        d = self._sessions_dir / session_id
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.error(
                "Cannot create session directory '%s' for session '%s': %s  "
                "(Check disk space and write permissions – JSONL persistence disabled for this session)",
                d, session_id, exc,
            )
            raise
        return d / "telemetry.jsonl"

    def append(self, record: TelemetryRecord) -> None:
        """Append a single record to the session's JSONL file."""
        try:
            path = self._session_path(record.session_id)
        except OSError:
            return  # already logged in _session_path
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(record.model_dump_json() + "\n")
        except OSError as exc:
            logger.error(
                "Failed to write JSONL for session '%s' at '%s': %s",
                record.session_id, path, exc,
            )

    def append_many(self, records: list[TelemetryRecord]) -> None:
        """Append multiple records, grouped by session."""
        by_session: dict[str, list[TelemetryRecord]] = {}
        for r in records:
            by_session.setdefault(r.session_id, []).append(r)

        for sid, recs in by_session.items():
            try:
                path = self._session_path(sid)
            except OSError:
                continue  # already logged in _session_path
            try:
                with open(path, "a", encoding="utf-8") as f:
                    for r in recs:
                        f.write(r.model_dump_json() + "\n")
            except OSError as exc:
                logger.error(
                    "Failed to write JSONL for session '%s' at '%s': %s",
                    sid, path, exc,
                )
