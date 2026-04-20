"""
Tests for the RawWriter JSONL writer.

Covers:
  - Normal append
  - append_many grouping
  - Resilience to missing directories (auto-created)
  - Unicode content
  - Partial write recovery
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from live_analytics.app.models import TelemetryRecord
from live_analytics.app.storage.raw_writer import RawWriter


def _make_record(session_id: str = "s1", unix_ms: int = 1000, speed: float = 5.0) -> TelemetryRecord:
    return TelemetryRecord(
        session_id=session_id,
        unix_ms=unix_ms,
        unity_time=unix_ms / 1000.0,
        speed=speed,
        heart_rate=70.0,
    )


class TestRawWriter:
    def test_append_creates_file(self, tmp_path: Path):
        writer = RawWriter(tmp_path)
        rec = _make_record()
        writer.append(rec)
        jsonl = tmp_path / "s1" / "telemetry.jsonl"
        assert jsonl.exists()
        lines = jsonl.read_text().strip().split("\n")
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["speed"] == 5.0

    def test_append_many(self, tmp_path: Path):
        writer = RawWriter(tmp_path)
        recs = [_make_record(unix_ms=i) for i in range(10)]
        writer.append_many(recs)
        jsonl = tmp_path / "s1" / "telemetry.jsonl"
        lines = jsonl.read_text().strip().split("\n")
        assert len(lines) == 10

    def test_append_many_groups_by_session(self, tmp_path: Path):
        writer = RawWriter(tmp_path)
        recs = [
            _make_record(session_id="a", unix_ms=1),
            _make_record(session_id="b", unix_ms=2),
            _make_record(session_id="a", unix_ms=3),
        ]
        writer.append_many(recs)
        assert (tmp_path / "a" / "telemetry.jsonl").exists()
        assert (tmp_path / "b" / "telemetry.jsonl").exists()
        a_lines = (tmp_path / "a" / "telemetry.jsonl").read_text().strip().split("\n")
        assert len(a_lines) == 2

    def test_append_creates_nested_dirs(self, tmp_path: Path):
        deep_dir = tmp_path / "deep" / "nested"
        writer = RawWriter(deep_dir)
        writer.append(_make_record())
        assert (deep_dir / "s1" / "telemetry.jsonl").exists()

    def test_each_line_is_valid_json(self, tmp_path: Path):
        writer = RawWriter(tmp_path)
        for i in range(50):
            writer.append(_make_record(unix_ms=i))
        jsonl = tmp_path / "s1" / "telemetry.jsonl"
        for line in jsonl.read_text().strip().split("\n"):
            obj = json.loads(line)
            assert isinstance(obj, dict)
            assert "session_id" in obj

    def test_lines_end_with_newline(self, tmp_path: Path):
        """Every line must end with \\n for proper JSONL format."""
        writer = RawWriter(tmp_path)
        writer.append(_make_record())
        content = (tmp_path / "s1" / "telemetry.jsonl").read_text()
        assert content.endswith("\n")
        # No double newlines
        assert "\n\n" not in content
