"""
Participant log directory management.

When a new test-person is registered in the questionnaire, a dedicated
directory is created so that every log type is immediately ready to receive
data for that participant ID.

Directory layout
----------------
<PARTICIPANTS_DIR>/
  <participant_id>/
    info.json        – Participant metadata (id, display name, created_at)
    pulse.jsonl      – Heart-rate samples (appended by web_api_client / ws_ingest)
    session.jsonl    – Session start/end events (appended by ws_ingest)

All three files are created immediately on participant registration so that:
  • downstream tools (dashboards, analysis scripts) can rely on the files
    existing even when the participant has not yet started a session
  • a missing file unambiguously means the participant was never registered
    (as opposed to "registered but no data yet")

Usage
-----
    from live_analytics.app.storage.participant_logs import create_participant_log_dir

    create_participant_log_dir(
        participants_dir=PARTICIPANTS_DIR,
        participant_id="P007",
        display_name="Jonas",
        created_at="2026-05-04T12:00:00+00:00",
    )
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger("live_analytics.participant_logs")

# ── JSONL header comments (written as the first line) ─────────────────
# These are NOT valid JSON lines — they start with '#' so standard JSONL
# parsers (which skip lines that fail json.loads) will silently ignore them.
_PULSE_HEADER = "# pulse log — fields: session_id, unix_ms, pulse, participant_id, created_at, local_time\n"
_SESSION_HEADER = "# session log — fields: session_id, scenario_id, started_at, ended_at, participant_id, local_time\n"


def create_participant_log_dir(
    participants_dir: Path,
    participant_id: str,
    display_name: str = "",
    created_at: str = "",
) -> Path:
    """Create the log directory and placeholder files for *participant_id*.

    Safe to call more than once — existing files are never overwritten so
    accumulated data is preserved if the function is called again for the
    same participant (e.g. after a crash-restart).

    Parameters
    ----------
    participants_dir:
        Root directory under which per-participant subdirectories live.
        Typically ``live_analytics/data/participants/``.
    participant_id:
        The unique test-person identifier from the questionnaire
        (e.g. ``"P007"`` or ``"7"``).
    display_name:
        Human-readable name for the test person (stored in ``info.json``).
    created_at:
        ISO-8601 timestamp string of when the participant was registered.

    Returns
    -------
    Path
        The participant's log directory (``participants_dir / participant_id``).
    """
    participant_dir = participants_dir / _sanitise(participant_id)

    try:
        participant_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.error(
            "create_participant_log_dir: could not create directory '%s' for "
            "participant %r: %s — log files will NOT be pre-created",
            participant_dir, participant_id, exc,
        )
        return participant_dir

    # ── info.json ─────────────────────────────────────────────────────
    info_path = participant_dir / "info.json"
    if not info_path.exists():
        _write_json(info_path, {
            "participant_id": participant_id,
            "display_name": display_name,
            "created_at": created_at,
        })

    # ── pulse.jsonl ───────────────────────────────────────────────────
    _touch_jsonl(participant_dir / "pulse.jsonl", _PULSE_HEADER)

    # ── session.jsonl ─────────────────────────────────────────────────
    _touch_jsonl(participant_dir / "session.jsonl", _SESSION_HEADER)

    logger.info(
        "create_participant_log_dir: log directory ready for participant %r → %s",
        participant_id, participant_dir,
    )
    return participant_dir


# ── Append helpers used by the ingest pipeline ────────────────────────

def append_pulse(participants_dir: Path, participant_id: str, record: dict) -> None:
    """Append a pulse record to ``<participant_id>/pulse.jsonl``.

    Called directly from ``ws_ingest._ingest_session_batch()`` as a pure local
    filesystem operation, independent of any API or database availability.
    Failures are logged and silently swallowed so the ingest pipeline is never
    blocked by a filesystem error.
    """
    _append_jsonl(participants_dir / _sanitise(participant_id) / "pulse.jsonl", record)


def append_session_event(participants_dir: Path, participant_id: str, record: dict) -> None:
    """Append a session event to ``<participant_id>/session.jsonl``.

    Typical callers: ``ws_ingest`` on session start/end.
    """
    _append_jsonl(participants_dir / _sanitise(participant_id) / "session.jsonl", record)


# ── Private helpers ───────────────────────────────────────────────────

def _sanitise(participant_id: str) -> str:
    """Return a filesystem-safe version of *participant_id*.

    Replaces path separators and null bytes with underscores so that
    malicious or accidentally malformed IDs cannot escape the directory.
    """
    return (
        participant_id
        .replace("/", "_")
        .replace("\\", "_")
        .replace(":", "_")       # Windows: colon is not valid in path components
        .replace("\x00", "_")    # null bytes are illegal on all platforms
    )


def _write_json(path: Path, data: dict) -> None:
    try:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning("participant_logs: could not write '%s': %s", path, exc)


def _touch_jsonl(path: Path, header: str) -> None:
    """Create *path* with a comment header if it does not already exist."""
    if path.exists():
        return
    try:
        path.write_text(header, encoding="utf-8")
    except OSError as exc:
        logger.warning("participant_logs: could not create '%s': %s", path, exc)


def _append_jsonl(path: Path, record: dict) -> None:
    try:
        line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "participant_logs: could not serialise record for '%s': %s — "
            "record dropped: %r",
            path, exc, record,
        )
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(line)
            f.flush()   # ensure OS buffer is flushed on clean + abnormal shutdown
    except OSError as exc:
        logger.warning(
            "participant_logs: could not append to '%s': %s — "
            "1 record dropped (does the participant directory exist?)",
            path, exc,
        )
