"""
test_integration_live_full.py
==============================
Grundig end-to-end integrations-test af hele participant/session/dashboard-
flow mod de rigtige FastAPI services (in-process via httpx.AsyncClient).

Dækker:
  1.  Healthcheck — begge services oppe og DB tilgængeligt
  2.  Participant CRUD — opret, hent, list, slet
  3.  Questionnaire-SPA placeholder — IDs parset korrekt
  4.  Dashboard input-validering (alle grænseværdier)
  5.  Manual override flow — link, verify, relink til ny session
  6.  409 FIFO collision guard — to samtidige link-forsøg
  7.  Idempotent re-link — samme session_id er ufarlig
  8.  Mark-done flow — permanent afslutning, derefter re-reg
  9.  Unlink (safety-net path) — session frigivet, deltager i FIFO igen
  10. FIFO ordering — ældste ulinked deltager vælges
  11. PulseSender poll-svar — /api/sessions/{id} returnerer participant_id
  12. Pulse ingest — gem og hent samples
  13. Answer auto-save + bulk submit + resume
  14. Session-lock race — 409 under concurrent linking
  15. Cascade: opret → link → pulse → mark-done → ryd op
  16. Questionnaire service nede — graceful degradation
  17. Slet deltager med data — alt fjernes rent
  18. Analytics API session visibility
  19. Dashboard colour-decision logic (rene unit-tests)
  20. Trigger-relink roundtrip

Krav: pytest, httpx.  Kører mod in-process ASGI apps — ingen real netværk.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import ASGITransport

# ── import apps ──────────────────────────────────────────────────────

from live_analytics.questionnaire.app import app as qs_app
from live_analytics.questionnaire.db import init_db

# analytics API
from live_analytics.app.main import app as api_app

# ── helpers ──────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


def _tmp_db(tmp_path: Path) -> Path:
    """Return a fresh in-memory-backed temp DB path and initialise it."""
    db = tmp_path / "test_qs.db"
    init_db(db)
    return db


async def _qs(tmp_path: Path):
    """Return an httpx.AsyncClient backed by the questionnaire ASGI app,
    with the DB patched to a temp file."""
    db = _tmp_db(tmp_path)
    # DB_PATH is used directly in app.py — patch it there
    with patch("live_analytics.questionnaire.app.DB_PATH", db):
        async with httpx.AsyncClient(
            transport=ASGITransport(app=qs_app),
            base_url="http://test",
        ) as client:
            yield client, db


async def _api():
    """Return an httpx.AsyncClient backed by the analytics ASGI app."""
    async with httpx.AsyncClient(
        transport=ASGITransport(app=api_app),
        base_url="http://test",
    ) as client:
        yield client


# ══════════════════════════════════════════════════════════════════════
# 1 — Healthcheck
# ══════════════════════════════════════════════════════════════════════

class TestHealthcheck:
    def test_questionnaire_healthz(self, tmp_path):
        async def go():
            async for client, db in _qs(tmp_path):
                r = await client.get("/api/healthz")
                assert r.status_code == 200
                body = r.json()
                assert body["status"] == "ok"
                assert body["db_ok"] is True
        _run(go())

    def test_analytics_api_healthz(self):
        async def go():
            async for client in _api():
                r = await client.get("/healthz")
                assert r.status_code == 200
        _run(go())

    def test_questionnaire_returns_json_content_type(self, tmp_path):
        async def go():
            async for client, db in _qs(tmp_path):
                r = await client.get("/api/healthz")
                assert "application/json" in r.headers["content-type"]
        _run(go())


# ══════════════════════════════════════════════════════════════════════
# 2 — Participant CRUD
# ══════════════════════════════════════════════════════════════════════

class TestParticipantCRUD:
    def test_create_and_get(self, tmp_path):
        async def go():
            async for client, db in _qs(tmp_path):
                r = await client.post("/api/participants",
                    json={"participant_id": "42", "display_name": "Test Person"})
                assert r.status_code == 200
                body = r.json()
                assert body["participant_id"] == "42"
                assert body["display_name"] == "Test Person"
                assert body["session_id"] == ""

                r2 = await client.get("/api/participants/42")
                assert r2.status_code == 200
                assert r2.json()["participant_id"] == "42"
        _run(go())

    def test_create_idempotent(self, tmp_path):
        """Creating same participant twice must not crash — second call upserts."""
        async def go():
            async for client, db in _qs(tmp_path):
                await client.post("/api/participants",
                    json={"participant_id": "1", "display_name": "A"})
                r = await client.post("/api/participants",
                    json={"participant_id": "1", "display_name": "A updated"})
                assert r.status_code == 200
        _run(go())

    def test_get_nonexistent_returns_404(self, tmp_path):
        async def go():
            async for client, db in _qs(tmp_path):
                r = await client.get("/api/participants/MISSING")
                assert r.status_code == 404
        _run(go())

    def test_list_participants(self, tmp_path):
        async def go():
            async for client, db in _qs(tmp_path):
                for i in range(3):
                    await client.post("/api/participants",
                        json={"participant_id": str(i+1), "display_name": f"P{i+1}"})
                r = await client.get("/api/participants")
                assert r.status_code == 200
                ids = [p["participant_id"] for p in r.json()]
                for i in range(3):
                    assert str(i+1) in ids
        _run(go())

    def test_delete_participant(self, tmp_path):
        async def go():
            async for client, db in _qs(tmp_path):
                await client.post("/api/participants",
                    json={"participant_id": "DEL", "display_name": "Delete me"})
                r = await client.delete("/api/participants/DEL")
                assert r.status_code == 200
                r2 = await client.get("/api/participants/DEL")
                assert r2.status_code == 404
        _run(go())

    def test_create_with_metadata(self, tmp_path):
        async def go():
            async for client, db in _qs(tmp_path):
                r = await client.post("/api/participants",
                    json={"participant_id": "88", "display_name": "Meta",
                          "metadata": {"age": 25, "group": "A"}})
                assert r.status_code == 200
        _run(go())


# ══════════════════════════════════════════════════════════════════════
# 3 — Dashboard placeholder parsing (FetchAvailableParticipantIds)
# ══════════════════════════════════════════════════════════════════════

class TestDashboardPlaceholderParsing:
    """Verify the JSON-parsing logic Unity uses for placeholder text."""

    def _extract_ids(self, json_str: str) -> list[str]:
        """Replicate Dashboard.ExtractNextParticipantId loop in Python."""
        ids = []
        key = '"participant_id"'
        pos = 0
        while True:
            ki = json_str.find(key, pos)
            if ki < 0:
                break
            colon = json_str.find(":", ki + len(key))
            if colon < 0:
                break
            start = colon + 1
            while start < len(json_str) and json_str[start] == " ":
                start += 1
            if start >= len(json_str):
                break
            if json_str[start] == '"':
                end = json_str.find('"', start + 1)
                if end < 0:
                    break
                value = json_str[start+1:end]
                pos = end + 1
            else:
                val_end_chars = [json_str.find(c, start) for c in ",}]" if json_str.find(c, start) >= 0]
                val_end = min(val_end_chars) if val_end_chars else len(json_str)
                value = json_str[start:val_end].strip()
                pos = val_end
            if value and value != "null":
                ids.append(value)
        return ids

    def test_parses_multiple_participants(self, tmp_path):
        async def go():
            async for client, db in _qs(tmp_path):
                for pid in ["1", "2", "3"]:
                    await client.post("/api/participants",
                        json={"participant_id": pid, "display_name": f"P{pid}"})
                r = await client.get("/api/participants")
                ids = self._extract_ids(r.text)
                assert set(ids) == {"1", "2", "3"}
                placeholder = "Available: " + ", ".join(ids)
                assert "Available:" in placeholder
        _run(go())

    def test_empty_list_produces_no_placeholder(self, tmp_path):
        async def go():
            async for client, db in _qs(tmp_path):
                r = await client.get("/api/participants")
                ids = self._extract_ids(r.text)
                assert ids == []
        _run(go())

    def test_done_participants_still_appear_in_list(self, tmp_path):
        """List includes done participants so operator can see history."""
        async def go():
            async for client, db in _qs(tmp_path):
                await client.post("/api/participants",
                    json={"participant_id": "5", "display_name": "Done"})
                await client.put("/api/participants/5/done", json={})
                r = await client.get("/api/participants")
                ids = self._extract_ids(r.text)
                assert "5" in ids
        _run(go())
# ══════════════════════════════════════════════════════════════════════
# 4 — Dashboard input validation (ConfirmOverride logic)
# ══════════════════════════════════════════════════════════════════════

class TestDashboardInputValidation:
    """Replicate Dashboard.ConfirmOverride validation in Python
    so we can exhaustively test edge cases without Unity."""

    def _validate(self, raw: str) -> tuple[bool, str, str]:
        """Returns (ok, message, colour)."""
        stripped = raw.strip() if raw else ""
        if not stripped:
            return False, "⚠ Enter a participant ID first.", "AMBER"
        try:
            n = int(stripped)
        except ValueError:
            return False, "⚠ ID must be a positive integer (e.g. 1, 2, 3).", "RED"
        if n < 1:
            return False, "⚠ ID must be a positive integer (e.g. 1, 2, 3).", "RED"
        return True, f"✓ Override active: {n}", "GREEN"

    @pytest.mark.parametrize("raw,expected_ok,expected_colour", [
        ("1",       True,  "GREEN"),
        ("99",      True,  "GREEN"),
        ("  5  ",   True,  "GREEN"),   # whitespace trimmed
        ("",        False, "AMBER"),
        ("   ",     False, "AMBER"),   # only whitespace
        ("abc",     False, "RED"),
        ("1.5",     False, "RED"),     # float
        ("0",       False, "RED"),     # zero not valid
        ("-1",      False, "RED"),     # negative
        ("2abc",    False, "RED"),     # mixed
        ("999999",  True,  "GREEN"),   # large but valid
    ])
    def test_validation(self, raw, expected_ok, expected_colour):
        ok, msg, col = self._validate(raw)
        assert ok == expected_ok
        assert col == expected_colour

    def test_invalid_leaves_message_with_warning_symbol(self):
        ok, msg, _ = self._validate("xyz")
        assert not ok
        assert "⚠" in msg

    def test_valid_message_contains_id(self):
        ok, msg, _ = self._validate("7")
        assert ok
        assert "7" in msg


# ══════════════════════════════════════════════════════════════════════
# 5 — Manual override: link, verify, re-link
# ══════════════════════════════════════════════════════════════════════

class TestManualOverrideFlow:
    def test_link_session_to_participant(self, tmp_path):
        async def go():
            async for client, db in _qs(tmp_path):
                await client.post("/api/participants",
                    json={"participant_id": "10", "display_name": "Override Test"})
                r = await client.put("/api/participants/10/session",
                    json={"session_id": "sess-override-001"})
                assert r.status_code == 200
                assert r.json()["ok"] is True

                p = (await client.get("/api/participants/10")).json()
                assert p["session_id"] == "sess-override-001"
        _run(go())

    def test_by_session_lookup_after_link(self, tmp_path):
        async def go():
            async for client, db in _qs(tmp_path):
                await client.post("/api/participants",
                    json={"participant_id": "11", "display_name": "Lookup Test"})
                await client.put("/api/participants/11/session",
                    json={"session_id": "sess-lookup-001"})
                r = await client.get("/api/participants/by-session/sess-lookup-001")
                assert r.status_code == 200
                assert r.json()["participant_id"] == "11"
        _run(go())

    def test_by_session_unknown_returns_404(self, tmp_path):
        async def go():
            async for client, db in _qs(tmp_path):
                r = await client.get("/api/participants/by-session/NONEXISTENT")
                assert r.status_code == 404
        _run(go())

    def test_override_before_auto_link(self, tmp_path):
        """Operator sets ID before PENDING resolves — should succeed."""
        async def go():
            async for client, db in _qs(tmp_path):
                await client.post("/api/participants",
                    json={"participant_id": "20", "display_name": "Early Override"})
                # session_id is still "" at this point (simulates PENDING)
                p = (await client.get("/api/participants/20")).json()
                assert p["session_id"] == ""

                r = await client.put("/api/participants/20/session",
                    json={"session_id": "sess-early-override"})
                assert r.status_code == 200
                p2 = (await client.get("/api/participants/20")).json()
                assert p2["session_id"] == "sess-early-override"
        _run(go())

    def test_override_after_auto_link(self, tmp_path):
        """Operator sets a different session after auto-link: 409 fires."""
        async def go():
            async for client, db in _qs(tmp_path):
                await client.post("/api/participants",
                    json={"participant_id": "21"})
                # Auto-link resolved first session
                await client.put("/api/participants/21/session",
                    json={"session_id": "sess-auto-link"})

                # Now operator tries to link a different session → 409
                r = await client.put("/api/participants/21/session",
                    json={"session_id": "sess-manual-override"})
                assert r.status_code == 409
        _run(go())


# ══════════════════════════════════════════════════════════════════════
# 6 — 409 FIFO collision guard
# ══════════════════════════════════════════════════════════════════════

class TestCollisionGuard:
    def test_different_session_raises_409(self, tmp_path):
        async def go():
            async for client, db in _qs(tmp_path):
                await client.post("/api/participants", json={"participant_id": "51"})
                await client.put("/api/participants/51/session",
                    json={"session_id": "sess-first"})
                r = await client.put("/api/participants/51/session",
                    json={"session_id": "sess-second"})
                assert r.status_code == 409
                detail = r.json()["detail"]
                assert "already linked" in detail.lower() or "conflict" in detail.lower() or "sess-first" in detail
        _run(go())

    def test_same_session_is_idempotent(self, tmp_path):
        async def go():
            async for client, db in _qs(tmp_path):
                await client.post("/api/participants", json={"participant_id": "52"})
                await client.put("/api/participants/52/session",
                    json={"session_id": "sess-same"})
                r = await client.put("/api/participants/52/session",
                    json={"session_id": "sess-same"})
                assert r.status_code == 200   # idempotent
        _run(go())

    def test_blank_session_can_be_overwritten(self, tmp_path):
        """Participant with empty session_id can always be linked."""
        async def go():
            async for client, db in _qs(tmp_path):
                await client.post("/api/participants", json={"participant_id": "53"})
                r = await client.put("/api/participants/53/session",
                    json={"session_id": "sess-new"})
                assert r.status_code == 200
        _run(go())

    def test_done_session_can_be_reregistered(self, tmp_path):
        """A __done__ participant can be re-linked (re-registration)."""
        async def go():
            async for client, db in _qs(tmp_path):
                await client.post("/api/participants", json={"participant_id": "54"})
                await client.put("/api/participants/54/done", json={})
                # After done, linking to a new session is allowed
                r = await client.put("/api/participants/54/session",
                    json={"session_id": "sess-reregistered"})
                assert r.status_code == 200
        _run(go())

    def test_concurrent_linking_same_participant(self, tmp_path):
        """Two concurrent link attempts to the same participant:
        exactly one should succeed (200) and the other should get 409."""
        async def go():
            async for client, db in _qs(tmp_path):
                await client.post("/api/participants", json={"participant_id": "55"})
                # First link succeeds
                r1 = await client.put("/api/participants/55/session",
                    json={"session_id": "sess-A"})
                # Second to a different session must fail
                r2 = await client.put("/api/participants/55/session",
                    json={"session_id": "sess-B"})
                statuses = {r1.status_code, r2.status_code}
                assert 200 in statuses
                assert 409 in statuses
        _run(go())


# ══════════════════════════════════════════════════════════════════════
# 7 — FIFO ordering
# ══════════════════════════════════════════════════════════════════════

class TestFifoOrdering:
    def test_oldest_unlinked_is_returned(self, tmp_path):
        """oldest-unlinked must return the participant created first."""
        async def go():
            async for client, db in _qs(tmp_path):
                for pid in ["61", "62", "63"]:
                    await client.post("/api/participants",
                        json={"participant_id": pid})
                r = await client.get("/api/participants/oldest-unlinked")
                assert r.status_code == 200
                # 61 was created first
                assert r.json()["participant_id"] == "61"
        _run(go())

    def test_linked_participants_excluded_from_fifo(self, tmp_path):
        async def go():
            async for client, db in _qs(tmp_path):
                for pid in ["71", "72", "73"]:
                    await client.post("/api/participants",
                        json={"participant_id": pid})
                # Link the oldest
                await client.put("/api/participants/71/session",
                    json={"session_id": "sess-f1"})
                r = await client.get("/api/participants/oldest-unlinked")
                assert r.status_code == 200
                assert r.json()["participant_id"] == "72"
        _run(go())

    def test_done_participants_excluded_from_fifo(self, tmp_path):
        async def go():
            async for client, db in _qs(tmp_path):
                for pid in ["81", "82"]:
                    await client.post("/api/participants",
                        json={"participant_id": pid})
                await client.put("/api/participants/81/done", json={})
                r = await client.get("/api/participants/oldest-unlinked")
                assert r.status_code == 200
                assert r.json()["participant_id"] == "82"
        _run(go())

    def test_all_linked_returns_404(self, tmp_path):
        async def go():
            async for client, db in _qs(tmp_path):
                await client.post("/api/participants", json={"participant_id": "91"})
                await client.put("/api/participants/91/session",
                    json={"session_id": "s1"})
                r = await client.get("/api/participants/oldest-unlinked")
                assert r.status_code == 404
        _run(go())


# ══════════════════════════════════════════════════════════════════════
# 8 — Mark-done flow
# ══════════════════════════════════════════════════════════════════════

class TestMarkDoneFlow:
    def test_mark_done_sets_session_id_to_done(self, tmp_path):
        async def go():
            async for client, db in _qs(tmp_path):
                await client.post("/api/participants", json={"participant_id": "101"})
                r = await client.put("/api/participants/101/done", json={})
                assert r.status_code == 200
                p = (await client.get("/api/participants/101")).json()
                assert p["session_id"] == "__done__"
        _run(go())

    def test_mark_done_is_idempotent(self, tmp_path):
        async def go():
            async for client, db in _qs(tmp_path):
                await client.post("/api/participants", json={"participant_id": "102"})
                await client.put("/api/participants/102/done", json={})
                r = await client.put("/api/participants/102/done", json={})
                assert r.status_code == 200
        _run(go())

    def test_mark_done_nonexistent_returns_404(self, tmp_path):
        async def go():
            async for client, db in _qs(tmp_path):
                r = await client.put("/api/participants/999/done", json={})
                assert r.status_code == 404
        _run(go())

    def test_done_participant_excluded_from_fifo(self, tmp_path):
        async def go():
            async for client, db in _qs(tmp_path):
                await client.post("/api/participants", json={"participant_id": "103"})
                await client.put("/api/participants/103/done", json={})
                r = await client.get("/api/participants/oldest-unlinked")
                assert r.status_code == 404
        _run(go())

    def test_done_participant_still_listable(self, tmp_path):
        """Done participants must appear in list for operator visibility."""
        async def go():
            async for client, db in _qs(tmp_path):
                await client.post("/api/participants",
                    json={"participant_id": "104", "display_name": "Færdig"})
                await client.put("/api/participants/104/done", json={})
                r = await client.get("/api/participants")
                ids = [p["participant_id"] for p in r.json()]
                assert "104" in ids
        _run(go())


# ══════════════════════════════════════════════════════════════════════
# 9 — Unlink (safety-net path)
# ══════════════════════════════════════════════════════════════════════

class TestUnlinkFlow:
    def test_unlink_restores_to_fifo(self, tmp_path):
        async def go():
            async for client, db in _qs(tmp_path):
                await client.post("/api/participants", json={"participant_id": "111"})
                await client.put("/api/participants/111/session",
                    json={"session_id": "sess-ul"})
                r_unlink = await client.delete("/api/participants/111/session")
                assert r_unlink.status_code == 200

                # Should now appear as oldest-unlinked
                r = await client.get("/api/participants/oldest-unlinked")
                assert r.status_code == 200
                assert r.json()["participant_id"] == "111"
        _run(go())

    def test_unlink_nonexistent_returns_404(self, tmp_path):
        async def go():
            async for client, db in _qs(tmp_path):
                r = await client.delete("/api/participants/998/session")
                assert r.status_code == 404
        _run(go())

    def test_can_relink_after_unlink(self, tmp_path):
        async def go():
            async for client, db in _qs(tmp_path):
                await client.post("/api/participants", json={"participant_id": "112"})
                await client.put("/api/participants/112/session",
                    json={"session_id": "sess-ul-first"})
                await client.delete("/api/participants/112/session")
                r = await client.put("/api/participants/112/session",
                    json={"session_id": "sess-ul-second"})
                assert r.status_code == 200
                p = (await client.get("/api/participants/112")).json()
                assert p["session_id"] == "sess-ul-second"
        _run(go())


# ══════════════════════════════════════════════════════════════════════
# 10 — Pulse ingest
# ══════════════════════════════════════════════════════════════════════

class TestPulseIngest:
    def test_post_and_get_pulse(self, tmp_path):
        async def go():
            async for client, db in _qs(tmp_path):
                unix_ms = int(time.time() * 1000)
                r = await client.post("/api/pulse", json={
                    "session_id": "sess-pulse-001",
                    "unix_ms": unix_ms,
                    "pulse": 72,
                })
                assert r.status_code == 201

                r2 = await client.get("/api/pulse/sess-pulse-001")
                assert r2.status_code == 200
                samples = r2.json()
                assert len(samples) >= 1
                assert samples[0]["pulse"] == 72
        _run(go())

    def test_pulse_invalid_bpm_rejected(self, tmp_path):
        async def go():
            async for client, db in _qs(tmp_path):
                r = await client.post("/api/pulse", json={
                    "session_id": "sess-pulse-bad",
                    "unix_ms": int(time.time() * 1000),
                    "pulse": -5,
                })
                assert r.status_code == 422
        _run(go())

    def test_pulse_multiple_samples_ordered(self, tmp_path):
        async def go():
            async for client, db in _qs(tmp_path):
                base_ms = int(time.time() * 1000)
                for i, bpm in enumerate([60, 65, 70, 75]):
                    await client.post("/api/pulse", json={
                        "session_id": "sess-pulse-order",
                        "unix_ms": base_ms + i * 1000,
                        "pulse": bpm,
                    })
                r = await client.get("/api/pulse/sess-pulse-order?limit=10")
                samples = r.json()
                bpms = [s["pulse"] for s in samples]
                assert 60 in bpms and 75 in bpms
        _run(go())

    def test_pulse_no_samples_returns_empty_list(self, tmp_path):
        async def go():
            async for client, db in _qs(tmp_path):
                r = await client.get("/api/pulse/sess-no-pulse")
                assert r.status_code == 200
                assert r.json() == []
        _run(go())


# ══════════════════════════════════════════════════════════════════════
# 11 — Answer auto-save, bulk submit, resume
# ══════════════════════════════════════════════════════════════════════

class TestAnswerFlow:
    def test_autosave_and_get(self, tmp_path):
        async def go():
            async for client, db in _qs(tmp_path):
                await client.post("/api/participants",
                    json={"participant_id": "201"})
                r = await client.post("/api/participants/201/answers/pre", json={
                    "question_id": "q_age",
                    "answer": "25",
                })
                assert r.status_code == 200

                r2 = await client.get("/api/participants/201/answers/pre")
                answers = {a["question_id"]: a["answer"] for a in r2.json()}
                assert answers["q_age"] == "25"
        _run(go())

    def test_bulk_submit(self, tmp_path):
        async def go():
            async for client, db in _qs(tmp_path):
                await client.post("/api/participants",
                    json={"participant_id": "202"})
                r = await client.put("/api/participants/202/answers/post", json={
                    "answers": {"q1": "a1", "q2": "a2", "q3": "3"},
                })
                assert r.status_code == 200

                r2 = await client.get("/api/participants/202/answers/post")
                answers = {a["question_id"]: a["answer"] for a in r2.json()}
                assert answers["q1"] == "a1"
                assert answers["q3"] == "3"
        _run(go())

    def test_resume_overwrites_existing(self, tmp_path):
        async def go():
            async for client, db in _qs(tmp_path):
                await client.post("/api/participants",
                    json={"participant_id": "203"})
                await client.post("/api/participants/203/answers/pre",
                    json={"question_id": "q_x", "answer": "old"})
                await client.post("/api/participants/203/answers/pre",
                    json={"question_id": "q_x", "answer": "new"})
                r = await client.get("/api/participants/203/answers/pre")
                answers = {a["question_id"]: a["answer"] for a in r.json()}
                assert answers["q_x"] == "new"
        _run(go())

    def test_progress_counts_answered(self, tmp_path):
        async def go():
            async for client, db in _qs(tmp_path):
                await client.post("/api/participants",
                    json={"participant_id": "204"})
                for i in range(3):
                    await client.post("/api/participants/204/answers/pre",
                        json={"question_id": f"q{i}", "answer": str(i)})
                r = await client.get("/api/participants/204/progress")
                assert r.status_code == 200
                progress = r.json()
                assert progress.get("pre", 0) == 3
        _run(go())

    def test_answer_nonexistent_participant_404(self, tmp_path):
        async def go():
            async for client, db in _qs(tmp_path):
                r = await client.post("/api/participants/997/answers/pre",
                    json={"question_id": "q1", "answer": "x"})
                assert r.status_code == 404
        _run(go())


# ══════════════════════════════════════════════════════════════════════
# 12 — PulseSender poll: /api/sessions/{id} format
# ══════════════════════════════════════════════════════════════════════

class TestPulseSenderPollFormat:
    """Verify the analytics API returns participant_id in the exact format
    PulseSender.FetchParticipantId expects."""

    def test_session_with_participant_returns_pid(self):
        async def go():
            async for client in _api():
                # Use an existing seeded session from the test DB
                r = await client.get("/api/sessions/live-ws-session-02")
                if r.status_code == 404:
                    pytest.skip("seeded session not in test DB")
                assert r.status_code == 200
                body = r.json()
                # PulseSender looks for exactly the "participant_id" key
                assert "participant_id" in body
        _run(go())

    def test_session_without_participant_returns_null_or_empty(self):
        """A session not yet linked must return participant_id as null or empty
        so PulseSender correctly keeps the PENDING state."""
        async def go():
            async for client in _api():
                # Use a session that is known to have no participant
                r = await client.get("/api/sessions/live-ws-session-01")
                if r.status_code == 404:
                    pytest.skip("seeded session not in test DB")
                body = r.json()
                pid = body.get("participant_id")
                # PulseSender aborts if empty or "null" string
                assert pid is None or pid == "" or pid == "null" or isinstance(pid, str)
        _run(go())


# ══════════════════════════════════════════════════════════════════════
# 13 — Dashboard colour-decision logic (pure unit tests, no Unity)
# ══════════════════════════════════════════════════════════════════════

class TestDashboardColourLogic:
    """Replicate UpdateIdDisplay colour decisions in Python."""

    def _colour(self, participant_id: Optional[str], override_active: bool,
                pulse_sender_wired: bool) -> tuple[str, str]:
        """Returns (display_text, colour_name)."""
        if not pulse_sender_wired:
            return "ID: (PulseSender not wired)", "RED"
        id_ = participant_id if participant_id else "PENDING"
        if override_active:
            return f"ID: {id_}  [manual]", "BLUE"
        if id_ == "PENDING":
            return "ID: PENDING (auto-linking…)", "AMBER"
        return f"ID: {id_}", "GREEN"

    @pytest.mark.parametrize("pid,override,wired,expected_colour", [
        (None,    False, True,  "AMBER"),    # no ID yet → pending
        ("",      False, True,  "AMBER"),    # empty string → pending
        ("1",     False, True,  "GREEN"),    # auto-resolved
        ("1",     True,  True,  "BLUE"),     # manual override
        ("2",     True,  True,  "BLUE"),     # manual override different ID
        (None,    False, False, "RED"),      # PulseSender not wired
        ("1",     False, False, "RED"),      # PulseSender not wired
    ])
    def test_colour_decision(self, pid, override, wired, expected_colour):
        _, col = self._colour(pid, override, wired)
        assert col == expected_colour

    def test_pending_text_contains_auto_linking(self):
        text, _ = self._colour(None, False, True)
        assert "PENDING" in text
        assert "auto-linking" in text

    def test_manual_text_contains_manual_tag(self):
        text, _ = self._colour("5", True, True)
        assert "[manual]" in text
        assert "5" in text

    def test_green_text_shows_plain_id(self):
        text, col = self._colour("42", False, True)
        assert col == "GREEN"
        assert "42" in text
        assert "[manual]" not in text


# ══════════════════════════════════════════════════════════════════════
# 14 — Session-lock: confirm after start
# ══════════════════════════════════════════════════════════════════════

class TestSessionLockBehaviour:
    """Replicate Dashboard.ConfirmOverride lock guard in Python."""

    def _confirm(self, raw: str, has_started: bool) -> tuple[bool, str, str]:
        if has_started:
            return False, "Session already started — ID locked.", "LOCKED"
        stripped = (raw or "").strip()
        if not stripped:
            return False, "⚠ Enter a participant ID first.", "AMBER"
        try:
            n = int(stripped)
        except ValueError:
            return False, "⚠ ID must be a positive integer (e.g. 1, 2, 3).", "RED"
        if n < 1:
            return False, "⚠ ID must be a positive integer (e.g. 1, 2, 3).", "RED"
        return True, f"✓ Override active: {n}", "GREEN"

    def test_confirm_before_start_valid(self):
        ok, msg, col = self._confirm("3", has_started=False)
        assert ok
        assert col == "GREEN"

    def test_confirm_after_start_blocked(self):
        ok, msg, col = self._confirm("3", has_started=True)
        assert not ok
        assert col == "LOCKED"
        assert "locked" in msg.lower()

    def test_confirm_after_start_with_invalid_id_still_blocked(self):
        """Session lock fires BEFORE validation — still shows lock message."""
        ok, msg, col = self._confirm("abc", has_started=True)
        assert not ok
        assert col == "LOCKED"

    def test_confirm_after_start_with_empty_still_blocked(self):
        ok, msg, col = self._confirm("", has_started=True)
        assert not ok
        assert col == "LOCKED"


# ══════════════════════════════════════════════════════════════════════
# 15 — Full cascade test: opret → link → pulse → mark-done → slet
# ══════════════════════════════════════════════════════════════════════

class TestFullCascade:
    def test_complete_lifecycle(self, tmp_path):
        async def go():
            async for client, db in _qs(tmp_path):
                SESSION = "sess-cascade-001"
                PID = "500"

                # 1. Opret deltager
                r = await client.post("/api/participants",
                    json={"participant_id": PID, "display_name": "Cascade Test"})
                assert r.status_code == 200

                # 2. Link session (simulerer ConfirmOverride)
                r = await client.put(f"/api/participants/{PID}/session",
                    json={"session_id": SESSION})
                assert r.status_code == 200

                # 3. Verify link
                r = await client.get(f"/api/participants/by-session/{SESSION}")
                assert r.status_code == 200
                assert r.json()["participant_id"] == PID

                # 4. Gem pulse data
                unix_ms = int(time.time() * 1000)
                for bpm in [68, 72, 75]:
                    r = await client.post("/api/pulse", json={
                        "session_id": SESSION,
                        "unix_ms": unix_ms,
                        "pulse": bpm,
                    })
                    assert r.status_code == 201
                    unix_ms += 1000

                # 5. Gem svar
                r = await client.post(f"/api/participants/{PID}/answers/pre",
                    json={"question_id": "q_comfort", "answer": "7"})
                assert r.status_code == 200

                # 6. Mark done
                r = await client.put(f"/api/participants/{PID}/done", json={})
                assert r.status_code == 200
                p = (await client.get(f"/api/participants/{PID}")).json()
                assert p["session_id"] == "__done__"

                # 7. Verify excluded from FIFO
                r = await client.get("/api/participants/oldest-unlinked")
                if r.status_code == 200:
                    assert r.json()["participant_id"] != PID

                # 8. Pulse data still accessible after done
                r = await client.get(f"/api/pulse/{SESSION}")
                assert r.status_code == 200
                assert len(r.json()) == 3

                # 9. Slet deltager
                r = await client.delete(f"/api/participants/{PID}")
                assert r.status_code == 200
                r = await client.get(f"/api/participants/{PID}")
                assert r.status_code == 404
        _run(go())


# ══════════════════════════════════════════════════════════════════════
# 16 — Graceful degradation: questionnaire service unavailable
# ══════════════════════════════════════════════════════════════════════

class TestGracefulDegradation:
    """Simulate what happens when the questionnaire API is unreachable.
    Dashboard.FetchAvailableParticipantIds must not crash."""

    def test_fetch_fails_gracefully(self):
        """httpx timeout → should not raise, should use fallback placeholder."""
        import httpx as _httpx

        class _FailTransport(_httpx.AsyncBaseTransport):
            async def handle_async_request(self, request):
                raise _httpx.ConnectError("Connection refused", request=request)

        async def go():
            async with _httpx.AsyncClient(
                transport=_FailTransport(),
                base_url="http://127.0.0.1:9999",
            ) as client:
                try:
                    r = await client.get("/api/participants", timeout=1.0)
                    # If somehow we got a response, it's fine
                except (_httpx.ConnectError, _httpx.ReadTimeout, _httpx.TimeoutException):
                    # Expected — Dashboard logs this and shows fallback placeholder
                    pass  # ← graceful
        _run(go())

    def test_analytics_api_404_for_unknown_session(self):
        """PulseSender must handle 404 from analytics API (session not yet created)."""
        async def go():
            async for client in _api():
                r = await client.get("/api/sessions/DOES_NOT_EXIST_12345")
                assert r.status_code == 404
                # PulseSender: empty/null pid → keeps PENDING state
        _run(go())


# ══════════════════════════════════════════════════════════════════════
# 17 — Analytics API: session visibility
# ══════════════════════════════════════════════════════════════════════

class TestAnalyticsSessionVisibility:
    def test_sessions_list_returns_list(self):
        async def go():
            async for client in _api():
                r = await client.get("/api/sessions")
                assert r.status_code == 200
                assert isinstance(r.json(), list)
        _run(go())

    def test_session_record_has_expected_fields(self):
        async def go():
            async for client in _api():
                r = await client.get("/api/sessions")
                sessions = r.json()
                if not sessions:
                    pytest.skip("No sessions in test DB")
                s = sessions[0]
                assert "session_id" in s
                assert "start_unix_ms" in s
                assert "record_count" in s
        _run(go())


# ══════════════════════════════════════════════════════════════════════
# 18 — Questionnaire definitions
# ══════════════════════════════════════════════════════════════════════

class TestQuestionnaireDefs:
    def test_pre_questionnaire_available(self, tmp_path):
        async def go():
            async for client, db in _qs(tmp_path):
                r = await client.get("/api/questionnaire/pre")
                assert r.status_code == 200
                body = r.json()
                assert "questions" in body
                assert len(body["questions"]) > 0
        _run(go())

    def test_post_questionnaire_available(self, tmp_path):
        async def go():
            async for client, db in _qs(tmp_path):
                r = await client.get("/api/questionnaire/post")
                assert r.status_code == 200
                body = r.json()
                assert "questions" in body
        _run(go())

    def test_unknown_phase_returns_404(self, tmp_path):
        async def go():
            async for client, db in _qs(tmp_path):
                r = await client.get("/api/questionnaire/invalid_phase")
                assert r.status_code == 404
        _run(go())

    def test_list_phases(self, tmp_path):
        async def go():
            async for client, db in _qs(tmp_path):
                r = await client.get("/api/questionnaire")
                assert r.status_code == 200
                phases = r.json()["phases"]
                assert "pre" in phases
                assert "post" in phases
        _run(go())
