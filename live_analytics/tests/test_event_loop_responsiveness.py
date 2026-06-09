"""
Test: event-loop responsiveness under Unity telemetry load.

Verifies the fix from commit e4bd2f7:
  _ingest_session_batch()  — fast in-memory phase (event-loop thread)
  _write_db_batch()        — blocking I/O via run_in_executor (thread pool)

The core assertion is:
  While SQLite writes are running in the background, the asyncio event loop
  must remain free to handle other tasks (e.g. dashboard HTTP requests).

We simulate this by:
  1. Patching _write_db_batch to sleep for 200 ms (simulated heavy DB write).
  2. Running 10 consecutive _process_message calls concurrently with a
     lightweight "API heartbeat" coroutine that records its wake-up times.
  3. Asserting that the heartbeat is never delayed by more than 150 ms —
     proving the event loop was NOT blocked by the DB writes.

A secondary suite verifies correctness of the split itself:
  - _ingest_session_batch returns a properly populated _DbWritePayload.
  - _write_db_batch performs the expected SQLite writes.
  - In-memory state (_windows, _record_counts, latest_scores) is updated
    immediately (before the DB write completes).
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from live_analytics.app.models import TelemetryBatch, TelemetryRecord
from live_analytics.app.storage.sqlite_store import (
    close_pool,
    get_session,
    init_db,
    list_sessions,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_records(session_id: str, n: int = 10, t0_ms: int = 1_700_000_000_000) -> list[TelemetryRecord]:
    """Return *n* realistic TelemetryRecord objects for *session_id*."""
    return [
        TelemetryRecord(
            session_id=session_id,
            unix_ms=t0_ms + i * 50,
            unity_time=float(i) * 0.05,
            scenario_id="test_intersection",
            speed=5.0 + i * 0.1,
            heart_rate=75.0 + i * 0.5,
            steering_angle=float(i % 5) - 2.0,
            brake_front=100 if i == n - 1 else 0,
            record_type="gameplay",
        )
        for i in range(n)
    ]


def _batch_json(session_id: str, n: int = 10) -> str:
    """Return a JSON-serialised TelemetryBatch string as Unity would send it."""
    records = _make_records(session_id, n)
    batch = TelemetryBatch(records=records, count=len(records), sent_at="2026-06-09T10:00:00Z")
    return batch.model_dump_json()


def _reset_ingest_module_state(session_id: str) -> None:
    """Clear the module-level dicts in ws_ingest for a given session_id.

    Necessary because the module uses global dicts that persist across tests
    within the same process.  Only the keys for *session_id* are removed so
    other parallel tests are unaffected.
    """
    import live_analytics.app.ws_ingest as ingest
    for d in (
        ingest._windows,
        ingest._record_counts,
        ingest.latest_scores,
        ingest.latest_records,
        ingest.latest_gameplay_records,
        ingest.latest_hr,
    ):
        d.pop(session_id, None)


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture()
def db_path(tmp_path: Path):
    p = tmp_path / "ingest_test.db"
    init_db(p)
    yield p
    close_pool()


@pytest.fixture(autouse=True)
def _patch_db_path(db_path: Path):
    """Redirect the module-level DB_PATH in ws_ingest to a temp DB."""
    with patch("live_analytics.app.ws_ingest.DB_PATH", db_path):
        yield


@pytest.fixture(autouse=True)
def _patch_web_api():
    """Suppress all outbound HTTP calls from the ingest pipeline."""
    with (
        patch("live_analytics.app.ws_ingest.web_api_client.resolve_participant",
              new_callable=AsyncMock, return_value=None),
        patch("live_analytics.app.ws_ingest.web_api_client.send_pulse",
              new_callable=AsyncMock),
        patch("live_analytics.app.ws_ingest.web_api_client.get_cached_participant",
              return_value=None),
    ):
        yield


# ── Suite 1: Event-loop responsiveness ───────────────────────────────────────

class TestEventLoopResponsiveness:
    """Proves the event loop is not frozen while DB writes are in progress."""

    @pytest.mark.asyncio
    async def test_heartbeat_not_delayed_during_db_writes(self, db_path: Path):
        """Dashboard 'heartbeat' must not be delayed by simulated slow DB writes.

        Strategy
        --------
        1. Patch _write_db_batch to sleep 200 ms (simulates a slow SQLite write).
        2. Send 8 telemetry batches via _process_message.
        3. Concurrently run a heartbeat coroutine that wakes every 50 ms and
           records the actual gap between wakes.
        4. Assert max_gap < 150 ms — the event loop stayed responsive.

        Before the fix (blocking _ingest_session_batch) this test would fail
        because each 200 ms DB sleep would block the event loop entirely,
        causing heartbeat gaps of 200+ ms.
        """
        import live_analytics.app.ws_ingest as ingest

        sid = "loop_responsiveness_test"
        _reset_ingest_module_state(sid)

        SIMULATED_DB_LATENCY = 0.20   # 200 ms — intentionally slow
        HEARTBEAT_INTERVAL   = 0.05   # 50 ms
        HEARTBEAT_COUNT      = 20
        MAX_ALLOWED_GAP_S    = 0.15   # 150 ms — must stay well below DB latency

        wake_times: list[float] = []

        async def _heartbeat():
            """Lightweight task that just records its wake-up times."""
            for _ in range(HEARTBEAT_COUNT):
                wake_times.append(asyncio.get_event_loop().time())
                await asyncio.sleep(HEARTBEAT_INTERVAL)

        async def _send_batches():
            """Send 8 batches through the real _process_message."""
            ws_mock = MagicMock()
            ws_mock.send = AsyncMock()
            ws_mock.remote_address = ("127.0.0.1", 9999)
            for _ in range(8):
                msg = _batch_json(sid, n=10)
                await ingest._process_message(ws_mock, msg)

        # Patch _write_db_batch with a slow blocking-sleep version.
        # time.sleep blocks the *worker* thread (not the event loop) — this is
        # the correct simulation of a slow SQLite write in run_in_executor.
        import threading

        def _slow_write(payload):
            time.sleep(SIMULATED_DB_LATENCY)  # blocks worker thread only

        with patch.object(ingest, "_write_db_batch", side_effect=_slow_write):
            # Run both coroutines concurrently
            await asyncio.gather(
                _heartbeat(),
                _send_batches(),
            )

        # Analyse heartbeat gaps
        gaps = [wake_times[i + 1] - wake_times[i] for i in range(len(wake_times) - 1)]
        max_gap = max(gaps) if gaps else 0.0

        assert max_gap < MAX_ALLOWED_GAP_S, (
            f"Event loop was blocked: max heartbeat gap was {max_gap * 1000:.0f} ms "
            f"(threshold: {MAX_ALLOWED_GAP_S * 1000:.0f} ms). "
            f"This indicates DB writes are still running on the event-loop thread. "
            f"All gaps (ms): {[round(g * 1000) for g in gaps]}"
        )

        _reset_ingest_module_state(sid)

    @pytest.mark.asyncio
    async def test_in_memory_state_updated_before_db_write_completes(self, db_path: Path):
        """latest_scores and _windows must be updated before the DB write finishes.

        Confirms the split is correct: Phase 1 (in-memory) runs synchronously
        on the event-loop thread, Phase 2 (DB write) runs in the executor.
        We verify this by capturing state inside a barrier that fires as soon
        as the executor thread begins — at that point Phase 1 must already be done.
        """
        import threading
        import live_analytics.app.ws_ingest as ingest

        sid = "inmemory_update_test"
        _reset_ingest_module_state(sid)

        # Capture the running loop BEFORE entering the thread, so the
        # worker thread can use call_soon_threadsafe correctly.
        loop = asyncio.get_running_loop()

        # threading.Event lets us synchronise across the thread boundary
        # without using asyncio primitives from inside the worker thread.
        write_started_ev  = threading.Event()
        write_release_ev  = threading.Event()
        state_snapshot: dict = {}

        def _gating_write(payload):
            """Record in-memory state at the moment the executor thread starts."""
            state_snapshot["has_windows"]  = payload.sid in ingest._windows
            state_snapshot["has_counts"]   = payload.sid in ingest._record_counts
            state_snapshot["has_records"]  = payload.sid in ingest.latest_records
            write_started_ev.set()       # signal: Phase 1 is complete
            write_release_ev.wait(timeout=5.0)  # block until test releases us

        ws_mock = MagicMock()
        ws_mock.send = AsyncMock()
        ws_mock.remote_address = ("127.0.0.1", 9999)

        with patch.object(ingest, "_write_db_batch", side_effect=_gating_write):
            process_task = asyncio.create_task(
                ingest._process_message(ws_mock, _batch_json(sid, n=10))
            )

            # Yield to event loop so process_task can start, then wait for the
            # executor thread to signal it has started (Phase 1 complete).
            def _thread_wait():
                write_started_ev.wait(timeout=5.0)

            await loop.run_in_executor(None, _thread_wait)

            # Phase 1 must already have updated in-memory state
            assert state_snapshot.get("has_windows"),  "_windows not updated before DB write finished"
            assert state_snapshot.get("has_counts"),   "_record_counts not updated before DB write finished"
            assert state_snapshot.get("has_records"),  "latest_records not updated before DB write finished"

            # Release the gating write and finish
            write_release_ev.set()
            await process_task

        _reset_ingest_module_state(sid)


# ── Suite 2: _ingest_session_batch payload correctness ───────────────────────

class TestIngestSessionBatchPayload:
    """Verifies that _ingest_session_batch returns a correct _DbWritePayload."""

    def test_payload_fields_on_new_session(self, db_path: Path):
        """First call for a session must produce is_new=True and correct counts."""
        import live_analytics.app.ws_ingest as ingest

        sid = "payload_new_session"
        _reset_ingest_module_state(sid)
        records = _make_records(sid, n=10)

        with (
            patch.object(ingest, "_get_pulse_logger", return_value=None),
            patch("live_analytics.app.ws_ingest.web_api_client.get_cached_participant", return_value=None),
        ):
            payload = ingest._ingest_session_batch(sid, records)

        assert payload.is_new is True
        assert payload.sid == sid
        assert payload.n == 10
        assert payload.old_count == 0
        assert payload.new_count == 10
        assert len(payload.records) == 10
        # All gameplay records (record_type != "hr_only") should be in gameplay_to_write
        assert all(r.record_type != "hr_only" for r in payload.gameplay_to_write)

        _reset_ingest_module_state(sid)

    def test_payload_is_new_false_on_second_call(self, db_path: Path):
        """Second batch for the same session must have is_new=False."""
        import live_analytics.app.ws_ingest as ingest

        sid = "payload_existing_session"
        _reset_ingest_module_state(sid)
        records = _make_records(sid, n=10)

        with (
            patch.object(ingest, "_get_pulse_logger", return_value=None),
            patch("live_analytics.app.ws_ingest.web_api_client.get_cached_participant", return_value=None),
        ):
            ingest._ingest_session_batch(sid, records)
            payload2 = ingest._ingest_session_batch(sid, records)

        assert payload2.is_new is False
        assert payload2.old_count == 10
        assert payload2.new_count == 20

        _reset_ingest_module_state(sid)

    def test_hr_records_extracted_correctly(self, db_path: Path):
        """Records with heart_rate > 0 must be in payload.hr_records."""
        import live_analytics.app.ws_ingest as ingest

        sid = "payload_hr_records"
        _reset_ingest_module_state(sid)

        records = _make_records(sid, n=5)
        # Set HR on 3 records, leave 2 at 0
        records[0].heart_rate = 0.0
        records[1].heart_rate = 0.0
        records[2].heart_rate = 72.0
        records[3].heart_rate = 74.0
        records[4].heart_rate = 76.0

        with (
            patch.object(ingest, "_get_pulse_logger", return_value=None),
            patch("live_analytics.app.ws_ingest.web_api_client.get_cached_participant", return_value=None),
        ):
            payload = ingest._ingest_session_batch(sid, records)

        assert len(payload.hr_records) == 3
        assert all(r.heart_rate > 0 for r in payload.hr_records)

        _reset_ingest_module_state(sid)

    def test_scores_persist_flag_triggers_at_boundary(self, db_path: Path):
        """should_persist_scores must flip True when count crosses a _SCORE_PERSIST_EVERY boundary."""
        import live_analytics.app.ws_ingest as ingest

        sid = "payload_persist_flag"
        _reset_ingest_module_state(sid)

        # _SCORE_PERSIST_EVERY = 20; send 10 records → not yet at boundary
        records_10 = _make_records(sid, n=10)
        with (
            patch.object(ingest, "_get_pulse_logger", return_value=None),
            patch("live_analytics.app.ws_ingest.web_api_client.get_cached_participant", return_value=None),
        ):
            p1 = ingest._ingest_session_batch(sid, records_10)
        assert p1.should_persist_scores is False, "Should not persist at count=10"

        # Send another 10 → crosses boundary at 20
        with (
            patch.object(ingest, "_get_pulse_logger", return_value=None),
            patch("live_analytics.app.ws_ingest.web_api_client.get_cached_participant", return_value=None),
        ):
            p2 = ingest._ingest_session_batch(sid, records_10)
        assert p2.should_persist_scores is True, "Should persist when count crosses 20"

        _reset_ingest_module_state(sid)


# ── Suite 3: _write_db_batch correctness ─────────────────────────────────────

class TestWriteDbBatch:
    """Verifies _write_db_batch makes the expected SQLite writes."""

    @pytest.mark.asyncio
    async def test_session_upserted_in_db(self, db_path: Path):
        """After _write_db_batch runs, the session row must exist in SQLite."""
        import live_analytics.app.ws_ingest as ingest

        sid = "db_batch_upsert"
        _reset_ingest_module_state(sid)
        records = _make_records(sid, n=10)

        with (
            patch.object(ingest, "_get_pulse_logger", return_value=None),
            patch("live_analytics.app.ws_ingest.web_api_client.get_cached_participant", return_value=None),
        ):
            payload = ingest._ingest_session_batch(sid, records)

        # Run the DB write synchronously in the test thread (no executor needed)
        ingest._write_db_batch(payload)

        session = get_session(db_path, sid)
        assert session is not None, "Session row must be created by _write_db_batch"
        assert session.session_id == sid

        _reset_ingest_module_state(sid)
        close_pool()

    @pytest.mark.asyncio
    async def test_record_count_incremented_in_db(self, db_path: Path):
        """record_count in SQLite must equal the number of records sent."""
        import live_analytics.app.ws_ingest as ingest

        sid = "db_batch_count"
        _reset_ingest_module_state(sid)
        records = _make_records(sid, n=15)

        with (
            patch.object(ingest, "_get_pulse_logger", return_value=None),
            patch("live_analytics.app.ws_ingest.web_api_client.get_cached_participant", return_value=None),
        ):
            payload = ingest._ingest_session_batch(sid, records)
        ingest._write_db_batch(payload)

        session = get_session(db_path, sid)
        assert session is not None
        assert session.record_count == 15

        _reset_ingest_module_state(sid)
        close_pool()

    @pytest.mark.asyncio
    async def test_scores_persisted_at_boundary(self, db_path: Path):
        """update_latest_scores must be called when should_persist_scores is True."""
        import live_analytics.app.ws_ingest as ingest
        from live_analytics.app.models import ScoringResult

        sid = "db_batch_scores"
        _reset_ingest_module_state(sid)

        # Two batches of 10 → crosses _SCORE_PERSIST_EVERY=20 boundary on second
        records = _make_records(sid, n=10)
        with (
            patch.object(ingest, "_get_pulse_logger", return_value=None),
            patch("live_analytics.app.ws_ingest.web_api_client.get_cached_participant", return_value=None),
        ):
            p1 = ingest._ingest_session_batch(sid, records)
            p2 = ingest._ingest_session_batch(sid, records)

        ingest._write_db_batch(p1)
        ingest._write_db_batch(p2)

        assert p2.should_persist_scores is True
        # DB row must have been updated (latest_scores must be a non-empty ScoringResult)
        session = get_session(db_path, sid)
        assert session is not None
        assert session.latest_scores is not None, "latest_scores must be persisted to SQLite"

        _reset_ingest_module_state(sid)
        close_pool()

    def test_write_db_batch_is_not_a_coroutine(self):
        """_write_db_batch must be a plain function (not async) so run_in_executor works."""
        import inspect
        import live_analytics.app.ws_ingest as ingest
        assert not inspect.iscoroutinefunction(ingest._write_db_batch), (
            "_write_db_batch must be a regular (non-async) function — "
            "asyncio.run_in_executor requires a plain callable, not a coroutine."
        )
