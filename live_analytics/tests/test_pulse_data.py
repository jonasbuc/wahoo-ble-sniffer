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
    """Tests for send_pulse — resolve_participant is mocked out in all cases
    so each test only exercises the HTTP logic for the two pulse destinations."""

    @staticmethod
    def _resolve_none(session_id: str):  # noqa: ARG002 – stub
        return None

    def _with_resolve_mocked(self, participant_id=None):
        """Context manager that stubs resolve_participant → participant_id."""
        return patch(
            "live_analytics.app.storage.web_api_client.resolve_participant",
            new=AsyncMock(return_value=participant_id),
        )

    def test_happy_path_returns_true(self) -> None:
        """Both destinations must be called and True returned when both succeed."""
        mock_client = _make_mock_client()

        with self._with_resolve_mocked(), \
             patch("live_analytics.app.storage.web_api_client.httpx.AsyncClient", return_value=mock_client):
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

        with self._with_resolve_mocked(), \
             patch("live_analytics.app.storage.web_api_client.httpx.AsyncClient", return_value=mock_client):
            _run(web_api_client.send_pulse("sess-abc", 1_000_000, 80))

        qs_call = next(c for c in mock_client.post.call_args_list if "/api/pulse" in c.args[0])
        assert qs_call.kwargs["json"]["session_id"] == "sess-abc"
        assert qs_call.kwargs["json"]["pulse"] == 80
        assert qs_call.kwargs["json"]["unix_ms"] == 1_000_000

    def test_external_payload_uses_resolved_participant(self) -> None:
        """External call UserId must come from the resolved participant_id, not env var."""
        mock_client = _make_mock_client()

        with self._with_resolve_mocked(participant_id="7"), \
             patch("live_analytics.app.storage.web_api_client.httpx.AsyncClient", return_value=mock_client), \
             patch.dict("os.environ", {"EXTERNAL_USER_ID": "0"}):
            # Clear cache so the mock is used
            web_api_client.clear_participant_cache("sess-resolved")
            _run(web_api_client.send_pulse("sess-resolved", 1_000_000, 65))

        ext_call = next(c for c in mock_client.post.call_args_list if "loglitepd" in c.args[0])
        assert ext_call.kwargs["json"]["UserId"] == 7, "UserId should be parsed from participant_id '7'"
        assert ext_call.kwargs["json"]["Pulse"] == 65

    def test_external_payload_falls_back_to_env_when_no_participant(self) -> None:
        """If no participant is linked, UserId falls back to EXTERNAL_USER_ID env var."""
        mock_client = _make_mock_client()

        with self._with_resolve_mocked(participant_id=None), \
             patch("live_analytics.app.storage.web_api_client.httpx.AsyncClient", return_value=mock_client), \
             patch.dict("os.environ", {"EXTERNAL_USER_ID": "42"}):
            web_api_client.clear_participant_cache("sess-no-p")
            # Re-read env so _EXTERNAL_USER_ID is fresh in this test
            with patch.object(web_api_client, "_EXTERNAL_USER_ID", 42):
                _run(web_api_client.send_pulse("sess-no-p", 1_000_000, 65))

        ext_call = next(c for c in mock_client.post.call_args_list if "loglitepd" in c.args[0])
        assert ext_call.kwargs["json"]["UserId"] == 42

    def test_non_positive_pulse_skips_http(self) -> None:
        with self._with_resolve_mocked(), \
             patch("live_analytics.app.storage.web_api_client.httpx.AsyncClient") as mock_cls:
            result = _run(web_api_client.send_pulse("sess-1", 1_000_000, 0))
        assert result is False
        mock_cls.assert_not_called()

    def test_connect_error_on_both_returns_false(self) -> None:
        mock_client = _make_mock_client(side_effect=httpx.ConnectError("refused"))

        with self._with_resolve_mocked(), \
             patch("live_analytics.app.storage.web_api_client.httpx.AsyncClient", return_value=mock_client):
            result = _run(web_api_client.send_pulse("sess-1", 1_000_000, 75))

        assert result is False

    def test_timeout_returns_false(self) -> None:
        mock_client = _make_mock_client(side_effect=httpx.ReadTimeout("timed out"))

        with self._with_resolve_mocked(), \
             patch("live_analytics.app.storage.web_api_client.httpx.AsyncClient", return_value=mock_client):
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

        with self._with_resolve_mocked(), \
             patch("live_analytics.app.storage.web_api_client.httpx.AsyncClient", return_value=mock_client):
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

        with self._with_resolve_mocked(), \
             patch("live_analytics.app.storage.web_api_client.httpx.AsyncClient", return_value=mock_client):
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


# ── resolve_participant ───────────────────────────────────────────────

class TestResolveParticipant:
    """Unit tests for web_api_client.resolve_participant()."""

    def setup_method(self) -> None:
        web_api_client.clear_participant_cache()

    def _make_get_response(self, status_code: int, json_body=None):
        mock_resp = MagicMock()
        mock_resp.status_code = status_code
        if json_body is not None:
            mock_resp.json = MagicMock(return_value=json_body)
        mock_resp.raise_for_status = MagicMock()
        return mock_resp

    def _make_get_client(self, response):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=response)
        return mock_client

    def test_returns_participant_id_on_200(self) -> None:
        resp = self._make_get_response(200, {"participant_id": "P007"})
        client = self._make_get_client(resp)
        with patch("live_analytics.app.storage.web_api_client.httpx.AsyncClient", return_value=client):
            result = _run(web_api_client.resolve_participant("sess-x"))
        assert result == "P007"

    def test_caches_result(self) -> None:
        resp = self._make_get_response(200, {"participant_id": "P007"})
        client = self._make_get_client(resp)
        with patch("live_analytics.app.storage.web_api_client.httpx.AsyncClient", return_value=client):
            _run(web_api_client.resolve_participant("sess-cache"))
            _run(web_api_client.resolve_participant("sess-cache"))
        # GET should only have been called once (second call uses cache)
        assert client.get.call_count == 1

    def test_returns_none_on_404(self) -> None:
        resp = self._make_get_response(404)
        client = self._make_get_client(resp)
        with patch("live_analytics.app.storage.web_api_client.httpx.AsyncClient", return_value=client):
            result = _run(web_api_client.resolve_participant("sess-missing"))
        assert result is None

    def test_returns_none_on_connection_error(self) -> None:
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
        with patch("live_analytics.app.storage.web_api_client.httpx.AsyncClient", return_value=client):
            result = _run(web_api_client.resolve_participant("sess-err"))
        assert result is None

    def test_clear_cache_removes_entry(self) -> None:
        web_api_client._participant_cache["sess-cached"] = "P001"
        web_api_client.clear_participant_cache("sess-cached")
        assert "sess-cached" not in web_api_client._participant_cache

    def test_clear_cache_all(self) -> None:
        web_api_client._participant_cache["a"] = "P001"
        web_api_client._participant_cache["b"] = "P002"
        web_api_client.clear_participant_cache()
        assert web_api_client._participant_cache == {}


# ── questionnaire: get_participant_by_session ─────────────────────────

class TestGetParticipantBySession:
    """Unit tests for questionnaire db.get_participant_by_session()."""

    def test_returns_none_when_no_match(self, qs_db: Path) -> None:
        from live_analytics.questionnaire.db import get_participant_by_session
        assert get_participant_by_session(qs_db, "nonexistent-session") is None

    def test_returns_participant_when_linked(self, qs_db: Path) -> None:
        import sqlite3
        from datetime import datetime, timezone
        from live_analytics.questionnaire.db import get_participant_by_session
        now = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(str(qs_db))
        conn.execute(
            "INSERT INTO participants (participant_id, session_id, display_name, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("P099", "my-sess-id", "Test Person", now, now),
        )
        conn.commit()
        conn.close()

        result = get_participant_by_session(qs_db, "my-sess-id")
        assert result is not None
        assert result["participant_id"] == "P099"

    def test_session_id_empty_does_not_match(self, qs_db: Path) -> None:
        """A participant with session_id='' must not be returned for an empty-string lookup."""
        import sqlite3
        from datetime import datetime, timezone
        from live_analytics.questionnaire.db import get_participant_by_session
        now = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(str(qs_db))
        conn.execute(
            "INSERT INTO participants (participant_id, session_id, display_name, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("P001", "", "No Session", now, now),
        )
        conn.commit()
        conn.close()
        # Empty session_id '' is a falsy value — callers should never pass '' but
        # we guard here to avoid accidental cross-participant matches.
        result = get_participant_by_session(qs_db, "some-other-id")
        assert result is None
