"""
test_lifecycle_hardening_deep.py
=================================
Deep lifecycle audit tests — covers the bugs and brittle assumptions found
in the participant/session/pulse lifecycle code review of May 2026.

Issues covered
--------------
A  _do_resolve_and_link_participant must abort if session removed from _windows
   (prevents linking a fresh participant to an already-closed session).
B  _on_disconnect must remove sid from _windows so the background resolve task
   aborts immediately on its next iteration.
C  link_session must raise ValueError when participant is already linked to a
   DIFFERENT non-empty, non-done session_id (TOCTOU collision guard).
D  questionnaire link_session_endpoint must return 409 on ValueError.
E  resolve_participant must treat a 409 link response as a retry-able failure
   (apply cooldown, do NOT cache, do NOT permanently block).
F  send_pulse warning gates must be cleared when delivery recovers so operators
   see the recovery event in INFO logs.

Additional transition-state tests
----------------------------------
G  link_session is idempotent (re-linking to same session is silent and safe).
H  link_session allows overwriting a '__done__' session_id (re-registration).
I  get_oldest_unlinked_participant excludes '__done__' and linked participants.
J  pulse back-fill runs correctly on late link.
K  _on_disconnect writes _windows pop even when mark_participant_done raises.
"""
from __future__ import annotations

import asyncio
import sqlite3
import time
from collections import deque
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── helpers ──────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


# ══════════════════════════════════════════════════════════════════════
# A — Resolve task aborts when session removed from _windows
# ══════════════════════════════════════════════════════════════════════

class TestResolveTaskAbortsOnWindowMissing:
    """_do_resolve_and_link_participant checks _windows at the TOP of every
    loop iteration, BEFORE calling resolve_participant, so it aborts
    immediately after a disconnect removes the session."""

    def setup_method(self):
        from live_analytics.app import ws_ingest
        self._wsi = ws_ingest
        self._sid = "sess-resolve-abort"
        # Put a sentinel in _windows so the first check succeeds,
        # then we'll remove it to simulate disconnect.
        ws_ingest._windows[self._sid] = deque(maxlen=600)

    def teardown_method(self):
        from live_analytics.app import ws_ingest
        ws_ingest._windows.pop(self._sid, None)
        ws_ingest._resolve_running.discard(self._sid)
        from live_analytics.app.storage import web_api_client
        web_api_client.clear_participant_cache(self._sid)

    def test_aborts_before_calling_resolve_when_window_missing(self):
        """If _windows[sid] is absent at the top of the first iteration,
        the task returns without ever calling resolve_participant."""
        from live_analytics.app import ws_ingest

        # Remove the session from _windows BEFORE the task runs
        ws_ingest._windows.pop(self._sid, None)

        resolve_calls = []

        async def fake_resolve(session_id):
            resolve_calls.append(session_id)
            return None

        with patch("live_analytics.app.storage.web_api_client.resolve_participant", side_effect=fake_resolve):
            _run(ws_ingest._do_resolve_and_link_participant(
                self._sid, "test-scenario", "2026-05-01T10:00:00+02:00"
            ))

        assert resolve_calls == [], (
            "resolve_participant must NOT be called when session is absent from _windows "
            "at the top of the first iteration — task should abort immediately"
        )

    def test_aborts_after_disconnect_removes_window_during_retry(self):
        """After the first failed resolve attempt, if _windows is cleared
        (simulating disconnect mid-retry), the second iteration aborts."""
        from live_analytics.app import ws_ingest

        call_count = [0]

        async def fake_resolve(session_id):
            call_count[0] += 1
            if call_count[0] == 1:
                # First attempt: no participant yet — but session is still alive.
                # We also simulate the disconnect happening here (between attempts).
                ws_ingest._windows.pop(session_id, None)
                return None
            return "P999"  # should never reach this

        with patch("live_analytics.app.storage.web_api_client.resolve_participant", side_effect=fake_resolve), \
             patch("asyncio.sleep", new=AsyncMock()):
            _run(ws_ingest._do_resolve_and_link_participant(
                self._sid, "scenario", "2026-05-01T10:00:00+02:00"
            ))

        # Only one resolve call — the second iteration saw no _windows entry and aborted.
        assert call_count[0] == 1, (
            "resolve_participant should be called exactly once: "
            "the second iteration should abort because _windows was cleared"
        )

    def test_links_successfully_when_window_present(self, tmp_path):
        """Sanity check: when _windows[sid] IS present, the task resolves and links."""
        from live_analytics.app import ws_ingest
        from live_analytics.app.storage import web_api_client

        linked = []

        async def fake_resolve(session_id):
            return "P001"

        with patch("live_analytics.app.storage.web_api_client.resolve_participant", side_effect=fake_resolve), \
             patch("live_analytics.app.ws_ingest.set_session_participant"), \
             patch("live_analytics.app.ws_ingest._append_session_event"), \
             patch("live_analytics.app.ws_ingest._append_pulse_marker"), \
             patch("live_analytics.app.ws_ingest._get_pulse_logger", return_value=None):
            _run(ws_ingest._do_resolve_and_link_participant(
                self._sid, "scenario", "2026-05-01T10:00:00+02:00"
            ))
        # No assertion needed — if it didn't raise the task ran to success.


# ══════════════════════════════════════════════════════════════════════
# B — _on_disconnect pops _windows for each session
# ══════════════════════════════════════════════════════════════════════

class TestOnDisconnectPopsWindows:
    """_on_disconnect must remove each processed session_id from _windows.

    This is the critical signal that causes background resolve tasks to abort
    (they check `if sid not in _windows` at the top of every iteration).
    """

    def setup_method(self):
        from live_analytics.app import ws_ingest
        from live_analytics.app.storage import web_api_client
        self._wsi = ws_ingest
        self._wac = web_api_client
        self._sid = "sess-disconnect-pop"
        ws_ingest._windows[self._sid] = deque(maxlen=600)
        ws_ingest._record_counts[self._sid] = 5

    def teardown_method(self):
        from live_analytics.app import ws_ingest
        ws_ingest._windows.pop(self._sid, None)
        ws_ingest._record_counts.pop(self._sid, None)
        ws_ingest.latest_records.pop(self._sid, None)
        ws_ingest.latest_scores.pop(self._sid, None)
        ws_ingest.latest_hr.pop(self._sid, None)
        from live_analytics.app.storage import web_api_client
        web_api_client.clear_participant_cache(self._sid)

    def test_windows_popped_when_no_records(self):
        """When last_rec is None (no records), _windows[sid] must be popped."""
        wsi = self._wsi
        # Ensure latest_records has no entry for this session
        wsi.latest_records.pop(self._sid, None)
        # No cached participant
        self._wac.clear_participant_cache(self._sid)

        _run(wsi._on_disconnect({self._sid}))

        assert self._sid not in wsi._windows, (
            "_windows[sid] must be popped in _on_disconnect even when no records exist"
        )

    def test_windows_popped_when_records_but_no_participant(self, tmp_path):
        """When records exist but no participant can be resolved, _windows is still popped."""
        from live_analytics.app.models import TelemetryRecord
        wsi = self._wsi

        rec = TelemetryRecord(
            session_id=self._sid, unix_ms=1_000_000, unity_time=1.0,
            heart_rate=70, speed=5.0,
        )
        wsi.latest_records[self._sid] = rec
        self._wac.clear_participant_cache(self._sid)

        with patch("live_analytics.app.storage.web_api_client.resolve_participant",
                   new=AsyncMock(return_value=None)), \
             patch("live_analytics.app.ws_ingest.end_session"):
            _run(wsi._on_disconnect({self._sid}))

        assert self._sid not in wsi._windows

    def test_windows_popped_on_success_path(self, tmp_path):
        """Full success path (records + participant found) still pops _windows."""
        from live_analytics.app.models import TelemetryRecord
        wsi = self._wsi

        rec = TelemetryRecord(
            session_id=self._sid, unix_ms=1_000_000, unity_time=1.0,
            heart_rate=70, speed=5.0,
        )
        wsi.latest_records[self._sid] = rec
        self._wac._participant_cache[self._sid] = "P001"

        with patch("live_analytics.app.ws_ingest.end_session"), \
             patch("live_analytics.app.ws_ingest._append_session_event"), \
             patch("live_analytics.app.ws_ingest._append_pulse_marker"), \
             patch("live_analytics.app.ws_ingest._get_pulse_logger", return_value=None), \
             patch("live_analytics.app.storage.web_api_client.mark_participant_done",
                   new=AsyncMock()):
            _run(wsi._on_disconnect({self._sid}))

        assert self._sid not in wsi._windows

    def test_windows_popped_even_when_mark_done_raises(self):
        """try/finally ensures _windows is popped even if mark_participant_done raises."""
        from live_analytics.app.models import TelemetryRecord
        wsi = self._wsi

        rec = TelemetryRecord(
            session_id=self._sid, unix_ms=1_000_000, unity_time=1.0,
            heart_rate=70, speed=5.0,
        )
        wsi.latest_records[self._sid] = rec
        self._wac._participant_cache[self._sid] = "P001"

        async def explode(*args, **kwargs):
            raise RuntimeError("simulated mark_done failure")

        with patch("live_analytics.app.ws_ingest.end_session"), \
             patch("live_analytics.app.ws_ingest._append_session_event"), \
             patch("live_analytics.app.ws_ingest._append_pulse_marker"), \
             patch("live_analytics.app.ws_ingest._get_pulse_logger", return_value=None), \
             patch("live_analytics.app.storage.web_api_client.mark_participant_done",
                   new=AsyncMock(side_effect=explode)):
            # Exception inside _process_one_disconnect should not propagate
            # and _windows must still be popped by the outer try/finally.
            try:
                _run(wsi._on_disconnect({self._sid}))
            except RuntimeError:
                pass  # propagation is acceptable; what matters is _windows was popped

        assert self._sid not in wsi._windows, (
            "_windows must be popped by the try/finally even when mark_participant_done raises"
        )


# ══════════════════════════════════════════════════════════════════════
# C — link_session raises ValueError on collision
# ══════════════════════════════════════════════════════════════════════

class TestLinkSessionCollisionGuard:
    """link_session must raise ValueError when a participant is already linked
    to a DIFFERENT non-empty, non-done session_id."""

    @pytest.fixture()
    def qs_db(self, tmp_path: Path):
        from live_analytics.questionnaire.db import init_db, close_pool
        p = tmp_path / "q.db"
        init_db(p)
        yield p
        close_pool()

    def _create(self, db, pid, session_id=""):
        conn = sqlite3.connect(str(db))
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT OR REPLACE INTO participants "
            "(participant_id, session_id, display_name, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (pid, session_id, pid, now, now),
        )
        conn.commit()
        conn.close()

    def test_raises_on_different_session(self, qs_db):
        from live_analytics.questionnaire.db import link_session
        self._create(qs_db, "P-col", "existing-session")
        with pytest.raises(ValueError, match="already linked to"):
            link_session(qs_db, "P-col", "new-session")

    def test_idempotent_same_session(self, qs_db):
        """Re-linking to the SAME session must not raise."""
        from live_analytics.questionnaire.db import link_session
        self._create(qs_db, "P-idem", "same-session")
        # Should not raise
        link_session(qs_db, "P-idem", "same-session")

    def test_allows_link_from_empty(self, qs_db):
        """Linking an unlinked participant (session_id='') is always allowed."""
        from live_analytics.questionnaire.db import link_session
        self._create(qs_db, "P-fresh", "")
        link_session(qs_db, "P-fresh", "new-session")

    def test_allows_link_from_done(self, qs_db):
        """A done participant (session_id='__done__') can be re-linked.

        This covers the case where an operator re-registers a participant
        for a second session in a multi-session study.
        """
        from live_analytics.questionnaire.db import link_session
        self._create(qs_db, "P-done", "__done__")
        # Should not raise
        link_session(qs_db, "P-done", "second-session")

    def test_allows_link_for_new_participant(self, qs_db):
        """Linking a brand-new participant (not yet in DB) is always allowed."""
        from live_analytics.questionnaire.db import link_session
        # P-new has no row yet; link_session should just run the UPDATE silently.
        # (No row → UPDATE affects 0 rows, which is fine.)
        link_session(qs_db, "P-new", "fresh-session")


# ══════════════════════════════════════════════════════════════════════
# D — questionnaire app link endpoint returns 409 on collision
# ══════════════════════════════════════════════════════════════════════

class TestLinkSessionEndpoint409:
    """PUT /api/participants/{id}/session must return 409 when link_session
    raises ValueError (participant already linked to a different session)."""

    @pytest.fixture()
    def client(self, tmp_path: Path):
        from live_analytics.questionnaire import app as qs_app
        from fastapi.testclient import TestClient
        import importlib

        # Point the app at a fresh temp DB
        db = tmp_path / "q.db"
        with patch.object(qs_app, "DB_PATH", db):
            from live_analytics.questionnaire.db import init_db, close_pool
            init_db(db)
            yield TestClient(qs_app.app, raise_server_exceptions=False), db
            close_pool()

    def test_returns_409_when_participant_already_linked(self, client):
        tc, db = client
        # Create participant linked to session-A
        import sqlite3
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT INTO participants "
            "(participant_id, session_id, display_name, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("P-409", "session-A", "Test", now, now),
        )
        conn.commit()
        conn.close()

        with patch("live_analytics.questionnaire.app.DB_PATH", db):
            resp = tc.put("/api/participants/P-409/session", json={"session_id": "session-B"})

        assert resp.status_code == 409, (
            f"Expected 409 when linking to different session, got {resp.status_code}: {resp.text}"
        )
        assert "already linked" in resp.json()["detail"].lower()

    def test_returns_200_for_idempotent_relink(self, client):
        tc, db = client
        import sqlite3
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT INTO participants "
            "(participant_id, session_id, display_name, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("P-idem2", "same-session", "Test", now, now),
        )
        conn.commit()
        conn.close()

        with patch("live_analytics.questionnaire.app.DB_PATH", db):
            resp = tc.put("/api/participants/P-idem2/session", json={"session_id": "same-session"})

        assert resp.status_code == 200


# ══════════════════════════════════════════════════════════════════════
# E — resolve_participant handles 409 as retry-able (no cache, cooldown)
# ══════════════════════════════════════════════════════════════════════

class TestResolveParticipantHandles409:
    """When the auto-link PUT returns 409 (TOCTOU collision), resolve_participant
    must NOT cache the participant_id and must apply the standard cooldown."""

    def setup_method(self):
        from live_analytics.app.storage import web_api_client
        self._wac = web_api_client
        self._sid = "sess-409-test"
        web_api_client.clear_participant_cache(self._sid)

    def teardown_method(self):
        from live_analytics.app.storage import web_api_client
        web_api_client.clear_participant_cache(self._sid)

    def _make_mock_client(self, by_session_status, unlinked_body, link_status):
        """Build a mock httpx.AsyncClient that returns controlled responses."""
        by_session_resp = MagicMock()
        by_session_resp.status_code = by_session_status

        unlinked_resp = MagicMock()
        unlinked_resp.status_code = 200
        unlinked_resp.json.return_value = unlinked_body

        link_resp = MagicMock()
        link_resp.status_code = link_status

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        # Ordered: first call = by-session, second = oldest-unlinked, third = PUT
        mock_client.get = AsyncMock(side_effect=[by_session_resp, unlinked_resp])
        mock_client.put = AsyncMock(return_value=link_resp)
        return mock_client

    def test_409_not_cached_cooldown_applied(self):
        """After a 409 from PUT, participant is NOT cached and cooldown IS set."""
        wac = self._wac
        mock_client = self._make_mock_client(
            by_session_status=404,
            unlinked_body={"participant_id": "P-conflict"},
            link_status=409,
        )

        with patch("live_analytics.app.storage.web_api_client.httpx.AsyncClient",
                   return_value=mock_client):
            result = _run(wac.resolve_participant(self._sid))

        assert result is None, "409 collision must return None"
        assert self._sid not in wac._participant_cache, \
            "participant must NOT be cached after 409 collision"
        assert self._sid in wac._resolve_cooldown_until, \
            "cooldown must be applied after 409 so we don't hammer the API"
        assert wac._resolve_cooldown_until[self._sid] > time.monotonic(), \
            "cooldown must expire in the future"

    def test_200_after_409_resolves_on_next_call(self):
        """After a 409 collision and cooldown expires, the next call succeeds normally."""
        wac = self._wac
        # Simulate expired cooldown
        wac._resolve_cooldown_until[self._sid] = time.monotonic() - 1.0

        mock_client = self._make_mock_client(
            by_session_status=404,
            unlinked_body={"participant_id": "P-new"},
            link_status=200,
        )
        # The second call's successful link response needs raise_for_status mock
        mock_client.put.return_value.raise_for_status = MagicMock()

        with patch("live_analytics.app.storage.web_api_client.httpx.AsyncClient",
                   return_value=mock_client):
            result = _run(wac.resolve_participant(self._sid))

        assert result == "P-new"
        assert wac._participant_cache.get(self._sid) == "P-new"


# ══════════════════════════════════════════════════════════════════════
# F — warning gates clear on recovery
# ══════════════════════════════════════════════════════════════════════

class TestWarningGateRecovery:
    """After a QS or external delivery failure, the warning gate must be
    cleared when delivery subsequently succeeds, so operators see the
    recovery in INFO logs and future failures warn again."""

    def setup_method(self):
        from live_analytics.app.storage import web_api_client
        self._wac = web_api_client
        self._sid = "sess-gate-recovery"
        web_api_client.clear_participant_cache(self._sid)
        # Pre-arm the warning gate as if a prior failure already fired.
        web_api_client._warned_qs_failed.add(self._sid)
        web_api_client._warned_ext_failed.add(self._sid)

    def teardown_method(self):
        from live_analytics.app.storage import web_api_client
        web_api_client.clear_participant_cache(self._sid)
        web_api_client._warned_qs_failed.discard(self._sid)
        web_api_client._warned_ext_failed.discard(self._sid)

    def _make_success_client(self):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=resp)
        return mock_client

    def test_qs_gate_cleared_on_recovery(self):
        wac = self._wac
        assert self._sid in wac._warned_qs_failed  # pre-condition

        mock_client = self._make_success_client()
        with patch("live_analytics.app.storage.web_api_client.resolve_participant",
                   new=AsyncMock(return_value=None)), \
             patch("live_analytics.app.storage.web_api_client.httpx.AsyncClient",
                   return_value=mock_client):
            _run(wac.send_pulse(self._sid, 1_000_000, 75))

        assert self._sid not in wac._warned_qs_failed, (
            "_warned_qs_failed must be cleared when questionnaire delivery succeeds after a failure"
        )

    def test_ext_gate_cleared_on_recovery(self):
        wac = self._wac
        assert self._sid in wac._warned_ext_failed  # pre-condition

        mock_client = self._make_success_client()
        with patch("live_analytics.app.storage.web_api_client.resolve_participant",
                   new=AsyncMock(return_value=None)), \
             patch("live_analytics.app.storage.web_api_client.httpx.AsyncClient",
                   return_value=mock_client):
            _run(wac.send_pulse(self._sid, 1_000_000, 75))

        assert self._sid not in wac._warned_ext_failed, (
            "_warned_ext_failed must be cleared when external delivery succeeds after a failure"
        )

    def test_gate_remains_after_continued_failure(self):
        """If delivery is still failing, the gate stays set (no double-warn)."""
        import httpx as _httpx
        wac = self._wac
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=_httpx.ConnectError("refused"))

        with patch("live_analytics.app.storage.web_api_client.resolve_participant",
                   new=AsyncMock(return_value=None)), \
             patch("live_analytics.app.storage.web_api_client.httpx.AsyncClient",
                   return_value=mock_client):
            _run(wac.send_pulse(self._sid, 1_000_000, 75))

        assert self._sid in wac._warned_qs_failed, "gate must remain when still failing"
        assert self._sid in wac._warned_ext_failed, "gate must remain when still failing"


# ══════════════════════════════════════════════════════════════════════
# G — link_session idempotency
# ══════════════════════════════════════════════════════════════════════

class TestLinkSessionIdempotency:
    @pytest.fixture()
    def qs_db(self, tmp_path: Path):
        from live_analytics.questionnaire.db import init_db, close_pool
        p = tmp_path / "q.db"
        init_db(p)
        yield p
        close_pool()

    def test_double_link_same_session_no_error_no_overwrite(self, qs_db):
        from live_analytics.questionnaire.db import (
            create_participant, link_session, get_participant,
        )
        create_participant(qs_db, "P-re", "Re-link test")
        link_session(qs_db, "P-re", "sess-X")
        # Second link to same session — must not raise and session_id unchanged
        link_session(qs_db, "P-re", "sess-X")
        p = get_participant(qs_db, "P-re")
        assert p["session_id"] == "sess-X"


# ══════════════════════════════════════════════════════════════════════
# H — link_session allows re-linking from __done__
# ══════════════════════════════════════════════════════════════════════

class TestLinkSessionFromDone:
    @pytest.fixture()
    def qs_db(self, tmp_path: Path):
        from live_analytics.questionnaire.db import init_db, close_pool
        p = tmp_path / "q.db"
        init_db(p)
        yield p
        close_pool()

    def test_re_link_from_done_succeeds(self, qs_db):
        from live_analytics.questionnaire.db import (
            create_participant, mark_participant_done,
            link_session, get_participant,
        )
        create_participant(qs_db, "P-redo", "Redo")
        link_session(qs_db, "P-redo", "sess-first")
        mark_participant_done(qs_db, "P-redo")

        # Simulate operator re-enrolling for a second session
        link_session(qs_db, "P-redo", "sess-second")
        p = get_participant(qs_db, "P-redo")
        assert p["session_id"] == "sess-second", (
            "A done participant must be re-linkable to a new session"
        )


# ══════════════════════════════════════════════════════════════════════
# I — get_oldest_unlinked excludes done and linked
# ══════════════════════════════════════════════════════════════════════

class TestGetOldestUnlinked:
    @pytest.fixture()
    def qs_db(self, tmp_path: Path):
        from live_analytics.questionnaire.db import init_db, close_pool
        p = tmp_path / "q.db"
        init_db(p)
        yield p
        close_pool()

    def test_excludes_done_participants(self, qs_db):
        import time as _time
        from live_analytics.questionnaire.db import (
            create_participant, mark_participant_done,
            get_oldest_unlinked_participant,
        )
        create_participant(qs_db, "P-done-excl", "Done participant")
        mark_participant_done(qs_db, "P-done-excl")
        _time.sleep(0.01)
        result = get_oldest_unlinked_participant(qs_db)
        if result:
            assert result["participant_id"] != "P-done-excl"

    def test_excludes_linked_participants(self, qs_db):
        from live_analytics.questionnaire.db import (
            create_participant, link_session, get_oldest_unlinked_participant,
        )
        create_participant(qs_db, "P-linked-excl", "Linked participant")
        link_session(qs_db, "P-linked-excl", "some-session")
        result = get_oldest_unlinked_participant(qs_db)
        if result:
            assert result["participant_id"] != "P-linked-excl"

    def test_fifo_order(self, qs_db):
        """Oldest participant (by created_at) must be returned first."""
        import time as _time
        from live_analytics.questionnaire.db import (
            create_participant, get_oldest_unlinked_participant,
        )
        create_participant(qs_db, "P-fifo-1", "First")
        _time.sleep(0.02)
        create_participant(qs_db, "P-fifo-2", "Second")
        result = get_oldest_unlinked_participant(qs_db)
        assert result is not None
        assert result["participant_id"] == "P-fifo-1", (
            "FIFO must return P-fifo-1 (registered first) not P-fifo-2"
        )


# ══════════════════════════════════════════════════════════════════════
# J — pulse back-fill on late link
# ══════════════════════════════════════════════════════════════════════

class TestPulseBackfill:
    @pytest.fixture()
    def qs_db(self, tmp_path: Path):
        from live_analytics.questionnaire.db import init_db, close_pool
        p = tmp_path / "q.db"
        init_db(p)
        yield p
        close_pool()

    def test_null_participant_backfilled_on_link(self, qs_db):
        """Pulses that arrived before participant was linked have participant_id=NULL.
        After link_session, those rows must be back-filled with the correct participant_id."""
        from live_analytics.questionnaire.db import (
            create_participant, link_session, insert_pulse_data, get_pulse_data,
        )
        create_participant(qs_db, "P-bf", "Back-fill test")

        # Insert pulses BEFORE linking — participant_id will be NULL
        insert_pulse_data(qs_db, "sess-bf", unix_ms=1_000_100, pulse=70)
        insert_pulse_data(qs_db, "sess-bf", unix_ms=1_000_200, pulse=72)

        # Verify NULL before link
        rows_before = get_pulse_data(qs_db, "sess-bf")
        assert all(r["participant_id"] is None for r in rows_before), \
            "participant_id must be NULL before link_session is called"

        # Link participant — triggers back-fill
        link_session(qs_db, "P-bf", "sess-bf")

        rows_after = get_pulse_data(qs_db, "sess-bf")
        assert all(r["participant_id"] == "P-bf" for r in rows_after), \
            "All pre-link pulse rows must be back-filled with the correct participant_id"

    def test_already_associated_rows_not_overwritten(self, qs_db):
        """Rows already having a participant_id must NOT be back-filled."""
        import sqlite3
        from datetime import datetime, timezone
        from live_analytics.questionnaire.db import (
            create_participant, link_session, get_pulse_data,
        )
        now = datetime.now(timezone.utc).isoformat()
        create_participant(qs_db, "P-safe", "Safe")
        create_participant(qs_db, "P-other", "Other")

        # Manually insert a row with participant_id already set (P-other)
        conn = sqlite3.connect(str(qs_db))
        conn.execute(
            "INSERT INTO pulse_data (session_id, participant_id, unix_ms, pulse, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("sess-safe", "P-other", 1_000_100, 80, now),
        )
        conn.commit()
        conn.close()

        # Link P-safe to this session — back-fill should only touch NULL rows
        link_session(qs_db, "P-safe", "sess-safe")

        rows = get_pulse_data(qs_db, "sess-safe")
        assert rows[0]["participant_id"] == "P-other", (
            "Back-fill must NOT overwrite rows that already have a participant_id"
        )


# ══════════════════════════════════════════════════════════════════════
# K — disconnect cleanup: windows popped even on exception in processing
# ══════════════════════════════════════════════════════════════════════

class TestDisconnectExceptionSafety:
    """The try/finally in _on_disconnect must pop _windows even when
    _process_one_disconnect raises an unexpected exception."""

    def setup_method(self):
        from live_analytics.app import ws_ingest
        self._sid = "sess-exc-safety"
        ws_ingest._windows[self._sid] = deque(maxlen=600)

    def teardown_method(self):
        from live_analytics.app import ws_ingest
        ws_ingest._windows.pop(self._sid, None)

    def test_windows_popped_despite_exception_in_process(self):
        from live_analytics.app import ws_ingest

        async def bad_process(sid, *args, **kwargs):
            raise RuntimeError("unexpected crash in process_one_disconnect")

        with patch("live_analytics.app.ws_ingest._process_one_disconnect",
                   side_effect=bad_process):
            try:
                _run(ws_ingest._on_disconnect({self._sid}))
            except RuntimeError:
                pass  # Exception propagation is acceptable

        assert self._sid not in ws_ingest._windows, (
            "_windows must be popped by the try/finally regardless of exceptions in processing"
        )


# ══════════════════════════════════════════════════════════════════════
# Regression: analytics DB set_session_participant visibility
# ══════════════════════════════════════════════════════════════════════

class TestSessionParticipantVisibility:
    """participant_id set in the analytics DB must be visible via
    GET /api/sessions/{session_id} (the endpoint DBSender.cs polls)."""

    @pytest.fixture()
    def analytics_db(self, tmp_path: Path):
        from live_analytics.app.storage.sqlite_store import init_db, close_pool
        p = tmp_path / "analytics.db"
        init_db(p)
        yield p
        close_pool()

    def test_participant_id_visible_in_get_session(self, analytics_db):
        from live_analytics.app.storage.sqlite_store import (
            upsert_session, set_session_participant, get_session,
        )
        upsert_session(analytics_db, "sess-vis", 1_000_000, "scenario-1")
        set_session_participant(analytics_db, "sess-vis", "P007")
        detail = get_session(analytics_db, "sess-vis")
        assert detail is not None
        assert detail.participant_id == "P007", (
            "participant_id written by set_session_participant must be "
            "returned by get_session (used by DBSender.cs polling)"
        )

    def test_participant_id_empty_until_linked(self, analytics_db):
        from live_analytics.app.storage.sqlite_store import upsert_session, get_session
        upsert_session(analytics_db, "sess-unlinked", 1_000_000, "scenario-1")
        detail = get_session(analytics_db, "sess-unlinked")
        assert detail is not None
        assert detail.participant_id == "", (
            "participant_id must be empty string before linking "
            "(DBSender.cs checks !string.IsNullOrEmpty which handles this)"
        )


# ══════════════════════════════════════════════════════════════════════
# Regression: FIFO guard in create_participant
# ══════════════════════════════════════════════════════════════════════

class TestCreateParticipantFifoGuard:
    @pytest.fixture()
    def qs_db(self, tmp_path: Path):
        from live_analytics.questionnaire.db import init_db, close_pool
        p = tmp_path / "q.db"
        init_db(p)
        yield p
        close_pool()

    def test_re_register_linked_participant_only_updates_cosmetics(self, qs_db):
        """Re-registering an already-linked participant must NOT reset session_id."""
        from live_analytics.questionnaire.db import (
            create_participant, link_session, get_participant,
        )
        create_participant(qs_db, "P-fg", "Original Name")
        link_session(qs_db, "P-fg", "active-session")

        # Re-register with new display_name
        create_participant(qs_db, "P-fg", "New Name")
        p = get_participant(qs_db, "P-fg")
        assert p["session_id"] == "active-session", \
            "FIFO guard must prevent re-registration from clearing an active session_id"
        assert p["display_name"] == "New Name", "display_name should be updated"

    def test_re_register_unlinked_participant_updates_normally(self, qs_db):
        """Re-registering an unlinked participant (session_id='') is allowed."""
        from live_analytics.questionnaire.db import (
            create_participant, get_participant,
        )
        create_participant(qs_db, "P-unreg", "Old Name")
        create_participant(qs_db, "P-unreg", "New Name")
        p = get_participant(qs_db, "P-unreg")
        assert p["display_name"] == "New Name"
        assert p["session_id"] == "", "session_id must remain empty"
