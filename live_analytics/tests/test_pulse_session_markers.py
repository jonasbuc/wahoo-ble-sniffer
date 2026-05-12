"""
Live mock-test: pulse.jsonl SESSION_START / SESSION_END markers + alle HR-samples.

Simulates the full pipeline for TWO participants in sequence:
  1. TP_001 oprettes i questionnaire → Unity session starter → puls-data
     strømmer ind → Unity stopper → SESSION_END skrives
  2. TP_002 oprettes → ny session → puls-data → session slutter

Verifierer:
  - pulse.jsonl for TP_001 starter med SESSION_START og slutter med SESSION_END
  - pulse.jsonl for TP_002 er separat og indeholder kun TP_002's data
  - Ingen puls-linjer fra TP_001 optræder i TP_002's log og omvendt
  - SESSION_END skrives med korrekt record_count
  - Strukturen i marker-linjer er korrekt JSON med alle forventede felter
  - ALLE HR-samples i en batch skrives til pulse.jsonl (ikke kun den sidste)
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from live_analytics.app.models import TelemetryBatch, TelemetryRecord
from live_analytics.app.storage.participant_logs import (
    append_pulse,
    append_pulse_session_marker,
    create_participant_log_dir,
)
from live_analytics.app.storage.sqlite_store import close_pool, init_db
import live_analytics.app.ws_ingest as ingest
import live_analytics.app.storage.web_api_client as web_api_client


# ── Helpers ────────────────────────────────────────────────────────────

def _make_records(session_id: str, n: int = 10, hr_base: float = 70.0) -> list[TelemetryRecord]:
    now_ms = int(time.time() * 1000)
    return [
        TelemetryRecord(
            session_id=session_id,
            unix_ms=now_ms + i * 50,
            unity_time=float(i) * 0.05,
            scenario_id="mock_scenario",
            speed=5.0 + i * 0.1,
            heart_rate=hr_base + i * 0.5,
            steering_angle=0.0,
            brake_front=0,
            brake_rear=0,
            record_type="gameplay",
        )
        for i in range(n)
    ]


def _read_jsonl(path: Path) -> list[dict]:
    """Read a JSONL file, skipping comment lines (starting with #)."""
    lines = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw or raw.startswith("#"):
            continue
        lines.append(json.loads(raw))
    return lines


# ── Fixtures ───────────────────────────────────────────────────────────

@pytest.fixture()
def pdir(tmp_path: Path) -> Path:
    d = tmp_path / "participants"
    d.mkdir()
    return d


@pytest.fixture()
def db(tmp_path: Path):
    p = tmp_path / "test.db"
    init_db(p)
    yield p
    close_pool()


# ── Tests ──────────────────────────────────────────────────────────────

class TestPulseSessionMarkers:
    """Unit-level: verify append_pulse_session_marker writes correct JSON lines."""

    def test_session_start_marker_fields(self, pdir: Path) -> None:
        create_participant_log_dir(pdir, "TP_001")
        append_pulse_session_marker(
            pdir, "TP_001",
            marker="SESSION_START",
            session_id="111000",
            timestamp="2026-05-12T10:00:00+00:00",
            local_time="2026-05-12 10:00:00 UTC",
            extra={"scenario_id": "mock_scenario"},
        )
        lines = _read_jsonl(pdir / "TP_001" / "pulse.jsonl")
        assert len(lines) == 1
        m = lines[0]
        assert m["marker"] == "SESSION_START"
        assert m["session_id"] == "111000"
        assert m["participant_id"] == "TP_001"
        assert m["scenario_id"] == "mock_scenario"
        assert "timestamp" in m

    def test_session_end_marker_fields(self, pdir: Path) -> None:
        create_participant_log_dir(pdir, "TP_001")
        append_pulse_session_marker(
            pdir, "TP_001",
            marker="SESSION_END",
            session_id="111000",
            timestamp="2026-05-12T10:30:00+00:00",
            local_time="2026-05-12 10:30:00 UTC",
            extra={"record_count": 600},
        )
        lines = _read_jsonl(pdir / "TP_001" / "pulse.jsonl")
        assert lines[0]["marker"] == "SESSION_END"
        assert lines[0]["record_count"] == 600

    def test_pulse_data_sits_between_markers(self, pdir: Path) -> None:
        create_participant_log_dir(pdir, "TP_001")
        # SESSION_START
        append_pulse_session_marker(pdir, "TP_001", "SESSION_START", "111000",
                                    "2026-05-12T10:00:00+00:00")
        # Puls-data
        append_pulse(pdir, "TP_001", {"pulse": 72, "session_id": "111000", "unix_ms": 1000})
        append_pulse(pdir, "TP_001", {"pulse": 75, "session_id": "111000", "unix_ms": 1050})
        # SESSION_END
        append_pulse_session_marker(pdir, "TP_001", "SESSION_END", "111000",
                                    "2026-05-12T10:30:00+00:00", extra={"record_count": 2})
        lines = _read_jsonl(pdir / "TP_001" / "pulse.jsonl")
        assert lines[0]["marker"] == "SESSION_START"
        assert lines[1]["pulse"] == 72
        assert lines[2]["pulse"] == 75
        assert lines[3]["marker"] == "SESSION_END"

    def test_two_participants_have_separate_logs(self, pdir: Path) -> None:
        for pid, hr, sid in [("TP_001", 70, "111000"), ("TP_002", 90, "222000")]:
            create_participant_log_dir(pdir, pid)
            append_pulse_session_marker(pdir, pid, "SESSION_START", sid, "2026-05-12T10:00:00+00:00")
            append_pulse(pdir, pid, {"pulse": hr, "session_id": sid, "unix_ms": 1000})
            append_pulse_session_marker(pdir, pid, "SESSION_END", sid, "2026-05-12T10:30:00+00:00",
                                        extra={"record_count": 1})

        lines_01 = _read_jsonl(pdir / "TP_001" / "pulse.jsonl")
        lines_02 = _read_jsonl(pdir / "TP_002" / "pulse.jsonl")

        # TP_001 har ingen TP_002 session_ids og vice versa
        for line in lines_01:
            assert line.get("session_id") != "222000", "TP_001 log indeholder TP_002 data!"
        for line in lines_02:
            assert line.get("session_id") != "111000", "TP_002 log indeholder TP_001 data!"

        assert lines_01[1]["pulse"] == 70
        assert lines_02[1]["pulse"] == 90


class TestIngestPipelineWithMockParticipant:
    """
    Integration-niveau: kører _ingest_session_batch + _resolve_and_link_participant
    med en mock questionnaire API for at verificere marker-skriving i den rigtige pipeline.
    """

    def _setup_ingest(self, db_path: Path, pdir: Path) -> None:
        """Nulstil ws_ingest module-state og sæt korrekte stier."""
        ingest._windows.clear()
        ingest._record_counts.clear()
        ingest.latest_scores.clear()
        ingest.latest_records.clear()
        ingest.latest_gameplay_records.clear()
        ingest.latest_hr.clear()
        ingest.set_raw_writer(None)
        web_api_client._participant_cache.clear()
        web_api_client._resolve_cooldown_until.clear()

        # Patch config paths til tmp-stier
        ingest.DB_PATH = db_path
        ingest.PARTICIPANTS_DIR = pdir

    @pytest.mark.asyncio
    async def test_session_start_marker_written_on_resolve(
        self, db: Path, pdir: Path
    ) -> None:
        """SESSION_START skrives i pulse.jsonl når participant resolves."""
        sid = "mock_session_001"
        pid = "TP_001"
        create_participant_log_dir(pdir, pid)

        self._setup_ingest(db, pdir)

        # Mock questionnaire API: returnerer TP_001 med det samme
        with patch.object(web_api_client, "resolve_participant", new=AsyncMock(return_value=pid)):
            with patch("live_analytics.app.storage.sqlite_store.set_session_participant"):
                records = _make_records(sid, n=5, hr_base=72.0)
                started_at = "2026-05-12T10:00:00+00:00"
                await ingest._resolve_and_link_participant(sid, "mock_scenario", started_at)

        lines = _read_jsonl(pdir / pid / "pulse.jsonl")
        markers = [l for l in lines if "marker" in l]
        assert len(markers) == 1, f"Forventede 1 marker, fik {markers}"
        assert markers[0]["marker"] == "SESSION_START"
        assert markers[0]["session_id"] == sid
        assert markers[0]["participant_id"] == pid
        assert markers[0]["scenario_id"] == "mock_scenario"

    @pytest.mark.asyncio
    async def test_session_end_marker_written_on_disconnect(
        self, db: Path, pdir: Path
    ) -> None:
        """SESSION_END skrives i pulse.jsonl når Unity disconnecter."""
        sid = "mock_session_002"
        pid = "TP_001"
        create_participant_log_dir(pdir, pid)

        self._setup_ingest(db, pdir)

        # Simuler at session allerede kører (records er set)
        from collections import deque
        ingest._windows[sid] = deque(maxlen=600)
        ingest._record_counts[sid] = 42
        # Sæt en fake latest_record så _on_disconnect ikke springer over
        rec = _make_records(sid, n=1)[0]
        ingest.latest_records[sid] = rec
        # Cache participant direkte
        web_api_client._participant_cache[sid] = pid

        with patch("live_analytics.app.storage.sqlite_store.end_session"):
            await ingest._on_disconnect({sid})

        lines = _read_jsonl(pdir / pid / "pulse.jsonl")
        markers = [l for l in lines if "marker" in l]
        assert len(markers) == 1, f"Forventede 1 SESSION_END marker, fik {markers}"
        assert markers[0]["marker"] == "SESSION_END"
        assert markers[0]["session_id"] == sid
        assert markers[0]["record_count"] == 42

    @pytest.mark.asyncio
    async def test_two_participants_full_lifecycle(
        self, db: Path, pdir: Path
    ) -> None:
        """TP_001 og TP_002 kører i sekvens – hvert log indeholder kun egne data."""
        for pid in ("TP_001", "TP_002"):
            create_participant_log_dir(pdir, pid)

        self._setup_ingest(db, pdir)

        participants = [
            ("mock_session_tp001", "TP_001", 65.0),
            ("mock_session_tp002", "TP_002", 88.0),
        ]

        for sid, pid, hr in participants:
            # --- Session start ---
            with patch.object(web_api_client, "resolve_participant", new=AsyncMock(return_value=pid)):
                with patch("live_analytics.app.storage.sqlite_store.set_session_participant"):
                    await ingest._resolve_and_link_participant(sid, "mock_scenario", "2026-05-12T10:00:00+00:00")

            # --- Simuler puls-data ---
            append_pulse(pdir, pid, {"pulse": int(hr), "session_id": sid, "unix_ms": 9999})

            # --- Session slut ---
            from collections import deque
            ingest._windows[sid] = deque(maxlen=600)
            ingest._record_counts[sid] = 100
            rec = _make_records(sid, n=1)[0]
            ingest.latest_records[sid] = rec
            web_api_client._participant_cache[sid] = pid

            with patch("live_analytics.app.storage.sqlite_store.end_session"):
                await ingest._on_disconnect({sid})

        # --- Verificer TP_001 ---
        lines_01 = _read_jsonl(pdir / "TP_001" / "pulse.jsonl")
        assert lines_01[0]["marker"] == "SESSION_START"
        assert lines_01[1]["pulse"] == 65
        assert lines_01[2]["marker"] == "SESSION_END"
        # Ingen TP_002 session_id i TP_001's log
        for line in lines_01:
            assert line.get("session_id") != "mock_session_tp002"

        # --- Verificer TP_002 ---
        lines_02 = _read_jsonl(pdir / "TP_002" / "pulse.jsonl")
        assert lines_02[0]["marker"] == "SESSION_START"
        assert lines_02[1]["pulse"] == 88
        assert lines_02[2]["marker"] == "SESSION_END"
        # Ingen TP_001 session_id i TP_002's log
        for line in lines_02:
            assert line.get("session_id") != "mock_session_tp001"


class TestAllHrSamplesWrittenToLog:
    """
    Verificerer at ALLE HR-samples i en batch skrives til pulse.jsonl –
    ikke kun den sidste.
    """

    def _setup_ingest(self, db_path: Path, pdir: Path) -> None:
        ingest._windows.clear()
        ingest._record_counts.clear()
        ingest.latest_scores.clear()
        ingest.latest_records.clear()
        ingest.latest_gameplay_records.clear()
        ingest.latest_hr.clear()
        ingest.set_raw_writer(None)
        web_api_client._participant_cache.clear()
        web_api_client._resolve_cooldown_until.clear()
        ingest.DB_PATH = db_path
        ingest.PARTICIPANTS_DIR = pdir

    def test_all_hr_records_in_batch_written(self, db, pdir: Path) -> None:
        """En batch med 5 HR-records skal give 5 linjer i pulse.jsonl (ikke 1)."""
        sid = "hr_all_test_session"
        pid = "TP_HR"
        create_participant_log_dir(pdir, pid)

        self._setup_ingest(db, pdir)

        # Sæt participant direkte i cache (simulerer at resolve allerede er sket)
        web_api_client._participant_cache[sid] = pid

        # Lav en batch med 5 records, alle med forskellig HR
        import time as _time
        now_ms = int(_time.time() * 1000)
        records = [
            TelemetryRecord(
                session_id=sid,
                unix_ms=now_ms + i * 50,
                unity_time=float(i) * 0.05,
                speed=5.0,
                heart_rate=70.0 + i,   # 70, 71, 72, 73, 74
                record_type="gameplay",
            )
            for i in range(5)
        ]

        with patch("live_analytics.app.storage.sqlite_store.upsert_session"), \
             patch("live_analytics.app.storage.sqlite_store.insert_records"), \
             patch("live_analytics.app.storage.sqlite_store.increment_record_count"), \
             patch("live_analytics.app.storage.sqlite_store.update_latest_scores"), \
             patch("live_analytics.app.storage.web_api_client.send_pulse", new=AsyncMock()), \
             patch("asyncio.get_running_loop", side_effect=RuntimeError("no loop")):
            ingest._ingest_session_batch(sid, records)

        lines = _read_jsonl(pdir / pid / "pulse.jsonl")
        pulse_lines = [l for l in lines if "pulse" in l and "marker" not in l]

        assert len(pulse_lines) == 5, (
            f"Forventede 5 puls-linjer (én per record), fik {len(pulse_lines)}: {pulse_lines}"
        )
        pulses = [l["pulse"] for l in pulse_lines]
        assert pulses == [70, 71, 72, 73, 74], f"Forkerte puls-værdier: {pulses}"

    def test_records_with_zero_hr_are_skipped(self, db, pdir: Path) -> None:
        """Records med heart_rate=0 (headpose/relay) skal IKKE skrives til pulse.jsonl."""
        sid = "hr_zero_test_session"
        pid = "TP_HR2"
        create_participant_log_dir(pdir, pid)

        self._setup_ingest(db, pdir)
        web_api_client._participant_cache[sid] = pid

        import time as _time
        now_ms = int(_time.time() * 1000)
        records = [
            TelemetryRecord(session_id=sid, unix_ms=now_ms, unity_time=0.0,
                            speed=0.0, heart_rate=0.0, record_type="headpose"),
            TelemetryRecord(session_id=sid, unix_ms=now_ms + 50, unity_time=0.05,
                            speed=5.0, heart_rate=75.0, record_type="gameplay"),
            TelemetryRecord(session_id=sid, unix_ms=now_ms + 100, unity_time=0.1,
                            speed=0.0, heart_rate=0.0, record_type="headpose"),
        ]

        with patch("live_analytics.app.storage.sqlite_store.upsert_session"), \
             patch("live_analytics.app.storage.sqlite_store.insert_records"), \
             patch("live_analytics.app.storage.sqlite_store.increment_record_count"), \
             patch("live_analytics.app.storage.sqlite_store.update_latest_scores"), \
             patch("live_analytics.app.storage.web_api_client.send_pulse", new=AsyncMock()), \
             patch("asyncio.get_running_loop", side_effect=RuntimeError("no loop")):
            ingest._ingest_session_batch(sid, records)

        lines = _read_jsonl(pdir / pid / "pulse.jsonl")
        pulse_lines = [l for l in lines if "pulse" in l and "marker" not in l]

        assert len(pulse_lines) == 1, f"Kun 1 gyldig HR-record — fik {len(pulse_lines)}"
        assert pulse_lines[0]["pulse"] == 75
