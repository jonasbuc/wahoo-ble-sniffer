"""
Raw JSONL writer – appends every telemetry record to a per-session JSONL file.

The writer is intentionally simple: open-append-close on each flush to
ensure durability even if the process crashes.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from live_analytics.app.models import TelemetryRecord

logger = logging.getLogger("live_analytics.raw_writer")


class RawWriter:
    """Writes telemetry records as newline-delimited JSON (JSONL)."""

    def __init__(self, sessions_dir: Path) -> None:
        self._sessions_dir = sessions_dir

    def _session_path(self, session_id: str) -> Path:
        d = self._sessions_dir / session_id
        d.mkdir(parents=True, exist_ok=True)
        return d / "telemetry.jsonl"

    def append(self, record: TelemetryRecord) -> None:
        """Append a single record to the session's JSONL file."""
        path = self._session_path(record.session_id)
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(record.model_dump_json() + "\n")
        except Exception:
            logger.exception("Failed to write JSONL for session %s", record.session_id)

    def append_many(self, records: list[TelemetryRecord]) -> None:
        """Append multiple records, grouped by session."""
        by_session: dict[str, list[TelemetryRecord]] = {}
        for r in records:
            by_session.setdefault(r.session_id, []).append(r)

        for sid, recs in by_session.items():
            path = self._session_path(sid)
            try:
                with open(path, "a", encoding="utf-8") as f:
                    for r in recs:
                        f.write(r.model_dump_json() + "\n")
            except Exception:
                logger.exception("Failed to write JSONL for session %s", sid)
