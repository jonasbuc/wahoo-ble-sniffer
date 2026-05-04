"""
Tests for the pulse-data pipeline under the new architecture:

  ws_ingest  →  web_api_client.send_pulse()  →  questionnaire API  →  questionnaire.db

Coverage
--------
* questionnaire.db: insert_pulse_data / get_pulse_data
* web_api_client.send_pulse – happy path, ConnectError, TimeoutException,
  HTTPStatusError, non-positive pulse
* ws_ingest._ingest_session_batch – fires send_pulse, skips zero HR
* sqlite_store: pulse_data table is GONE (regression guard)
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

# ── questionnaire DB layer ─────────────────────────────────────────────

from live_analytics.questionnaire.db import (
    get_pulse_data as qs_get_pulse,
    init_db as qs_init_db,
    insert_pulse_data as qs_insert_pulse,
)


@pytest.fixture()
def qs_db(tmp_path: Path) -> Path:
    p = tmp_path / "questionnaire.db"
    qs_init_db(p)
    return p


class TestQuestionnairePulseDb:
    def test_happy_path_stores_row(self, qs_db: Path) -> None:
        row = qs_insert_pulse(qs_db, "sess-1", unix_ms=1_000_100, pulse=72)
        assert row["pulse"] == 72
        assert row["session_id"] == "sess-1"
        assert row["unix_ms"] == 1_000_100

    def test_participant_id_none_when_no_link(self, qs_db: Path) -> None:
        row = qs_insert_pulse(qs_db, "sess-no-link", unix_ms=1_000_100, pulse=60)
        assert row["participant_id"] is None

    def test_participant_id_resolved_from_session(self, qs_db: Path) -> None:
        import sqlite3
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(str(qs_db))
        conn.execute(
            "INSERT INTO participants (participant_id, session_id, display_name, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            ("p-abc", "linked-sess", "Alice", now, now),
        )
        conn.commit()
        conn.close()

        row = qs_insert_pulse(qs_db, "linked-sess", unix_ms=2_000_000, pulse=88)
        assert row["participant_id"] == "p-abc"

    def test_non_positive_pulse_rejected(self, qs_db: Path) -> None:
        with pytest.raises(ValueError):
            qs_insert_pulse(qs_db, "sess-1", unix_ms=1_000_100, pulse=0)

    def test_negative_pulse_rejected(self, qs_db: Path) -> None:
        with pytest.raises(ValueError):
            qs_insert_pulse(qs_db, "sess-1", unix_ms=1_000_100, pulse=-5)

    def test_multiple_samples_ordered_newest_first(self, qs_db: Path) -> None:
        for bpm, ts in [(70, 1_000_100), (75, 1_000_200), (80, 1_000_300)]:
            qs_insert_pulse(qs_db, "sess-1", unix_ms=ts, pulse=bpm)
        rows = qs_get_pulse(qs_db, "sess-1")
        assert [r["pulse"] for r in rows] == [80, 75, 70]

    def test_limit_respected(self, qs_db: Path) -> None:
        for i in range(10):
            qs_insert_pulse(qs_db, "sess-1", unix_ms=1_000_000 + i, pulse=60 + i)
        rows = qs_get_pulse(qs_db, "sess-1", limit=3)
        assert len(rows) == 3

    def test_separate_sessions_do_not_mix(self, qs_db: Path) -> None:
        qs_insert_pulse(qs_db, "sess-A", unix_ms=1_000_100, pulse=65)
        qs_insert_pulse(qs_db, "sess-B", unix_ms=2_000_100, pulse=90)
        assert qs_get_pulse(qs_db, "sess-A")[0]["pulse"] == 65
        assert qs_get_pulse(qs_db, "sess-B")[0]["pulse"] == 90


# ── web_api_client.send_pulse ─────────────────────────────────────────

from live_analytics.app.storage import web_api_client  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


def _make_mock_client(side_effect=None):
    """Return an AsyncMock httpx.AsyncClient where .post succeeds by default."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    if side_effect is not None:
        mock_client.post = AsyncMock(side_effect=side_effect)
    else:
        mock_client.post = AsyncMock(return_value=mock_resp)
    return mock_client


class TestSendPulse:
    def test_happy_path_returns_true(self) -> None:
        """Both destinations must be called and True returned when both succeed."""
        mock_client = _make_mock_client()

        with patch("live_analytics.app.storage.web_api_client.httpx.AsyncClient", return_value=mock_client):
            result = _run(web_api_client.send_pulse("sess-1", 1_000_000, 75))

        assert result is True
        # Two calls: one to questionnaire API, one to external research API.
        assert mock_client.post.call_count == 2
        urls_called = [c.args[0] for c in mock_client.post.call_args_list]
        assert any("/api/pulse" in u for u in urls_called), "questionnaire endpoint not called"
        assert any("loglitepd" in u for u in urls_called), "external endpoint not called"

    def test_questionnaire_payload(self) -> None:
        """Questionnaire call must include session_id, unix_ms, pulse."""
        mock_client = _make_mock_client()

        with patch("live_analytics.app.storage.web_api_client.httpx.AsyncClient", return_value=mock_client):
            _run(web_api_client.send_pulse("sess-abc", 1_000_000, 80))

        qs_call = next(c for c in mock_client.post.call_args_list if "/api/pulse" in c.args[0])
        assert qs_call.kwargs["json"]["session_id"] == "sess-abc"
        assert qs_call.kwargs["json"]["pulse"] == 80
        assert qs_call.kwargs["json"]["unix_ms"] == 1_000_000

    def test_external_payload(self) -> None:
        """External call must include UserId and Pulse (capital keys per DB schema)."""
        mock_client = _make_mock_client()

        with patch("live_analytics.app.storage.web_api_client.httpx.AsyncClient", return_value=mock_client), \
             patch.dict("os.environ", {"EXTERNAL_USER_ID": "42"}):
            _run(web_api_client.send_pulse("sess-1", 1_000_000, 65))

        ext_call = next(c for c in mock_client.post.call_args_list if "loglitepd" in c.args[0])
        assert ext_call.kwargs["json"]["UserId"] == 42
        assert ext_call.kwargs["json"]["Pulse"] == 65

    def test_non_positive_pulse_skips_http(self) -> None:
        with patch("live_analytics.app.storage.web_api_client.httpx.AsyncClient") as mock_cls:
            result = _run(web_api_client.send_pulse("sess-1", 1_000_000, 0))
        assert result is False
        mock_cls.assert_not_called()

    def test_connect_error_on_both_returns_false(self) -> None:
        mock_client = _make_mock_client(side_effect=httpx.ConnectError("refused"))

        with patch("live_analytics.app.storage.web_api_client.httpx.AsyncClient", return_value=mock_client):
            result = _run(web_api_client.send_pulse("sess-1", 1_000_000, 75))

        assert result is False

    def test_timeout_returns_false(self) -> None:
        mock_client = _make_mock_client(side_effect=httpx.ReadTimeout("timed out"))

        with patch("live_analytics.app.storage.web_api_client.httpx.AsyncClient", return_value=mock_client):
            result = _run(web_api_client.send_pulse("sess-1", 1_000_000, 75))

        assert result is False

    def test_http_status_error_returns_false(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"

        def _raise_for_status():
            raise httpx.HTTPStatusError("500", request=MagicMock(), response=mock_resp)

        mock_resp.raise_for_status = _raise_for_status
        mock_client = _make_mock_client()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("live_analytics.app.storage.web_api_client.httpx.AsyncClient", return_value=mock_client):
            result = _run(web_api_client.send_pulse("sess-1", 1_000_000, 75))

        assert result is False

    def test_partial_failure_returns_false(self) -> None:
        """If only one destination fails, result must be False (but no exception raised)."""
        ok_resp = MagicMock()
        ok_resp.raise_for_status = MagicMock()
        fail_resp = MagicMock()
        fail_resp.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError("500", request=MagicMock(), response=MagicMock(status_code=500, text="err"))
        )

        call_count = 0

        async def _alternating_post(url, **kwargs):
            nonlocal call_count
            call_count += 1
            # First call (questionnaire) succeeds, second (external) fails.
            return ok_resp if call_count == 1 else fail_resp

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=_alternating_post)

        with patch("live_analytics.app.storage.web_api_client.httpx.AsyncClient", return_value=mock_client):
            result = _run(web_api_client.send_pulse("sess-1", 1_000_000, 75))

        assert result is False  # partial failure → False
        assert mock_client.post.call_count == 2  # both were still attempted



# ── ws_ingest integration ─────────────────────────────────────────────

async def test_ingest_batch_fires_send_pulse(tmp_path: Path) -> None:
    """_ingest_session_batch() must schedule send_pulse for HR > 0."""
    import live_analytics.app.ws_ingest as ingest
    from live_analytics.app.models import TelemetryRecord
    from live_analytics.app.storage.sqlite_store import init_db, close_pool

    db = tmp_path / "ingest_test.db"
    init_db(db)

    fired: list[tuple] = []

    async def _fake_send(session_id, unix_ms, pulse):
        fired.append((session_id, unix_ms, pulse))
        return True

    with patch.object(ingest, "DB_PATH", db), \
         patch.object(ingest.web_api_client, "send_pulse", side_effect=_fake_send):
        ingest._windows.clear()
        ingest._record_counts.clear()
        ingest.latest_scores.clear()
        ingest.latest_records.clear()

        records = [
            TelemetryRecord(
                session_id="test-sess",
                unix_ms=1_000_000 + i * 50,
                unity_time=float(i),
                scenario_id="test",
                speed=10.0,
                heart_rate=75.0,
            )
            for i in range(5)
        ]
        ingest._ingest_session_batch("test-sess", records)
        # Yield control so that the ensure_future task can run.
        await asyncio.sleep(0)

    assert len(fired) == 1, "Expected exactly one send_pulse call per batch"
    assert fired[0][0] == "test-sess"
    assert fired[0][2] == 75
    close_pool()


async def test_ingest_batch_skips_zero_hr(tmp_path: Path) -> None:
    """No send_pulse call when all records have heart_rate == 0."""
    import live_analytics.app.ws_ingest as ingest
    from live_analytics.app.models import TelemetryRecord
    from live_analytics.app.storage.sqlite_store import init_db, close_pool

    db = tmp_path / "ingest_zero_hr.db"
    init_db(db)

    fired: list = []

    async def _fake_send(session_id, unix_ms, pulse):
        fired.append(pulse)
        return True

    with patch.object(ingest, "DB_PATH", db), \
         patch.object(ingest.web_api_client, "send_pulse", side_effect=_fake_send):
        ingest._windows.clear()
        ingest._record_counts.clear()
        ingest.latest_scores.clear()
        ingest.latest_records.clear()

        records = [
            TelemetryRecord(
                session_id="zero-sess",
                unix_ms=1_000_000 + i * 50,
                unity_time=float(i),
                scenario_id="test",
                speed=5.0,
                heart_rate=0.0,
            )
            for i in range(3)
        ]
        ingest._ingest_session_batch("zero-sess", records)
        await asyncio.sleep(0)

    assert fired == [], "send_pulse must not be called when HR is 0"
    close_pool()


# ── Regression: pulse_data table must NOT exist in sqlite_store ───────

def test_sqlite_store_has_no_pulse_data_table(tmp_path: Path) -> None:
    """sqlite_store must NOT create a pulse_data table — it belongs in questionnaire.db."""
    import sqlite3
    from live_analytics.app.storage.sqlite_store import init_db, close_pool

    db = tmp_path / "regression.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='pulse_data'"
    ).fetchone()
    conn.close()
    close_pool()
    assert row is None, (
        "pulse_data table must NOT exist in sqlite_store (live_analytics.db). "
        "Pulse data belongs in questionnaire.db via the Web API."
    )


def test_sqlite_store_has_no_insert_pulse_data() -> None:
    """insert_pulse_data must not be importable from sqlite_store."""
    import importlib
    store = importlib.import_module("live_analytics.app.storage.sqlite_store")
    assert not hasattr(store, "insert_pulse_data"), (
        "insert_pulse_data must be removed from sqlite_store"
    )
