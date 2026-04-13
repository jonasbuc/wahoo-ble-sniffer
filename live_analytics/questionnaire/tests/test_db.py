"""Tests for the questionnaire database layer."""
import json
import os
import sqlite3
import tempfile

import pytest

from live_analytics.questionnaire import db  # noqa: E402


@pytest.fixture()
def db_path(tmp_path):
    """Create a fresh temp database for every test."""
    p = tmp_path / "test_qs.db"
    db.init_db(p)
    return p


# ── create / get participant ──────────────────────────────────
class TestParticipants:
    def test_create_and_get(self, db_path):
        db.create_participant(db_path, "TP-001", "Alice")
        p = db.get_participant(db_path, "TP-001")
        assert p is not None
        assert p["participant_id"] == "TP-001"
        assert p["display_name"] == "Alice"

    def test_create_duplicate_updates_name(self, db_path):
        db.create_participant(db_path, "TP-001", "Alice")
        db.create_participant(db_path, "TP-001", "Bob")
        p = db.get_participant(db_path, "TP-001")
        assert p["display_name"] == "Bob"

    def test_get_missing_returns_none(self, db_path):
        assert db.get_participant(db_path, "NOPE") is None

    def test_list_participants(self, db_path):
        db.create_participant(db_path, "A", "Alpha")
        db.create_participant(db_path, "B", "Beta")
        lst = db.list_participants(db_path)
        assert len(lst) == 2
        ids = {r["participant_id"] for r in lst}
        assert ids == {"A", "B"}

    def test_link_session(self, db_path):
        db.create_participant(db_path, "TP-001")
        db.link_session(db_path, "TP-001", "sess-42")
        p = db.get_participant(db_path, "TP-001")
        assert p["session_id"] == "sess-42"

    def test_delete_participant(self, db_path):
        db.create_participant(db_path, "TP-001")
        db.save_answer(db_path, "TP-001", "pre", "q1", "hello")
        db.delete_participant_data(db_path, "TP-001")
        assert db.get_participant(db_path, "TP-001") is None
        assert db.get_answers(db_path, "TP-001", "pre") == []


# ── answers ───────────────────────────────────────────────────
class TestAnswers:
    def test_save_and_retrieve(self, db_path):
        db.create_participant(db_path, "TP-001")
        db.save_answer(db_path, "TP-001", "pre", "q1", "hello")
        answers = db.get_answers(db_path, "TP-001", "pre")
        assert len(answers) == 1
        assert answers[0]["question_id"] == "q1"
        assert answers[0]["answer"] == "hello"

    def test_upsert_answer(self, db_path):
        db.create_participant(db_path, "TP-001")
        db.save_answer(db_path, "TP-001", "pre", "q1", "first")
        db.save_answer(db_path, "TP-001", "pre", "q1", "second")
        answers = db.get_answers(db_path, "TP-001", "pre")
        assert len(answers) == 1
        assert answers[0]["answer"] == "second"

    def test_save_complex_answer(self, db_path):
        db.create_participant(db_path, "TP-001")
        db.save_answer(db_path, "TP-001", "post", "q2", ["opt-a", "opt-b"])
        answers = db.get_answers(db_path, "TP-001", "post")
        assert answers[0]["answer"] == ["opt-a", "opt-b"]

    def test_bulk_save(self, db_path):
        db.create_participant(db_path, "TP-001")
        db.save_answers_bulk(db_path, "TP-001", "pre", {"q1": "a", "q2": 42, "q3": [1, 2]})
        answers = db.get_answers(db_path, "TP-001", "pre")
        assert len(answers) == 3

    def test_progress(self, db_path):
        db.create_participant(db_path, "TP-001")
        db.save_answer(db_path, "TP-001", "pre", "q1", "a")
        db.save_answer(db_path, "TP-001", "pre", "q2", "b")
        db.save_answer(db_path, "TP-001", "post", "q1", "c")
        progress = db.get_progress(db_path, "TP-001")
        assert progress == {"pre": 2, "post": 1}

    def test_answers_empty_for_unknown_phase(self, db_path):
        db.create_participant(db_path, "TP-001")
        assert db.get_answers(db_path, "TP-001", "pre") == []
        assert db.get_progress(db_path, "TP-001") == {}
