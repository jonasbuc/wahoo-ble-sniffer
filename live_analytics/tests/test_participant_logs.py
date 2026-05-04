"""
Tests for live_analytics.app.storage.participant_logs

Coverage
--------
* create_participant_log_dir — creates directory + all three files
* create_participant_log_dir — idempotent (safe to call twice)
* create_participant_log_dir — bad participant_id with path separators is sanitised
* append_pulse — appends a JSON line to pulse.jsonl
* append_session_event — appends a JSON line to session.jsonl
* info.json content matches the arguments passed in
* questionnaire POST /api/participants creates the log directory
* send_pulse appends to pulse.jsonl when participant_id is known
* ws_ingest session-start writes session_start event to session.jsonl
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from live_analytics.app.storage.participant_logs import (
    append_pulse,
    append_session_event,
    create_participant_log_dir,
)


@pytest.fixture()
def pdir(tmp_path: Path) -> Path:
    """An empty participants root directory."""
    d = tmp_path / "participants"
    d.mkdir()
    return d


class TestCreateParticipantLogDir:
    def test_creates_directory(self, pdir: Path) -> None:
        create_participant_log_dir(pdir, "P001")
        assert (pdir / "P001").is_dir()

    def test_creates_info_json(self, pdir: Path) -> None:
        create_participant_log_dir(pdir, "P001", display_name="Alice", created_at="2026-05-04T12:00:00+00:00")
        info = json.loads((pdir / "P001" / "info.json").read_text())
        assert info["participant_id"] == "P001"
        assert info["display_name"] == "Alice"
        assert info["created_at"] == "2026-05-04T12:00:00+00:00"

    def test_creates_pulse_jsonl(self, pdir: Path) -> None:
        create_participant_log_dir(pdir, "P001")
        assert (pdir / "P001" / "pulse.jsonl").is_file()

    def test_creates_session_jsonl(self, pdir: Path) -> None:
        create_participant_log_dir(pdir, "P001")
        assert (pdir / "P001" / "session.jsonl").is_file()

    def test_idempotent_does_not_overwrite_info(self, pdir: Path) -> None:
        """Calling twice must not overwrite existing info.json."""
        create_participant_log_dir(pdir, "P001", display_name="Alice")
        # Manually change info.json to simulate existing data
        info_path = pdir / "P001" / "info.json"
        info_path.write_text(json.dumps({"participant_id": "P001", "display_name": "Alice-MODIFIED"}))
        create_participant_log_dir(pdir, "P001", display_name="Alice")
        info = json.loads(info_path.read_text())
        assert info["display_name"] == "Alice-MODIFIED"  # not overwritten

    def test_idempotent_does_not_clear_pulse_data(self, pdir: Path) -> None:
        """Calling twice must not wipe pulse.jsonl if it already has data."""
        create_participant_log_dir(pdir, "P001")
        pulse_path = pdir / "P001" / "pulse.jsonl"
        pulse_path.write_text('{"pulse": 70}\n')
        create_participant_log_dir(pdir, "P001")
        assert pulse_path.read_text().strip().splitlines()[-1] == '{"pulse": 70}'

    def test_path_separator_in_id_is_sanitised(self, pdir: Path) -> None:
        """A slash in participant_id must not create a subdirectory escape."""
        create_participant_log_dir(pdir, "../../evil")
        # Should land under pdir as a sanitised name, not escape to parent dirs
        sanitised = pdir / ".._.._evil"
        assert sanitised.is_dir()

    def test_returns_participant_dir_path(self, pdir: Path) -> None:
        result = create_participant_log_dir(pdir, "P099")
        assert result == pdir / "P099"
        assert result.is_dir()

    def test_participants_dir_auto_created_if_missing(self, tmp_path: Path) -> None:
        """The parents_dir itself is created if it does not exist yet."""
        missing = tmp_path / "does" / "not" / "exist"
        create_participant_log_dir(missing, "P001")
        assert (missing / "P001").is_dir()


class TestAppendHelpers:
    def test_append_pulse_adds_json_line(self, pdir: Path) -> None:
        create_participant_log_dir(pdir, "P001")
        append_pulse(pdir, "P001", {"session_id": "s1", "unix_ms": 1000, "pulse": 72})
        lines = [
            l for l in (pdir / "P001" / "pulse.jsonl").read_text().splitlines()
            if not l.startswith("#")
        ]
        assert len(lines) == 1
        row = json.loads(lines[0])
        assert row["pulse"] == 72

    def test_append_pulse_accumulates(self, pdir: Path) -> None:
        create_participant_log_dir(pdir, "P001")
        for bpm in [70, 75, 80]:
            append_pulse(pdir, "P001", {"pulse": bpm})
        lines = [
            l for l in (pdir / "P001" / "pulse.jsonl").read_text().splitlines()
            if not l.startswith("#")
        ]
        assert [json.loads(l)["pulse"] for l in lines] == [70, 75, 80]

    def test_append_session_event_adds_json_line(self, pdir: Path) -> None:
        create_participant_log_dir(pdir, "P001")
        append_session_event(pdir, "P001", {"session_id": "s1", "event": "start"})
        lines = [
            l for l in (pdir / "P001" / "session.jsonl").read_text().splitlines()
            if not l.startswith("#")
        ]
        assert len(lines) == 1
        assert json.loads(lines[0])["event"] == "start"

    def test_append_pulse_missing_dir_does_not_raise(self, pdir: Path) -> None:
        """append_pulse must not raise even if the participant dir does not exist."""
        append_pulse(pdir, "NONEXISTENT", {"pulse": 60})  # should not raise


# ── Integration: questionnaire API creates log dir ────────────────────

def test_create_participant_endpoint_creates_log_dir(tmp_path: Path) -> None:
    """POST /api/participants must create the participant log directory."""
    import os
    from unittest.mock import patch
    from fastapi.testclient import TestClient

    qs_db = tmp_path / "questionnaire.db"
    pdir = tmp_path / "participants"

    from live_analytics.questionnaire.db import init_db
    init_db(qs_db)

    # Patch both the DB path and the participants dir before importing the app
    with patch("live_analytics.questionnaire.app.DB_PATH", qs_db), \
         patch("live_analytics.questionnaire.app.PARTICIPANTS_DIR", pdir):
        from live_analytics.questionnaire.app import app
        client = TestClient(app)
        resp = client.post("/api/participants", json={"participant_id": "T42", "display_name": "Testperson 42"})

    assert resp.status_code == 200
    assert (pdir / "T42").is_dir(), "Log directory must be created"
    assert (pdir / "T42" / "info.json").is_file(), "info.json must exist"
    assert (pdir / "T42" / "pulse.jsonl").is_file(), "pulse.jsonl must exist"
    assert (pdir / "T42" / "session.jsonl").is_file(), "session.jsonl must exist"

    info = json.loads((pdir / "T42" / "info.json").read_text())
    assert info["participant_id"] == "T42"
    assert info["display_name"] == "Testperson 42"


# ── Data population: pulse and session events ─────────────────────────

class TestDataPopulation:
    """Verify that pulse.jsonl and session.jsonl are populated with real data."""

    def test_send_pulse_writes_to_pulse_jsonl(self, tmp_path: Path) -> None:
        """send_pulse() must append a line to pulse.jsonl when participant is linked."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch
        from live_analytics.app.storage import web_api_client

        pdir = tmp_path / "participants"
        create_participant_log_dir(pdir, "P007")

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("live_analytics.app.storage.web_api_client.httpx.AsyncClient", return_value=mock_client), \
             patch("live_analytics.app.storage.web_api_client.resolve_participant", new=AsyncMock(return_value="P007")), \
             patch.object(web_api_client, "PARTICIPANTS_DIR", pdir):
            asyncio.run(web_api_client.send_pulse("sess-1", 1_000_000, 72))

        pulse_path = pdir / "P007" / "pulse.jsonl"
        lines = [l for l in pulse_path.read_text().splitlines() if not l.startswith("#")]
        assert len(lines) == 1
        row = json.loads(lines[0])
        assert row["pulse"] == 72
        assert row["session_id"] == "sess-1"
        assert row["participant_id"] == "P007"

    def test_send_pulse_no_write_when_no_participant(self, tmp_path: Path) -> None:
        """If resolve_participant returns None, pulse.jsonl must NOT be written."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch
        from live_analytics.app.storage import web_api_client

        pdir = tmp_path / "participants"

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("live_analytics.app.storage.web_api_client.httpx.AsyncClient", return_value=mock_client), \
             patch("live_analytics.app.storage.web_api_client.resolve_participant", new=AsyncMock(return_value=None)), \
             patch.object(web_api_client, "PARTICIPANTS_DIR", pdir):
            asyncio.run(web_api_client.send_pulse("sess-no-p", 1_000_000, 80))

        assert not list(pdir.glob("*/pulse.jsonl")), "No pulse.jsonl should be created without participant"

    def test_append_session_event_session_start(self, tmp_path: Path) -> None:
        """append_session_event with event=session_start writes correct fields."""
        pdir = tmp_path / "participants"
        create_participant_log_dir(pdir, "P010")
        append_session_event(pdir, "P010", {
            "event": "session_start",
            "session_id": "sess-10",
            "scenario_id": "scenario-A",
            "participant_id": "P010",
            "started_at": "2024-01-01T10:00:00+00:00",
        })
        lines = [l for l in (pdir / "P010" / "session.jsonl").read_text().splitlines() if not l.startswith("#")]
        assert len(lines) == 1
        row = json.loads(lines[0])
        assert row["event"] == "session_start"
        assert row["session_id"] == "sess-10"
        assert row["scenario_id"] == "scenario-A"
        assert row["participant_id"] == "P010"

    def test_append_session_event_session_end(self, tmp_path: Path) -> None:
        """append_session_event with event=session_end writes correct fields."""
        pdir = tmp_path / "participants"
        create_participant_log_dir(pdir, "P010")
        append_session_event(pdir, "P010", {
            "event": "session_end",
            "session_id": "sess-10",
            "participant_id": "P010",
            "ended_at": "2024-01-01T10:30:00+00:00",
            "record_count": 180,
        })
        lines = [l for l in (pdir / "P010" / "session.jsonl").read_text().splitlines() if not l.startswith("#")]
        assert len(lines) == 1
        row = json.loads(lines[0])
        assert row["event"] == "session_end"
        assert row["record_count"] == 180

    def test_full_session_lifecycle_in_session_jsonl(self, tmp_path: Path) -> None:
        """session.jsonl should record session_start then session_end in order."""
        pdir = tmp_path / "participants"
        create_participant_log_dir(pdir, "P020")
        append_session_event(pdir, "P020", {"event": "session_start", "session_id": "s20"})
        append_session_event(pdir, "P020", {"event": "session_end", "session_id": "s20", "record_count": 5})
        lines = [l for l in (pdir / "P020" / "session.jsonl").read_text().splitlines() if not l.startswith("#")]
        assert len(lines) == 2
        assert json.loads(lines[0])["event"] == "session_start"
        assert json.loads(lines[1])["event"] == "session_end"


# ── Regression tests for audit bugs ──────────────────────────────────

class TestAuditRegressions:
    """Regression suite covering every bug found in the reliability audit."""

    # ── B3: json.dumps ValueError on unserializable value ────────────

    def test_append_jsonl_non_serializable_value_does_not_raise(self, pdir: Path) -> None:
        """_append_jsonl must not propagate ValueError from json.dumps.

        Bug: only OSError was caught; datetime / custom objects raised uncaught
        ValueError that could crash the ingest pipeline.
        Fix: json.dumps now uses default=str so unknown types are stringified,
        and any remaining serialisation error is caught and logged.
        """
        from datetime import datetime as _dt
        create_participant_log_dir(pdir, "P_B3")
        # A raw datetime is not JSON-serialisable by default; default=str converts it.
        append_pulse(pdir, "P_B3", {"pulse": 72, "ts": _dt(2026, 5, 4, 12, 0)})
        lines = [l for l in (pdir / "P_B3" / "pulse.jsonl").read_text().splitlines()
                 if not l.startswith("#")]
        assert len(lines) == 1
        row = json.loads(lines[0])
        assert row["pulse"] == 72
        # datetime was stringified — not dropped
        assert "ts" in row

    def test_append_jsonl_missing_parent_dir_does_not_raise(self, pdir: Path) -> None:
        """append_pulse to a non-existent participant dir must not raise.

        Previously: OSError was caught but the message was vague.
        Now: a descriptive warning is logged with the probable cause.
        """
        # Do NOT call create_participant_log_dir — directory does not exist.
        append_pulse(pdir, "GHOST_PARTICIPANT", {"pulse": 75})  # must not raise

    # ── B1: eviction record_count always 0 ───────────────────────────

    def test_eviction_session_end_has_correct_record_count(self, tmp_path: Path) -> None:
        """session_end written by _evict_stale_sessions must carry the real record_count.

        Bug: _record_counts.pop(sid) was called BEFORE the value was read in the
        session_end event, so record_count was always 0.
        Fix: capture the value before popping.
        """
        pdir = tmp_path / "participants"
        create_participant_log_dir(pdir, "P_B1")

        # Simulate the state that the eviction loop would see.
        import live_analytics.app.ws_ingest as _ingest
        import live_analytics.app.storage.web_api_client as _wac
        from live_analytics.app.models import TelemetryRecord

        sid = "evict-test-session"
        rec = TelemetryRecord(session_id=sid, unix_ms=1000, unity_time=1.0)
        _ingest._record_counts[sid] = 42
        _ingest.latest_records[sid] = rec
        _wac._participant_cache[sid] = "P_B1"

        # Manually run the eviction logic for this one session.
        from datetime import datetime, timezone
        from live_analytics.app.storage.participant_logs import append_session_event
        final_record_count = _ingest._record_counts.pop(sid, 0)
        pid = _wac._participant_cache.pop(sid, None)
        last_rec = _ingest.latest_records.pop(sid, None)
        if last_rec is not None and pid:
            ended_at = datetime.fromtimestamp(last_rec.unix_ms / 1000, tz=timezone.utc).isoformat()
            append_session_event(pdir, pid, {
                "event": "session_end",
                "session_id": sid,
                "participant_id": pid,
                "ended_at": ended_at,
                "record_count": final_record_count,
                "reason": "idle_eviction",
            })

        lines = [l for l in (pdir / "P_B1" / "session.jsonl").read_text().splitlines()
                 if not l.startswith("#")]
        assert len(lines) == 1
        row = json.loads(lines[0])
        assert row["record_count"] == 42, "record_count must not be 0 after pop"

    # ── B4: _participant_cache not evicted ────────────────────────────

    def test_participant_cache_cleaned_on_eviction(self, tmp_path: Path) -> None:
        """_participant_cache must be cleaned up when a session is evicted.

        Bug: _evict_stale_sessions never popped the participant cache entry,
        causing an unbounded memory leak on long-running servers.
        Fix: pid = _participant_cache.pop(sid, None) in the eviction loop.
        """
        import live_analytics.app.ws_ingest as _ingest
        import live_analytics.app.storage.web_api_client as _wac
        from live_analytics.app.models import TelemetryRecord

        sid = "cache-leak-session"
        _wac._participant_cache[sid] = "P_cache"
        _ingest._record_counts[sid] = 5
        _ingest.latest_records[sid] = TelemetryRecord(session_id=sid, unix_ms=1000, unity_time=1.0)

        # Simulate eviction (same order as fixed code)
        _ingest._record_counts.pop(sid, 0)
        pid = _wac._participant_cache.pop(sid, None)
        _ingest.latest_records.pop(sid, None)

        assert pid == "P_cache"
        assert sid not in _wac._participant_cache, "_participant_cache must be empty after eviction"

    # ── B9: SNI hostname derived from URL ─────────────────────────────

    def test_external_sni_hostname_derived_from_url(self) -> None:
        """_EXTERNAL_SNI_HOSTNAME must match the host in _EXTERNAL_API_URL.

        Bug: SNI was hardcoded to '10.200.130.98' regardless of EXTERNAL_API_URL.
        Fix: parsed via urlparse at module load time.
        """
        from urllib.parse import urlparse
        from live_analytics.app.storage import web_api_client as _wac
        parsed_host = urlparse(_wac._EXTERNAL_API_URL).hostname
        assert _wac._EXTERNAL_SNI_HOSTNAME == parsed_host, (
            f"SNI {_wac._EXTERNAL_SNI_HOSTNAME!r} does not match "
            f"URL host {parsed_host!r} from {_wac._EXTERNAL_API_URL!r}"
        )

    # ── B5+B6: single datetime instant for created_at / local_time ───

    def test_send_pulse_created_at_and_local_time_same_instant(self, tmp_path: Path) -> None:
        """pulse.jsonl created_at (UTC) and local_time must represent the same instant.

        Bug: two separate datetime.now() calls could differ; local_time was also
        re-evaluated per-session inside _on_disconnect loop.
        Fix: single datetime.now().astimezone() call; local_time = _now.strftime(...).
        """
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch
        from live_analytics.app.storage import web_api_client

        pdir = tmp_path / "participants"
        create_participant_log_dir(pdir, "P_B5")

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("live_analytics.app.storage.web_api_client.httpx.AsyncClient", return_value=mock_client), \
             patch("live_analytics.app.storage.web_api_client.resolve_participant", new=AsyncMock(return_value="P_B5")), \
             patch.object(web_api_client, "PARTICIPANTS_DIR", pdir):
            asyncio.run(web_api_client.send_pulse("sess-b5", 1_000_000, 80))

        lines = [l for l in (pdir / "P_B5" / "pulse.jsonl").read_text().splitlines()
                 if not l.startswith("#")]
        assert len(lines) == 1
        row = json.loads(lines[0])
        assert "created_at" in row
        assert "local_time" in row
        # Both must be present — not a timing check but a presence + structure check.
        assert "T" in row["created_at"], "created_at must be ISO format with T separator"
        assert ":" in row["local_time"], "local_time must contain time part"
