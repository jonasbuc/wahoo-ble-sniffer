"""
Regression tests for the hardening fixes applied in May 2026:

F1 – resolve_participant() must NOT cache a None participant_id returned by the
     questionnaire API (a 200 response with {"participant_id": null} would
     permanently block future resolution).
     → pid is only written to _participant_cache when it is not None.

F2 – participant_logs._append_jsonl must auto-create the parent directory so
     that pulse/session writes never silently drop data when the participant
     directory does not yet exist (e.g. after a service restart or cross-machine
     deployment).
     → path.parent.mkdir(parents=True, exist_ok=True) added before open().

F3 – participant_logs._sanitise must replace ':' so that participant IDs
     containing colons produce valid directory names on Windows.
     → ':' is now mapped to '_'.

F4 – _resolve_and_link_participant retries up to 20 times with a 30-second
     delay so that athletes who register in the questionnaire slightly after
     their trainer connects are still linked (session_start written) without
     operator intervention.
     → retry loop added; the task yields to asyncio.sleep() between attempts.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


# ─────────────────────────────────────────────────────────────────────────────
# F1 — resolve_participant never caches None
# ─────────────────────────────────────────────────────────────────────────────

class TestResolveParticipantNoCacheNone:
    """
    When the questionnaire API returns HTTP 200 with {"participant_id": null},
    the session must NOT be permanently cached as None.  After the cooldown
    expires a subsequent call must issue a new HTTP request and succeed.
    """

    def setup_method(self):
        from live_analytics.app.storage import web_api_client
        self._wac = web_api_client
        # Clean any leftover state from other tests.
        self._sid = "sess-null-pid-test"
        self._wac._participant_cache.pop(self._sid, None)
        self._wac._resolve_cooldown_until.pop(self._sid, None)

    def teardown_method(self):
        self._wac._participant_cache.pop(self._sid, None)
        self._wac._resolve_cooldown_until.pop(self._sid, None)

    def _mock_http(self, body: dict, status_code: int = 200):
        """Return a patched httpx.AsyncClient context manager."""
        resp = MagicMock()
        resp.status_code = status_code
        resp.json.return_value = body
        resp.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=resp)
        return mock_client

    def test_null_pid_not_cached(self):
        """200 + null participant_id → NOT stored in cache, cooldown applied."""
        wac = self._wac
        mock_client = self._mock_http({"participant_id": None})

        with patch("live_analytics.app.storage.web_api_client.httpx.AsyncClient", return_value=mock_client):
            result = _run(wac.resolve_participant(self._sid))

        assert result is None, "should return None when participant_id is null"
        assert self._sid not in wac._participant_cache, \
            "None pid must NOT be written to _participant_cache"
        assert self._sid in wac._resolve_cooldown_until, \
            "cooldown must be set after a null-pid 200 response"

    def test_after_null_response_second_call_retries(self):
        """After null-pid response + cooldown expires, second call issues new HTTP request."""
        wac = self._wac
        # Simulate cooldown already expired.
        wac._resolve_cooldown_until[self._sid] = time.monotonic() - 1.0

        mock_client = self._mock_http({"participant_id": "P099"})

        with patch("live_analytics.app.storage.web_api_client.httpx.AsyncClient", return_value=mock_client):
            result = _run(wac.resolve_participant(self._sid))

        assert result == "P099"
        assert wac._participant_cache.get(self._sid) == "P099"

    def test_missing_key_not_cached_either(self):
        """200 body without 'participant_id' key → treated same as null."""
        wac = self._wac
        sid = self._sid + "-nokey"
        wac._participant_cache.pop(sid, None)
        wac._resolve_cooldown_until.pop(sid, None)

        mock_client = self._mock_http({})

        try:
            with patch("live_analytics.app.storage.web_api_client.httpx.AsyncClient", return_value=mock_client):
                result = _run(wac.resolve_participant(sid))
            assert result is None
            assert sid not in wac._participant_cache
        finally:
            wac._participant_cache.pop(sid, None)
            wac._resolve_cooldown_until.pop(sid, None)

    def test_valid_pid_is_still_cached(self):
        """200 with a non-None participant_id still gets cached as before."""
        wac = self._wac
        mock_client = self._mock_http({"participant_id": "P042"})

        with patch("live_analytics.app.storage.web_api_client.httpx.AsyncClient", return_value=mock_client):
            result = _run(wac.resolve_participant(self._sid))

        assert result == "P042"
        assert wac._participant_cache.get(self._sid) == "P042"
        assert self._sid not in wac._resolve_cooldown_until


# ─────────────────────────────────────────────────────────────────────────────
# F2 — _append_jsonl auto-creates the parent directory
# ─────────────────────────────────────────────────────────────────────────────

class TestAppendJsonlMkdir:
    """_append_jsonl must create missing parent directories instead of dropping data."""

    def test_writes_to_non_existent_dir(self, tmp_path):
        from live_analytics.app.storage.participant_logs import _append_jsonl

        nested = tmp_path / "participants" / "P001" / "pulse.jsonl"
        assert not nested.parent.exists(), "directory must not exist before write"

        _append_jsonl(nested, {"unix_ms": 1_000_000, "heart_rate": 72})

        assert nested.exists(), "_append_jsonl must create the file"
        assert nested.read_text(encoding="utf-8").strip() != ""

    def test_data_is_valid_json(self, tmp_path):
        from live_analytics.app.storage.participant_logs import _append_jsonl

        path = tmp_path / "sub" / "deeply" / "nested" / "session.jsonl"
        record = {"event": "session_start", "session_id": "s1", "participant_id": "P001"}
        _append_jsonl(path, record)

        line = path.read_text(encoding="utf-8").strip()
        assert json.loads(line) == record

    def test_second_write_appends(self, tmp_path):
        from live_analytics.app.storage.participant_logs import _append_jsonl

        path = tmp_path / "p" / "pulse.jsonl"
        _append_jsonl(path, {"a": 1})
        _append_jsonl(path, {"b": 2})

        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0]) == {"a": 1}
        assert json.loads(lines[1]) == {"b": 2}

    def test_existing_dir_still_works(self, tmp_path):
        """mkdir with exist_ok=True must not raise when directory already exists."""
        from live_analytics.app.storage.participant_logs import _append_jsonl

        path = tmp_path / "p2" / "pulse.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)  # pre-create
        _append_jsonl(path, {"x": 99})

        assert json.loads(path.read_text(encoding="utf-8").strip()) == {"x": 99}


# ─────────────────────────────────────────────────────────────────────────────
# F3 — _sanitise replaces colons
# ─────────────────────────────────────────────────────────────────────────────

class TestSanitiseColon:
    """_sanitise must replace ':' so IDs like 'P:001' are safe on Windows."""

    def test_colon_replaced(self):
        from live_analytics.app.storage.participant_logs import _sanitise
        assert _sanitise("P:001") == "P_001"

    def test_multiple_colons(self):
        from live_analytics.app.storage.participant_logs import _sanitise
        assert _sanitise("a:b:c") == "a_b_c"

    def test_slash_still_replaced(self):
        from live_analytics.app.storage.participant_logs import _sanitise
        assert _sanitise("a/b\\c") == "a_b_c"

    def test_null_byte_still_replaced(self):
        from live_analytics.app.storage.participant_logs import _sanitise
        assert _sanitise("a\x00b") == "a_b"

    def test_clean_id_unchanged(self):
        from live_analytics.app.storage.participant_logs import _sanitise
        assert _sanitise("P007") == "P007"

    def test_colon_path_does_not_escape_dir(self, tmp_path):
        """A colon-containing participant_id must not produce OS-level errors."""
        from live_analytics.app.storage.participant_logs import create_participant_log_dir
        d = create_participant_log_dir(
            tmp_path, "P:007", display_name="Test User", created_at="2026-01-01T00:00:00Z"
        )
        assert d.exists()
        assert ":" not in d.name


# ─────────────────────────────────────────────────────────────────────────────
# F4 — _resolve_and_link_participant retries until participant is found
# ─────────────────────────────────────────────────────────────────────────────

class TestResolveAndLinkRetry:
    """
    _resolve_and_link_participant must retry resolve_participant() on None
    results instead of giving up immediately, so late-registering participants
    are still linked and session_start is still written.
    """

    def _make_dummy_rec(self, sid: str = "sess-retry"):
        from live_analytics.app.models import TelemetryRecord
        return TelemetryRecord(
            session_id=sid,
            unix_ms=1_746_360_138_000,
            unity_time=1.0,
            speed=10.0,
            heart_rate=75.0,
            resistance=0.5,
            cadence=90.0,
            power=150.0,
            scenario_id="sc1",
            game_state="Riding",
        )

    def test_retries_and_succeeds_on_second_attempt(self, tmp_path):
        """
        resolve_participant returns None on the first call and 'P011' on the second.
        session_start should be written after the second attempt.
        """
        import live_analytics.app.ws_ingest as wsi
        import live_analytics.app.storage.web_api_client as wac

        sid = "sess-retry-f4-a"
        # Ensure the session appears active so the retry loop doesn't bail out.
        from collections import deque
        wsi._windows[sid] = deque(maxlen=200)
        wsi._record_counts[sid] = 5

        mock_resolve = AsyncMock(side_effect=[None, "P011"])
        mock_sleep = AsyncMock()
        mock_set_sp = MagicMock()
        mock_append = MagicMock()

        try:
            with (
                patch.object(wac, "resolve_participant", mock_resolve),
                patch("live_analytics.app.ws_ingest.asyncio.sleep", mock_sleep),
                patch("live_analytics.app.ws_ingest.set_session_participant", mock_set_sp),
                patch("live_analytics.app.ws_ingest._append_session_event", mock_append),
            ):
                _run(wsi._resolve_and_link_participant(sid, "sc1", "2026-01-01T00:00:00+00:00"))
        finally:
            wsi._windows.pop(sid, None)
            wsi._record_counts.pop(sid, None)

        assert mock_resolve.call_count == 2, "should have called resolve_participant twice"
        mock_sleep.assert_called_once()  # one sleep between attempt 1 and 2
        mock_set_sp.assert_called_once_with(wsi.DB_PATH, sid, "P011")
        mock_append.assert_called_once()
        written_event = mock_append.call_args[0][2]  # third positional arg
        assert written_event["event"] == "session_start"
        assert written_event["participant_id"] == "P011"

    def test_stops_retrying_when_session_evicted(self, tmp_path):
        """
        If the session is evicted (client disconnected) while the retry loop
        is waiting, the task should stop gracefully without writing any event.
        """
        import live_analytics.app.ws_ingest as wsi
        import live_analytics.app.storage.web_api_client as wac

        sid = "sess-retry-evicted"
        # Do NOT add to _windows → session appears evicted from the start.

        mock_resolve = AsyncMock(return_value=None)
        mock_sleep = AsyncMock()
        mock_append = MagicMock()

        with (
            patch.object(wac, "resolve_participant", mock_resolve),
            patch("live_analytics.app.ws_ingest.asyncio.sleep", mock_sleep),
            patch("live_analytics.app.ws_ingest._append_session_event", mock_append),
        ):
            _run(wsi._resolve_and_link_participant(sid, "sc1", "2026-01-01T00:00:00+00:00"))

        # After the first None with no active session, the task should stop.
        assert mock_resolve.call_count == 1
        mock_sleep.assert_not_called()
        mock_append.assert_not_called()

    def test_succeeds_on_first_attempt_no_sleep(self):
        """If participant is found immediately, asyncio.sleep must not be called."""
        import live_analytics.app.ws_ingest as wsi
        import live_analytics.app.storage.web_api_client as wac

        sid = "sess-instant-resolve"
        from collections import deque
        wsi._windows[sid] = deque(maxlen=200)
        wsi._record_counts[sid] = 1

        mock_resolve = AsyncMock(return_value="P777")
        mock_sleep = AsyncMock()
        mock_set_sp = MagicMock()
        mock_append = MagicMock()

        try:
            with (
                patch.object(wac, "resolve_participant", mock_resolve),
                patch("live_analytics.app.ws_ingest.asyncio.sleep", mock_sleep),
                patch("live_analytics.app.ws_ingest.set_session_participant", mock_set_sp),
                patch("live_analytics.app.ws_ingest._append_session_event", mock_append),
            ):
                _run(wsi._resolve_and_link_participant(sid, "sc1", "2026-01-01T00:00:00+00:00"))
        finally:
            wsi._windows.pop(sid, None)
            wsi._record_counts.pop(sid, None)

        mock_resolve.assert_called_once()
        mock_sleep.assert_not_called()
        mock_set_sp.assert_called_once()
        mock_append.assert_called_once()
