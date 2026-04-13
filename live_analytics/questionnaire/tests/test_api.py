"""Tests for the questionnaire API endpoints."""
import os
import tempfile

import pytest

# Patch DB path before importing anything
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["QS_DB_PATH"] = _tmp.name

from fastapi.testclient import TestClient  # noqa: E402
from live_analytics.questionnaire.app import app  # noqa: E402
from live_analytics.questionnaire import db  # noqa: E402
from live_analytics.questionnaire.config import DB_PATH  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_db():
    """Re-initialise the database for every test."""
    import sqlite3
    conn = sqlite3.connect(str(DB_PATH))
    conn.executescript("DROP TABLE IF EXISTS questionnaire_responses; DROP TABLE IF EXISTS participants;")
    conn.close()
    db.init_db(DB_PATH)
    yield


client = TestClient(app)


class TestHealthz:
    def test_healthz(self):
        r = client.get("/api/healthz")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


class TestParticipants:
    def test_create(self):
        r = client.post("/api/participants", json={"participant_id": "TP-001", "display_name": "Alice"})
        assert r.status_code == 200
        assert r.json()["participant_id"] == "TP-001"

    def test_list(self):
        client.post("/api/participants", json={"participant_id": "A"})
        client.post("/api/participants", json={"participant_id": "B"})
        r = client.get("/api/participants")
        assert r.status_code == 200
        assert len(r.json()) == 2

    def test_get_not_found(self):
        r = client.get("/api/participants/NOPE")
        assert r.status_code == 404

    def test_link_session(self):
        client.post("/api/participants", json={"participant_id": "TP-001"})
        r = client.put("/api/participants/TP-001/session", json={"session_id": "s-42"})
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_delete(self):
        client.post("/api/participants", json={"participant_id": "TP-001"})
        r = client.delete("/api/participants/TP-001")
        assert r.status_code == 200
        r2 = client.get("/api/participants/TP-001")
        assert r2.status_code == 404


class TestQuestionnaire:
    def test_get_pre(self):
        r = client.get("/api/questionnaire/pre")
        assert r.status_code == 200
        data = r.json()
        assert data["phase"] == "pre"
        assert len(data["questions"]) > 0

    def test_get_post(self):
        r = client.get("/api/questionnaire/post")
        assert r.status_code == 200

    def test_unknown_phase_404(self):
        r = client.get("/api/questionnaire/during")
        assert r.status_code == 404


class TestAnswers:
    def _create_participant(self):
        client.post("/api/participants", json={"participant_id": "TP-001"})

    def test_save_and_get(self):
        self._create_participant()
        r = client.post("/api/participants/TP-001/answers/pre", json={"question_id": "q1", "answer": "hello"})
        assert r.status_code == 200
        r2 = client.get("/api/participants/TP-001/answers/pre")
        assert r2.status_code == 200
        assert len(r2.json()) == 1

    def test_bulk_save(self):
        self._create_participant()
        r = client.put("/api/participants/TP-001/answers/pre", json={"answers": {"q1": "a", "q2": 5}})
        assert r.status_code == 200
        answers = client.get("/api/participants/TP-001/answers/pre").json()
        assert len(answers) == 2

    def test_progress(self):
        self._create_participant()
        client.post("/api/participants/TP-001/answers/pre", json={"question_id": "q1", "answer": "a"})
        client.post("/api/participants/TP-001/answers/post", json={"question_id": "q2", "answer": "b"})
        r = client.get("/api/participants/TP-001/progress")
        assert r.status_code == 200
        assert r.json() == {"pre": 1, "post": 1}

    def test_resume_overwrites(self):
        self._create_participant()
        client.post("/api/participants/TP-001/answers/pre", json={"question_id": "q1", "answer": "first"})
        client.post("/api/participants/TP-001/answers/pre", json={"question_id": "q1", "answer": "second"})
        answers = client.get("/api/participants/TP-001/answers/pre").json()
        assert len(answers) == 1
        assert answers[0]["answer"] == "second"
