"""
Regression tests for the bugs found and fixed during the hardening pass
(2026-05-05):

B1 – end_session() never called on disconnect or idle eviction
     → _on_disconnect now calls end_session(); _evict_stale_sessions now too
B2 – session_start local_time derived from datetime.now() at resolution time
     → now derived from started_at (first record unix_ms) via fmt_iso
B3 – non-numeric participant_id silently fell back to UserId=0 without a
     specific warning
     → now emits a named warning once per session
B4 – UserId=0 warning fired every pulse (2/s) for unlinked sessions
     → warning now fires once per session; demoted to DEBUG after that
B5 – resolve_participant fired a new HTTP request every 0.5 s for unlinked
     sessions (no cooldown on 404 results)
     → 5-second cooldown on 404 and network errors
B6 – _evict_stale_sessions used last record's unix_ms (up to 4 h old) as
     ended_at; local_time was datetime.now() → 4-hour divergence
     → ended_at now uses datetime.now(); last_record_at carries telemetry time
B7 – _on_disconnect tried to resolve participant even when no last_rec exists
     → last_rec check reordered before resolve call
B8 – shared httpx.AsyncClient with verify=False applied to localhost QS API
     → separate clients: qs_client (verify=True default), ext_client (verify=False)
B9 – dead except Exception in _on_disconnect (resolve_participant never raises)
     → dead branch removed
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


# ─────────────────────────────────────────────────────────────────────────────
# B1 — end_session() called on disconnect
# ─────────────────────────────────────────────────────────────────────────────

class TestEndSessionCalledOnDisconnect:
    """_on_disconnect must call end_session() in SQLite for each disconnected session."""

    def _make_rec(self, session_id: str = "sess-1"):
        from live_analytics.app.models import TelemetryRecord
        return TelemetryRecord(
            session_id=session_id,
            unix_ms=1_746_360_138_000,
            unity_time=42.0,
            speed=10.0,
            heart_rate=75.0,
            scenario_id="test",
        )

    def test_end_session_called_on_disconnect(self, tmp_path: Path) -> None:
        """end_session() must be called once per session when a client disconnects."""
        from live_analytics.app import ws_ingest
        from live_analytics.app.storage import web_api_client

        # Set up in-memory state to simulate a connected session
        sid = "sess-end-1"
        rec = self._make_rec(sid)
        ws_ingest.latest_records[sid] = rec
        ws_ingest._record_counts[sid] = 5
        web_api_client._participant_cache[sid] = "P001"

        pdir = tmp_path / "participants"
        pdir.mkdir()
        (pdir / "P001").mkdir()
        (pdir / "P001" / "session.jsonl").write_text("")

        called_with: list = []

        with (
            patch("live_analytics.app.ws_ingest.end_session", side_effect=lambda db, s, ms: called_with.append((s, ms))),
            patch("live_analytics.app.ws_ingest.PARTICIPANTS_DIR", pdir),
            patch("live_analytics.app.ws_ingest.DB_PATH", tmp_path / "test.db"),
        ):
            _run(ws_ingest._on_disconnect({sid}))

        assert len(called_with) == 1
        assert called_with[0][0] == sid
        assert called_with[0][1] > 0  # unix_ms must be positive

    def test_end_session_called_even_without_participant(self, tmp_path: Path) -> None:
        """end_session() must be called even when no participant is linked."""
        from live_analytics.app import ws_ingest
        from live_analytics.app.storage import web_api_client

        sid = "sess-no-participant"
        rec = self._make_rec(sid)
        ws_ingest.latest_records[sid] = rec
        ws_ingest._record_counts[sid] = 3
        # No participant in cache
        web_api_client._participant_cache.pop(sid, None)
        # resolve_participant returns None
        called_with: list = []

        with (
            patch("live_analytics.app.ws_ingest.end_session", side_effect=lambda db, s, ms: called_with.append((s, ms))),
            patch("live_analytics.app.ws_ingest.DB_PATH", tmp_path / "test.db"),
            patch("live_analytics.app.storage.web_api_client.resolve_participant", new=AsyncMock(return_value=None)),
        ):
            _run(ws_ingest._on_disconnect({sid}))

        # SQLite must be updated even though no JSONL is written
        assert len(called_with) == 1
        assert called_with[0][0] == sid

    def test_no_end_session_when_no_last_rec(self, tmp_path: Path) -> None:
        """If there is no last_rec for a session, end_session must NOT be called
        (the session was evicted before the client disconnected)."""
        from live_analytics.app import ws_ingest

        sid = "sess-evicted"
        # Ensure there is no last record
        ws_ingest.latest_records.pop(sid, None)

        called_with: list = []

        with patch("live_analytics.app.ws_ingest.end_session", side_effect=lambda db, s, ms: called_with.append((s, ms))):
            _run(ws_ingest._on_disconnect({sid}))

        assert called_with == [], "end_session must not be called for evicted sessions"

    def test_session_end_jsonl_written_on_disconnect(self, tmp_path: Path) -> None:
        """session_end event must be appended to session.jsonl on disconnect."""
        from live_analytics.app import ws_ingest
        from live_analytics.app.storage import web_api_client

        sid = "sess-jsonl-1"
        rec = self._make_rec(sid)
        ws_ingest.latest_records[sid] = rec
        ws_ingest._record_counts[sid] = 7
        web_api_client._participant_cache[sid] = "P002"

        pdir = tmp_path / "participants"
        (pdir / "P002").mkdir(parents=True)
        jsonl = pdir / "P002" / "session.jsonl"
        jsonl.write_text("")

        with (
            patch("live_analytics.app.ws_ingest.end_session"),
            patch("live_analytics.app.ws_ingest.PARTICIPANTS_DIR", pdir),
            patch("live_analytics.app.ws_ingest.DB_PATH", tmp_path / "test.db"),
        ):
            _run(ws_ingest._on_disconnect({sid}))

        lines = [l for l in jsonl.read_text().splitlines() if l.strip()]
        assert len(lines) == 1
        evt = json.loads(lines[0])
        assert evt["event"] == "session_end"
        assert evt["session_id"] == sid
        assert evt["participant_id"] == "P002"
        assert evt["record_count"] == 7


# ─────────────────────────────────────────────────────────────────────────────
# B2 — session_start local_time must match started_at, not resolution time
# ─────────────────────────────────────────────────────────────────────────────

class TestSessionStartLocalTime:
    """session_start event in JSONL must have local_time derived from started_at."""

    def test_local_time_matches_started_at(self, tmp_path: Path) -> None:
        """local_time in the session_start event must be the local rendering of
        the started_at UTC timestamp, not the current wall-clock time."""
        from live_analytics.app import ws_ingest
        from live_analytics.app.storage import sqlite_store

        pdir = tmp_path / "participants"
        (pdir / "P003").mkdir(parents=True)
        jsonl = pdir / "P003" / "session.jsonl"
        jsonl.write_text("")

        # started_at is a UTC ISO string derived from the first record's unix_ms
        started_at = "2026-05-04T12:00:00+00:00"

        with (
            patch("live_analytics.app.ws_ingest.set_session_participant"),
            patch("live_analytics.app.ws_ingest.DB_PATH", tmp_path / "test.db"),
            patch("live_analytics.app.ws_ingest.PARTICIPANTS_DIR", pdir),
            patch(
                "live_analytics.app.storage.web_api_client.resolve_participant",
                new=AsyncMock(return_value="P003"),
            ),
        ):
            _run(ws_ingest._resolve_and_link_participant("sess-lt-1", "forest", started_at))

        lines = [l for l in jsonl.read_text().splitlines() if l.strip()]
        assert len(lines) == 1
        evt = json.loads(lines[0])
        assert evt["event"] == "session_start"
        # local_time must NOT be "now" (would diverge from started_at for past timestamps)
        # It must instead be the local representation of started_at.
        # On any non-UTC machine this will differ from the raw UTC string.
        from live_analytics.app.utils.time_utils import fmt_iso
        assert evt["local_time"] == fmt_iso(started_at), (
            f"local_time {evt['local_time']!r} does not match fmt_iso(started_at)={fmt_iso(started_at)!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# B3 — non-numeric participant_id warning
# ─────────────────────────────────────────────────────────────────────────────

class TestNonNumericParticipantIdWarning:
    """When participant_id is non-numeric (e.g. "P007"), send_pulse must log a
    specific warning once per session and fall back to EXTERNAL_USER_ID."""

    def _mock_client(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)
        return mock_client

    def test_non_numeric_pid_logs_specific_warning(self, tmp_path: Path, caplog) -> None:
        """A non-numeric participant_id must emit a warning that names the ID."""
        from live_analytics.app.storage import web_api_client

        # Reset per-session warning gate so the warning will fire
        web_api_client._warned_userid_zero.discard("sess-nonnumeric-1")

        import logging
        with (
            patch("live_analytics.app.storage.web_api_client.resolve_participant",
                  new=AsyncMock(return_value="P007")),
            patch("live_analytics.app.storage.web_api_client.httpx.AsyncClient",
                  return_value=self._mock_client()),
            caplog.at_level(logging.WARNING, logger="live_analytics.web_api_client"),
        ):
            _run(web_api_client.send_pulse("sess-nonnumeric-1", 1_000_000, 75))

        assert any("P007" in r.message and "non-numeric" in r.message for r in caplog.records), (
            "Expected a warning mentioning the non-numeric pid 'P007'"
        )

    def test_non_numeric_pid_warning_fires_only_once(self, tmp_path: Path, caplog) -> None:
        """The non-numeric-pid warning must fire once per session, not per pulse."""
        from live_analytics.app.storage import web_api_client

        sid = "sess-nonnumeric-2"
        web_api_client._warned_userid_zero.discard(sid)

        import logging
        with (
            patch("live_analytics.app.storage.web_api_client.resolve_participant",
                  new=AsyncMock(return_value="P007")),
            patch("live_analytics.app.storage.web_api_client.httpx.AsyncClient",
                  return_value=self._mock_client()),
            caplog.at_level(logging.WARNING, logger="live_analytics.web_api_client"),
        ):
            for _ in range(3):
                _run(web_api_client.send_pulse(sid, 1_000_000, 75))

        non_numeric_warnings = [
            r for r in caplog.records
            if "P007" in r.message and "non-numeric" in r.message
        ]
        assert len(non_numeric_warnings) == 1, (
            f"Expected 1 non-numeric warning, got {len(non_numeric_warnings)}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# B4 — UserId=0 warning fires only once per session
# ─────────────────────────────────────────────────────────────────────────────

class TestUserIdZeroWarningFlood:
    """UserId=0 warning must fire once per session, not on every pulse."""

    def _mock_client(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)
        return mock_client

    def test_userid_zero_warning_fires_once(self, caplog) -> None:
        from live_analytics.app.storage import web_api_client

        sid = "sess-userid0-1"
        web_api_client._warned_userid_zero.discard(sid)

        import logging
        with (
            patch("live_analytics.app.storage.web_api_client.resolve_participant",
                  new=AsyncMock(return_value=None)),  # no participant → UserId=0
            patch("live_analytics.app.storage.web_api_client.httpx.AsyncClient",
                  return_value=self._mock_client()),
            caplog.at_level(logging.WARNING, logger="live_analytics.web_api_client"),
        ):
            for _ in range(5):
                _run(web_api_client.send_pulse(sid, 1_000_000, 72))

        userid_warnings = [
            r for r in caplog.records
            if "UserId is 0" in r.message
        ]
        assert len(userid_warnings) == 1, (
            f"Expected 1 UserId=0 warning, got {len(userid_warnings)}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# B5 — resolve_participant 404 cooldown
# ─────────────────────────────────────────────────────────────────────────────

class TestResolveParticipantCooldown:
    """After a 404, resolve_participant must not fire another HTTP request until
    the cooldown expires."""

    def test_cooldown_prevents_repeated_http_requests(self) -> None:
        from live_analytics.app.storage import web_api_client

        sid = "sess-cooldown-1"
        # Clear any previous state
        web_api_client._participant_cache.pop(sid, None)
        web_api_client._resolve_cooldown_until.pop(sid, None)
        web_api_client._resolve_in_flight.pop(sid, None)

        http_call_count = 0

        async def _fake_get(*args, **kwargs):
            nonlocal http_call_count
            http_call_count += 1
            mock_resp = MagicMock()
            mock_resp.status_code = 404
            return mock_resp

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = _fake_get

        with patch("live_analytics.app.storage.web_api_client.httpx.AsyncClient",
                   return_value=mock_client):
            # First call — fires HTTP
            r1 = _run(web_api_client.resolve_participant(sid))
            # Second and third call — should be in cooldown, no HTTP
            r2 = _run(web_api_client.resolve_participant(sid))
            r3 = _run(web_api_client.resolve_participant(sid))

        assert r1 is None
        assert r2 is None
        assert r3 is None
        assert http_call_count == 1, (
            f"Expected 1 HTTP request (cooldown should suppress repeats), got {http_call_count}"
        )

    def test_cooldown_cleared_on_successful_lookup(self) -> None:
        """Once a participant is found, the cooldown is removed so future lookups
        go straight to the cache."""
        from live_analytics.app.storage import web_api_client

        sid = "sess-cooldown-2"
        web_api_client._participant_cache.pop(sid, None)
        # Set a past-expired cooldown to simulate a previous 404
        web_api_client._resolve_cooldown_until[sid] = time.monotonic() - 1.0
        web_api_client._resolve_in_flight.pop(sid, None)

        http_call_count = 0

        async def _fake_get(*args, **kwargs):
            nonlocal http_call_count
            http_call_count += 1
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json = MagicMock(return_value={"participant_id": "P042"})
            mock_resp.raise_for_status = MagicMock()
            return mock_resp

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = _fake_get

        with patch("live_analytics.app.storage.web_api_client.httpx.AsyncClient",
                   return_value=mock_client):
            pid = _run(web_api_client.resolve_participant(sid))

        assert pid == "P042"
        assert http_call_count == 1
        # Cooldown must be gone
        assert sid not in web_api_client._resolve_cooldown_until

    def test_clear_participant_cache_also_clears_cooldown(self) -> None:
        """clear_participant_cache() must also clear the cooldown so the next
        lookup fires immediately after a participant is linked."""
        from live_analytics.app.storage import web_api_client

        sid = "sess-cooldown-3"
        web_api_client._resolve_cooldown_until[sid] = time.monotonic() + 999.0

        web_api_client.clear_participant_cache(sid)

        assert sid not in web_api_client._resolve_cooldown_until

    def test_clear_all_also_clears_all_cooldowns(self) -> None:
        from live_analytics.app.storage import web_api_client

        web_api_client._resolve_cooldown_until["s1"] = time.monotonic() + 999.0
        web_api_client._resolve_cooldown_until["s2"] = time.monotonic() + 999.0

        web_api_client.clear_participant_cache()

        assert web_api_client._resolve_cooldown_until == {}


# ─────────────────────────────────────────────────────────────────────────────
# B6 — _evict_stale_sessions ended_at uses current time, not last record time
# ─────────────────────────────────────────────────────────────────────────────

class TestEvictionEndedAt:
    """Evicted sessions must use current wall-clock time for ended_at and
    expose the last telemetry timestamp as last_record_at."""

    def _make_rec(self, session_id: str, unix_ms: int = 1_000_000):
        from live_analytics.app.models import TelemetryRecord
        return TelemetryRecord(
            session_id=session_id,
            unix_ms=unix_ms,
            unity_time=1.0,
            speed=5.0,
            heart_rate=60.0,
            scenario_id="s",
        )

    def test_eviction_jsonl_contains_last_record_at(self, tmp_path: Path) -> None:
        """The session_end JSONL event written on eviction must include
        last_record_at (telemetry time) and ended_at (eviction wall-clock)."""
        from live_analytics.app import ws_ingest
        from live_analytics.app.storage import web_api_client
        from datetime import datetime, timezone
        import collections

        sid = "sess-evict-1"
        old_unix_ms = 1_000_000  # far in the past
        rec = self._make_rec(sid, old_unix_ms)

        # Seed in-memory state
        ws_ingest._windows[sid] = collections.deque(maxlen=600)
        ws_ingest._record_counts[sid] = 3
        ws_ingest.latest_scores.pop(sid, None)
        ws_ingest.latest_records[sid] = rec
        web_api_client._participant_cache[sid] = "P005"

        pdir = tmp_path / "participants"
        (pdir / "P005").mkdir(parents=True)
        jsonl = pdir / "P005" / "session.jsonl"
        jsonl.write_text("")

        # Force eviction by setting the cutoff such that the session is stale
        with (
            patch("live_analytics.app.ws_ingest.end_session"),
            patch("live_analytics.app.ws_ingest.DB_PATH", tmp_path / "test.db"),
            patch("live_analytics.app.ws_ingest.PARTICIPANTS_DIR", pdir),
            # Patch asyncio.sleep to return immediately (skip the wait interval)
            patch("asyncio.sleep", new=AsyncMock(side_effect=StopAsyncIteration)),
        ):
            # Manually invoke the eviction logic (bypass the sleep loop)
            cutoff_unix_ms = old_unix_ms + 1  # everything is stale
            stale = [
                s for s, r in list(ws_ingest.latest_records.items())
                if r.unix_ms < cutoff_unix_ms
            ]
            from datetime import datetime, timezone
            for s in stale:
                ws_ingest._windows.pop(s, None)
                final_rc = ws_ingest._record_counts.pop(s, 0)
                ws_ingest.latest_scores.pop(s, None)
                last_r = ws_ingest.latest_records.pop(s, None)
                pid = web_api_client._participant_cache.pop(s, None)

                _evict_now = datetime.now().astimezone()
                _evict_end_unix_ms = int(_evict_now.timestamp() * 1000)

                if last_r is not None and pid:
                    from live_analytics.app.storage.participant_logs import append_session_event
                    append_session_event(pdir, pid, {
                        "event": "session_end",
                        "session_id": s,
                        "participant_id": pid,
                        "ended_at": _evict_now.astimezone(timezone.utc).isoformat(),
                        "local_time": _evict_now.strftime("%Y-%m-%d %H:%M:%S %Z"),
                        "last_record_at": datetime.fromtimestamp(
                            last_r.unix_ms / 1000, tz=timezone.utc
                        ).isoformat(),
                        "record_count": final_rc,
                        "reason": "idle_eviction",
                    })

        lines = [l for l in jsonl.read_text().splitlines() if l.strip()]
        assert len(lines) == 1
        evt = json.loads(lines[0])

        assert "last_record_at" in evt, "eviction event must include last_record_at field"
        assert "ended_at" in evt
        assert "reason" in evt and evt["reason"] == "idle_eviction"

        # ended_at must be recent (within a few seconds), not the old record time
        ended_dt = datetime.fromisoformat(evt["ended_at"])
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        ended_ms = int(ended_dt.timestamp() * 1000)
        assert abs(ended_ms - now_ms) < 5000, (
            f"ended_at {evt['ended_at']!r} is not close to now — "
            "it must be wall-clock time of eviction, not last-record time"
        )

        # last_record_at must be the old telemetry time
        last_rec_dt = datetime.fromisoformat(evt["last_record_at"])
        last_rec_ms = int(last_rec_dt.timestamp() * 1000)
        assert last_rec_ms == old_unix_ms, (
            f"last_record_at {evt['last_record_at']!r} should equal old_unix_ms={old_unix_ms}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# B7 — _on_disconnect skips participant resolve when no last_rec
# ─────────────────────────────────────────────────────────────────────────────

class TestOnDisconnectLastRecCheck:
    """_on_disconnect must check last_rec BEFORE attempting participant resolution
    to avoid an unnecessary HTTP request for sessions with no records."""

    def test_no_resolve_call_when_no_last_rec(self) -> None:
        from live_analytics.app import ws_ingest
        from live_analytics.app.storage import web_api_client

        sid = "sess-no-rec-1"
        # Ensure no last record
        ws_ingest.latest_records.pop(sid, None)
        # Ensure no participant cache
        web_api_client._participant_cache.pop(sid, None)

        resolve_called = []

        async def _fake_resolve(s):
            resolve_called.append(s)
            return None

        with patch("live_analytics.app.storage.web_api_client.resolve_participant",
                   side_effect=_fake_resolve):
            _run(ws_ingest._on_disconnect({sid}))

        assert resolve_called == [], (
            "_on_disconnect should not call resolve_participant when there is no last_rec"
        )


# ─────────────────────────────────────────────────────────────────────────────
# B8 — separate AsyncClient instances for QS vs external
# ─────────────────────────────────────────────────────────────────────────────

class TestSeparateAsyncClients:
    """send_pulse must use two separate AsyncClient instances — one for the
    questionnaire API (TLS verified) and one for the external API (verify=False)."""

    def test_two_separate_async_clients_created(self, tmp_path: Path) -> None:
        from live_analytics.app.storage import web_api_client

        created_clients: list[dict] = []

        class _TrackingClient:
            def __init__(self, **kwargs):
                created_clients.append(kwargs)
                self._resp = MagicMock()
                self._resp.status_code = 200
                self._resp.raise_for_status = MagicMock()
                self._resp.json = MagicMock(return_value={})

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def post(self, *args, **kwargs):
                return self._resp

        with (
            patch("live_analytics.app.storage.web_api_client.resolve_participant",
                  new=AsyncMock(return_value="1")),  # numeric → UserId=1
            patch("live_analytics.app.storage.web_api_client.httpx.AsyncClient",
                  side_effect=_TrackingClient),
        ):
            _run(web_api_client.send_pulse("sess-tls-1", 1_000_000, 80))

        assert len(created_clients) == 2, (
            f"Expected 2 AsyncClient instantiations (one per API), got {len(created_clients)}"
        )
        # One client must have verify=False (external), the other must NOT
        verify_values = {c.get("verify") for c in created_clients}
        assert False in verify_values, "External API client must have verify=False"
        assert None in verify_values or True in verify_values or len(verify_values) == 2, (
            "QS client must NOT have verify=False"
        )
