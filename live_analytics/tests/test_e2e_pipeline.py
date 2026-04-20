"""
End-to-end integration test: WS ingest → scoring → storage → API → dashboard read.

Verifies the full pipeline without running actual servers by calling the
internal functions directly in the correct order.
"""

from __future__ import annotations

import json
import time
from collections import deque
from pathlib import Path

import pytest

from live_analytics.app.models import ScoringResult, TelemetryBatch, TelemetryRecord
from live_analytics.app.scoring.rules import compute_scores
from live_analytics.app.storage.raw_writer import RawWriter
from live_analytics.app.storage.sqlite_store import (
    close_pool,
    get_session,
    increment_record_count,
    init_db,
    list_sessions,
    update_latest_scores,
    upsert_session,
)


@pytest.fixture()
def db_path(tmp_path: Path):
    p = tmp_path / "e2e.db"
    init_db(p)
    yield p
    close_pool()


@pytest.fixture()
def sessions_dir(tmp_path: Path):
    d = tmp_path / "sessions"
    d.mkdir()
    return d


def _make_batch(session_id: str, n: int = 20, t0: float = 0.0) -> TelemetryBatch:
    """Create a realistic batch of telemetry records."""
    now_ms = int(time.time() * 1000)
    records = []
    for i in range(n):
        records.append(TelemetryRecord(
            session_id=session_id,
            unix_ms=now_ms + i * 50,
            unity_time=t0 + i * 0.05,
            scenario_id="test_scenario",
            speed=5.0 + i * 0.1,
            heart_rate=72.0 + i * 0.5,
            steering_angle=float(i % 5) - 2.0,
            brake_front=100 if i == n - 1 else 0,
        ))
    return TelemetryBatch(
        records=records,
        count=len(records),
        sent_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )


class TestEndToEndPipeline:
    """Full pipeline: batch → ingest → score → store → API read."""

    def test_single_session_lifecycle(self, db_path: Path, sessions_dir: Path):
        sid = "e2e_test_session"
        batch = _make_batch(sid, n=40)
        writer = RawWriter(sessions_dir)

        # Step 1: Upsert session (what _ingest_record does on first record)
        upsert_session(db_path, sid, batch.records[0].unix_ms, "test_scenario")

        # Step 2: Write raw JSONL + accumulate window + score
        window: deque[TelemetryRecord] = deque(maxlen=600)
        for rec in batch.records:
            writer.append(rec)
            window.append(rec)
            increment_record_count(db_path, sid, 1)

        # Step 3: Score the window
        scores = compute_scores(list(window))
        assert isinstance(scores, ScoringResult)
        assert 0 <= scores.stress_score <= 100
        assert 0 <= scores.risk_score <= 100

        # Step 4: Persist scores
        update_latest_scores(db_path, sid, scores)

        # Step 5: Verify via API-equivalent DB reads
        sessions = list_sessions(db_path)
        assert len(sessions) == 1
        assert sessions[0].session_id == sid
        assert sessions[0].record_count == 40

        detail = get_session(db_path, sid)
        assert detail is not None
        assert detail.latest_scores is not None
        assert detail.latest_scores.stress_score == scores.stress_score
        assert detail.latest_scores.risk_score == scores.risk_score

        # Step 6: Verify JSONL file written correctly
        jsonl_path = sessions_dir / sid / "telemetry.jsonl"
        assert jsonl_path.exists()
        lines = jsonl_path.read_text().strip().split("\n")
        assert len(lines) == 40
        first = json.loads(lines[0])
        assert first["session_id"] == sid
        assert "speed" in first
        assert "heart_rate" in first

    def test_multiple_sessions_concurrent(self, db_path: Path, sessions_dir: Path):
        """Simulate two sessions ingesting interleaved."""
        writer = RawWriter(sessions_dir)
        windows: dict[str, deque[TelemetryRecord]] = {}

        for sid in ("session_A", "session_B"):
            batch = _make_batch(sid, n=20)
            upsert_session(db_path, sid, batch.records[0].unix_ms, "multi_test")
            windows[sid] = deque(maxlen=600)
            for rec in batch.records:
                writer.append(rec)
                windows[sid].append(rec)
                increment_record_count(db_path, sid, 1)
            scores = compute_scores(list(windows[sid]))
            update_latest_scores(db_path, sid, scores)

        sessions = list_sessions(db_path)
        assert len(sessions) == 2
        ids = {s.session_id for s in sessions}
        assert ids == {"session_A", "session_B"}

        for sid in ("session_A", "session_B"):
            d = get_session(db_path, sid)
            assert d is not None
            assert d.record_count == 20
            assert d.latest_scores is not None

    def test_empty_batch_does_not_crash(self, db_path: Path, sessions_dir: Path):
        """An empty batch should produce an empty scoring result."""
        scores = compute_scores([])
        assert scores.stress_score == 0.0
        assert scores.risk_score == 0.0

    def test_jsonl_readable_by_dashboard_helper(self, db_path: Path, sessions_dir: Path):
        """Verify the JSONL produced by the pipeline is parseable
        by the dashboard's _read_last_jsonl_rows equivalent."""
        sid = "read_test"
        batch = _make_batch(sid, n=10)
        writer = RawWriter(sessions_dir)
        for rec in batch.records:
            writer.append(rec)

        jsonl_path = sessions_dir / sid / "telemetry.jsonl"
        # Simulate dashboard read
        from collections import deque as dq

        with jsonl_path.open("r") as f:
            last = dq(f, maxlen=50)
        rows = [json.loads(line) for line in last if line.strip()]
        assert len(rows) == 10
        assert all("unity_time" in r for r in rows)
        assert all("speed" in r for r in rows)

    def test_scoring_values_change_with_data(self, db_path: Path, sessions_dir: Path):
        """Scores should differ between calm and erratic data."""
        calm = [
            TelemetryRecord(
                session_id="calm", unix_ms=1000 + i * 50, unity_time=i * 0.05,
                speed=5.0, heart_rate=70.0, steering_angle=0.0,
            )
            for i in range(100)
        ]
        erratic = [
            TelemetryRecord(
                session_id="erratic", unix_ms=1000 + i * 50, unity_time=i * 0.05,
                speed=15.0 + i * 0.5, heart_rate=70.0 + i * 1.5,
                steering_angle=float((-1) ** i * i * 10),
            )
            for i in range(100)
        ]
        s_calm = compute_scores(calm)
        s_erratic = compute_scores(erratic)
        # Erratic should produce higher stress and risk
        assert s_erratic.stress_score > s_calm.stress_score
        assert s_erratic.risk_score > s_calm.risk_score
