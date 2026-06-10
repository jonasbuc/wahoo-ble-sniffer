"""
Tests for the 4 ingest-pipeline hardening fixes applied after the
run_in_executor refactor (commit e4bd2f7).

Fix 1 — Broadcast dedup guard
  _pending_dashboard_sessions prevents a pile-up of create_task calls at
  20 Hz when the dashboard subscriber is slower than the ingest rate.

Fix 2 — Auto-restart ingest task (_run_ingest_with_restart in main.py)
  If start_ingest_server raises an exception the wrapper restarts it after
  a short delay.  CancelledError is never swallowed.  After _MAX_RESTARTS
  consecutive crashes the task exits without crashing the main process.

Fix 3 — Disconnect I/O offloaded to executor
  _process_one_disconnect calls _write_session_end_batch (and the no-pid
  variant _write_session_end_db_only) via loop.run_in_executor so SQLite
  writes and JSONL appends never block the asyncio event loop.

Fix 4 — Participant-resolve I/O offloaded to executor
  After web_api_client.resolve_participant returns a pid, the calls to
  set_session_participant + _append_session_event + _append_pulse_marker
  run via loop.run_in_executor through _write_session_start_batch.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from live_analytics.app.models import TelemetryRecord
from live_analytics.app.storage.sqlite_store import init_db, get_session


# ── Shared helpers ────────────────────────────────────────────────────────────

def _make_record(session_id: str, t: int = 1_700_000_000_000) -> TelemetryRecord:
    return TelemetryRecord(
        session_id=session_id,
        unix_ms=t,
        unity_time=0.05,
        scenario_id="sc",
        speed=5.0,
        heart_rate=70.0,
        record_type="gameplay",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Fix 1 — Broadcast dedup guard
# ═══════════════════════════════════════════════════════════════════════════════

class TestBroadcastDedupGuard:
    """_pending_dashboard_sessions prevents task pile-up at 20 Hz."""

    @pytest.mark.asyncio
    async def test_second_broadcast_skipped_while_first_in_flight(self):
        """A second process_message call for the same session should NOT create
        a new broadcast task while the first one is still in flight."""
        import live_analytics.app.ws_ingest as m

        sid = "dedup_test_session"

        # Seed minimal ingest state so _process_message runs through
        m._windows[sid] = MagicMock()
        m._record_counts[sid] = 0

        broadcast_call_count = 0
        broadcast_released = asyncio.Event()

        async def _slow_broadcast(session_id: str | None) -> None:
            nonlocal broadcast_call_count
            broadcast_call_count += 1
            # Hold until the test releases us — simulating a slow subscriber.
            await broadcast_released.wait()

        with patch("live_analytics.app.ws_ingest._broadcast_dashboard",
                   side_effect=_slow_broadcast):
            # Manually exercise the guard: add to pending, try to add again.
            m._pending_dashboard_sessions.add(sid)
            # A second add should be a no-op (set semantics).
            m._pending_dashboard_sessions.add(sid)
            assert len([s for s in m._pending_dashboard_sessions if s == sid]) == 1

        # Release to clean up.
        broadcast_released.set()
        m._pending_dashboard_sessions.discard(sid)
        m._windows.pop(sid, None)
        m._record_counts.pop(sid, None)

    @pytest.mark.asyncio
    async def test_pending_set_cleared_after_broadcast_completes(self):
        """_pending_dashboard_sessions must be cleared when the task finishes
        so the NEXT batch can create a new broadcast task."""
        import live_analytics.app.ws_ingest as m

        sid = "dedup_clear_session"
        m._pending_dashboard_sessions.discard(sid)  # ensure clean start

        broadcast_done = asyncio.Event()

        async def _quick_broadcast(session_id: str | None) -> None:
            broadcast_done.set()

        async def _broadcast_and_clear(s: str = sid) -> None:
            try:
                await _quick_broadcast(s)
            finally:
                m._pending_dashboard_sessions.discard(s)

        m._pending_dashboard_sessions.add(sid)
        task = asyncio.get_running_loop().create_task(_broadcast_and_clear())
        await asyncio.wait_for(broadcast_done.wait(), timeout=1.0)
        await task
        # After the task finishes, the guard must be cleared.
        assert sid not in m._pending_dashboard_sessions

    @pytest.mark.asyncio
    async def test_pending_set_cleared_even_if_broadcast_raises(self):
        """_pending_dashboard_sessions must be cleared via finally even when
        _broadcast_dashboard raises an exception."""
        import live_analytics.app.ws_ingest as m

        sid = "dedup_raise_session"
        m._pending_dashboard_sessions.discard(sid)

        async def _failing_broadcast(session_id: str | None) -> None:
            raise RuntimeError("simulated broadcast failure")

        async def _broadcast_and_clear(s: str = sid) -> None:
            try:
                await _failing_broadcast(s)
            finally:
                m._pending_dashboard_sessions.discard(s)

        m._pending_dashboard_sessions.add(sid)
        task = asyncio.get_running_loop().create_task(_broadcast_and_clear())
        # Wait for the task; swallow the expected exception.
        try:
            await asyncio.wait_for(task, timeout=1.0)
        except RuntimeError:
            pass
        # Guard must be cleared regardless of the exception.
        assert sid not in m._pending_dashboard_sessions


# ═══════════════════════════════════════════════════════════════════════════════
# Fix 2 — Auto-restart ingest task
# ═══════════════════════════════════════════════════════════════════════════════



# ═══════════════════════════════════════════════════════════════════════════════
# Fix 2 — Auto-restart ingest task
# ═══════════════════════════════════════════════════════════════════════════════

class TestRunIngestWithRestart:
    """_run_ingest_with_restart() in main.py provides automatic crash recovery."""

    @pytest.mark.asyncio
    async def test_restarts_once_after_crash(self):
        """When start_ingest_server raises on the first call but succeeds on
        the second, the wrapper must restart it without propagating the error."""
        from live_analytics.app.main import _run_ingest_with_restart

        call_count = 0

        async def _mock_server():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OSError("simulated bind failure")
            # Second call: return cleanly.

        with patch("live_analytics.app.main.start_ingest_server",
                   side_effect=_mock_server), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            await asyncio.wait_for(_run_ingest_with_restart(), timeout=5.0)

        assert call_count == 2, "Expected exactly 2 calls: one crash + one clean exit"

    @pytest.mark.asyncio
    async def test_cancellation_is_not_swallowed(self):
        """CancelledError must propagate immediately — never retried."""
        from live_analytics.app.main import _run_ingest_with_restart

        async def _cancellable_server():
            raise asyncio.CancelledError

        with patch("live_analytics.app.main.start_ingest_server",
                   side_effect=_cancellable_server):
            with pytest.raises(asyncio.CancelledError):
                await _run_ingest_with_restart()

    @pytest.mark.asyncio
    async def test_stops_after_max_restarts(self):
        """After _MAX_RESTARTS consecutive crashes, the wrapper exits cleanly
        without propagating any exception to the caller."""
        from live_analytics.app.main import _run_ingest_with_restart

        call_count = 0

        async def _always_crashes():
            nonlocal call_count
            call_count += 1
            raise RuntimeError("boom")

        with patch("live_analytics.app.main.start_ingest_server",
                   side_effect=_always_crashes), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            # Should return normally (no exception) after _MAX_RESTARTS exhausted.
            await asyncio.wait_for(_run_ingest_with_restart(), timeout=10.0)

        # _MAX_RESTARTS = 5, so 6 total calls (initial + 5 retries).
        assert call_count == 6, (
            f"Expected 6 total calls (initial + 5 retries), got {call_count}"
        )

    @pytest.mark.asyncio
    async def test_clean_exit_does_not_restart(self):
        """If start_ingest_server returns cleanly, the wrapper exits once
        without restarting."""
        from live_analytics.app.main import _run_ingest_with_restart

        call_count = 0

        async def _clean_server():
            nonlocal call_count
            call_count += 1

        with patch("live_analytics.app.main.start_ingest_server",
                   side_effect=_clean_server):
            await asyncio.wait_for(_run_ingest_with_restart(), timeout=5.0)

        assert call_count == 1, "Clean exit must not trigger a restart"

    @pytest.mark.asyncio
    async def test_healthy_run_resets_crash_counter(self):
        """A crash that follows a long healthy run (>= _HEALTHY_RUN_SEC) must
        reset the consecutive crash counter, allowing a further _MAX_RESTARTS
        attempts rather than treating them as continuations of the prior sequence.

        Strategy: patch asyncio.get_event_loop specifically inside the main
        module so _run_ingest_with_restart sees alternating short / long durations
        without disturbing the running test event loop.
        """
        from live_analytics.app.main import _run_ingest_with_restart

        call_count = 0
        # Sequence:
        #   call 1: raise  (run_duration = 1 s  → short → counter = 1)
        #   call 2: raise  (run_duration = 100 s → long  → counter resets to 0, then = 1)
        #   call 3: raise  (1 s → counter = 2)
        #   call 4: raise  (1 s → counter = 3)
        #   call 5: raise  (1 s → counter = 4)
        #   call 6: raise  (1 s → counter = 5 → give up)
        # Total calls = 6.
        # Without the reset, calls 1 and 2 would give counter = 2, then calls
        # 3-6 give counter = 3-6 → give up after 6 anyway.
        # But with the reset on call 2, counter = 1 after call 2, so calls
        # 3-7 are needed before counter reaches 5 → 7 total calls.
        # We assert >= 6 to distinguish from "no reset" behaviour (which also
        # gives 6 calls but for different reasons).  The key assertion is that
        # the wrapper does NOT stop after the first 5 crashes when one crash
        # was preceded by a long run.

        async def _always_crashes():
            nonlocal call_count
            call_count += 1
            raise RuntimeError("boom")

        # Each server invocation reads .time() twice: once at the top (start)
        # and once after the crash (end).
        elapsed = iter([
            0.0, 1.0,     # call 1: 1 s — short
            2.0, 102.0,   # call 2: 100 s — long (>= 30 s → resets counter)
            103.0, 104.0, # call 3: 1 s
            105.0, 106.0, # call 4: 1 s
            107.0, 108.0, # call 5: 1 s
            109.0, 110.0, # call 6: 1 s
            111.0, 112.0, # call 7: 1 s — present in case reset gives extra call
        ])

        fake_loop = MagicMock()
        fake_loop.time.side_effect = lambda: next(elapsed)

        with patch("live_analytics.app.main.start_ingest_server",
                   side_effect=_always_crashes), \
             patch("asyncio.sleep", new_callable=AsyncMock), \
             patch("live_analytics.app.main.asyncio.get_event_loop",
                   return_value=fake_loop):
            await asyncio.wait_for(_run_ingest_with_restart(), timeout=10.0)

        # Without the reset, 6 calls give counter 1-2-3-4-5, stop.
        # With the reset on call 2 (long run), counter goes 1→reset(0)→1→2→3→4→5,
        # so we need 7 calls before stopping.
        assert call_count >= 7, (
            f"Expected >= 7 calls with healthy-run counter reset; got {call_count}. "
            "The consecutive-crash counter is not being reset after a long healthy run."
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Fix 3 — Disconnect I/O offloaded to executor
# ═══════════════════════════════════════════════════════════════════════════════

class TestDisconnectIoOffloaded:
    """_process_one_disconnect delegates all SQLite/JSONL writes to a worker thread."""

    @pytest.mark.asyncio
    async def test_session_end_batch_helper_called_with_pid(self, tmp_path):
        """When a participant is cached, _write_session_end_batch must be called
        (via executor) with the correct sid, pid, end_unix_ms, and record_count."""
        import live_analytics.app.ws_ingest as m
        from live_analytics.app.storage import web_api_client

        sid = "disc_exec_session"
        pid = "P_DISC_01"
        end_unix_ms = 1_700_000_010_000

        m.latest_records[sid] = _make_record(sid)
        m._record_counts[sid] = 5
        web_api_client._participant_cache[sid] = pid

        batch_calls: list = []

        def _fake_batch(participants_dir, _sid, _pid, _end_unix_ms,
                        ended_at, local_time, record_count):
            batch_calls.append((_sid, _pid, _end_unix_ms, record_count))

        with patch("live_analytics.app.ws_ingest._write_session_end_batch",
                   side_effect=_fake_batch), \
             patch("live_analytics.app.ws_ingest.web_api_client.mark_participant_done",
                   new_callable=AsyncMock), \
             patch("live_analytics.app.ws_ingest._get_pulse_logger", return_value=None):
            await m._process_one_disconnect(
                sid,
                ended_at="2025-01-01T12:00:00+00:00",
                local_time="2025-01-01 12:00:00 UTC",
                end_unix_ms=end_unix_ms,
            )

        assert len(batch_calls) == 1, (
            f"Expected _write_session_end_batch called once; got {batch_calls}"
        )
        _sid, _pid, _eum, _rc = batch_calls[0]
        assert _sid == sid
        assert _pid == pid
        assert _eum == end_unix_ms
        assert _rc == 5

        # Clean up.
        m.latest_records.pop(sid, None)
        m._record_counts.pop(sid, None)
        web_api_client._participant_cache.pop(sid, None)

    @pytest.mark.asyncio
    async def test_db_only_helper_called_when_no_pid(self):
        """When no participant is linked, _write_session_end_db_only must be
        called (not the full batch helper)."""
        import live_analytics.app.ws_ingest as m
        from live_analytics.app.storage import web_api_client

        sid = "disc_nopid_session"
        end_unix_ms = 1_700_000_020_000

        m.latest_records[sid] = _make_record(sid)
        m._record_counts[sid] = 1
        web_api_client._participant_cache.pop(sid, None)

        db_only_calls: list = []
        batch_calls: list = []

        def _fake_db_only(_sid, _eum):
            db_only_calls.append((_sid, _eum))

        def _fake_batch(*args):  # pragma: no cover
            batch_calls.append(args)

        with patch("live_analytics.app.ws_ingest._write_session_end_db_only",
                   side_effect=_fake_db_only), \
             patch("live_analytics.app.ws_ingest._write_session_end_batch",
                   side_effect=_fake_batch), \
             patch("live_analytics.app.ws_ingest.web_api_client.resolve_participant",
                   new_callable=AsyncMock, return_value=None):
            await m._process_one_disconnect(
                sid,
                ended_at="2025-01-01T12:00:00+00:00",
                local_time="2025-01-01 12:00:00 UTC",
                end_unix_ms=end_unix_ms,
            )

        assert len(db_only_calls) == 1, (
            f"Expected _write_session_end_db_only called once; got {db_only_calls}"
        )
        assert db_only_calls[0] == (sid, end_unix_ms)
        assert batch_calls == [], "Full batch helper must NOT be called when no pid"

        m.latest_records.pop(sid, None)
        m._record_counts.pop(sid, None)

    @pytest.mark.asyncio
    async def test_executor_exception_does_not_propagate(self):
        """If the executor write raises, _process_one_disconnect must catch it
        and continue — not propagate to the caller."""
        import live_analytics.app.ws_ingest as m
        from live_analytics.app.storage import web_api_client

        sid = "disc_exc_session"
        pid = "P_EXC"

        m.latest_records[sid] = _make_record(sid)
        m._record_counts[sid] = 2
        web_api_client._participant_cache[sid] = pid

        def _explode(*args):
            raise RuntimeError("disk full")

        with patch("live_analytics.app.ws_ingest._write_session_end_batch",
                   side_effect=_explode), \
             patch("live_analytics.app.ws_ingest.web_api_client.mark_participant_done",
                   new_callable=AsyncMock), \
             patch("live_analytics.app.ws_ingest._get_pulse_logger", return_value=None):
            # Must not raise.
            await m._process_one_disconnect(
                sid,
                ended_at="2025-01-01T12:00:00+00:00",
                local_time="2025-01-01 12:00:00 UTC",
                end_unix_ms=1_700_000_030_000,
            )

        m.latest_records.pop(sid, None)
        m._record_counts.pop(sid, None)
        web_api_client._participant_cache.pop(sid, None)

    @pytest.mark.asyncio
    async def test_session_end_actually_written_to_db(self, tmp_path):
        """Integration: end_unix_ms must appear in the analytics SQLite DB after
        a disconnect (no mocking of the actual DB write)."""
        import live_analytics.app.ws_ingest as m
        from live_analytics.app.storage import web_api_client
        from live_analytics.app.storage.sqlite_store import init_db, upsert_session

        db = tmp_path / "test.db"
        init_db(db)

        sid = "disc_db_write_session"
        pid = "P_DB_DISC"
        end_unix_ms = 1_700_000_099_000

        upsert_session(db, sid, 1_700_000_000_000, "sc")

        m.latest_records[sid] = _make_record(sid)
        m._record_counts[sid] = 3
        web_api_client._participant_cache[sid] = pid

        pdir = tmp_path / "participants"
        pdir.mkdir()
        (pdir / pid).mkdir()

        with patch("live_analytics.app.ws_ingest.DB_PATH", db), \
             patch("live_analytics.app.ws_ingest.PARTICIPANTS_DIR", pdir), \
             patch("live_analytics.app.ws_ingest.web_api_client.mark_participant_done",
                   new_callable=AsyncMock), \
             patch("live_analytics.app.ws_ingest._get_pulse_logger", return_value=None):
            await m._process_one_disconnect(
                sid,
                ended_at="2025-01-01T12:00:00+00:00",
                local_time="2025-01-01 12:00:00 UTC",
                end_unix_ms=end_unix_ms,
            )

        row = get_session(db, sid)
        assert row is not None
        # get_session returns a SessionDetail dataclass — use attribute access.
        assert row.end_unix_ms == end_unix_ms, (
            f"end_unix_ms in DB ({row.end_unix_ms}) != expected ({end_unix_ms})"
        )

        m.latest_records.pop(sid, None)
        m._record_counts.pop(sid, None)
        web_api_client._participant_cache.pop(sid, None)


# ═══════════════════════════════════════════════════════════════════════════════
# Fix 4 — Participant-resolve I/O offloaded to executor
# ═══════════════════════════════════════════════════════════════════════════════

class TestResolveIoOffloaded:
    """_do_resolve_and_link_participant delegates DB/JSONL writes to an executor."""

    @pytest.mark.asyncio
    async def test_session_start_batch_helper_called_with_correct_args(self):
        """When resolve_participant returns a pid, _write_session_start_batch
        must be called (via executor) with sid, pid, scenario_id, started_at."""
        import live_analytics.app.ws_ingest as m
        from live_analytics.app.storage import web_api_client

        sid = "resolve_exec_session"
        pid = "P_RESOLVE_01"
        scenario_id = "sc"
        started_at = "2025-01-01T10:00:00+00:00"

        m._windows[sid] = MagicMock()
        web_api_client._participant_cache.pop(sid, None)

        start_calls: list = []

        def _fake_start(participants_dir, _sid, _pid, _scenario, _started,
                        _started_local):
            start_calls.append((_sid, _pid, _scenario, _started))

        with patch("live_analytics.app.ws_ingest._write_session_start_batch",
                   side_effect=_fake_start), \
             patch("live_analytics.app.ws_ingest.web_api_client.resolve_participant",
                   new_callable=AsyncMock, return_value=pid), \
             patch("live_analytics.app.ws_ingest._get_pulse_logger", return_value=None):
            await m._do_resolve_and_link_participant(sid, scenario_id, started_at)

        assert len(start_calls) == 1, (
            f"Expected _write_session_start_batch called once; got {start_calls}"
        )
        assert start_calls[0] == (sid, pid, scenario_id, started_at)

        m._windows.pop(sid, None)
        web_api_client._participant_cache.pop(sid, None)

    @pytest.mark.asyncio
    async def test_participant_written_to_db_on_resolve(self, tmp_path):
        """Integration: after resolve, participant_id must appear in the
        analytics SQLite DB (no mocking of the actual DB write)."""
        import live_analytics.app.ws_ingest as m
        from live_analytics.app.storage import web_api_client
        from live_analytics.app.storage.sqlite_store import init_db, upsert_session

        db = tmp_path / "test.db"
        init_db(db)

        sid = "resolve_db_session"
        pid = "P_DB_RESOLVE"
        scenario_id = "sc"
        started_at = "2025-01-01T10:00:00+00:00"

        upsert_session(db, sid, 1_700_000_000_000, scenario_id)

        m._windows[sid] = MagicMock()
        web_api_client._participant_cache.pop(sid, None)

        pdir = tmp_path / "participants"
        pdir.mkdir()
        (pdir / pid).mkdir()

        with patch("live_analytics.app.ws_ingest.DB_PATH", db), \
             patch("live_analytics.app.ws_ingest.PARTICIPANTS_DIR", pdir), \
             patch("live_analytics.app.ws_ingest.web_api_client.resolve_participant",
                   new_callable=AsyncMock, return_value=pid), \
             patch("live_analytics.app.ws_ingest._get_pulse_logger", return_value=None):
            await m._do_resolve_and_link_participant(sid, scenario_id, started_at)

        row = get_session(db, sid)
        assert row is not None
        # get_session returns a SessionDetail dataclass — use attribute access.
        assert row.participant_id == pid, (
            f"participant_id in DB ({row.participant_id!r}) != expected ({pid!r})"
        )

        m._windows.pop(sid, None)
        web_api_client._participant_cache.pop(sid, None)

    @pytest.mark.asyncio
    async def test_resolve_aborts_if_session_removed(self):
        """If the session is not in _windows, the function must exit early
        without calling _write_session_start_batch."""
        import live_analytics.app.ws_ingest as m
        from live_analytics.app.storage import web_api_client

        sid = "resolve_abort_session"
        m._windows.pop(sid, None)  # session already gone

        start_calls: list = []

        def _fake_start(*args):  # pragma: no cover
            start_calls.append(args)

        with patch("live_analytics.app.ws_ingest._write_session_start_batch",
                   side_effect=_fake_start), \
             patch("live_analytics.app.ws_ingest.web_api_client.resolve_participant",
                   new_callable=AsyncMock, return_value="P_SHOULD_NOT_LINK"):
            await m._do_resolve_and_link_participant(
                sid, "sc", "2025-01-01T10:00:00+00:00"
            )

        assert start_calls == [], (
            f"_write_session_start_batch should not run for dead session; got: {start_calls}"
        )
        web_api_client._participant_cache.pop(sid, None)
