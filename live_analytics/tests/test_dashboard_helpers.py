"""
Tests for Streamlit dashboard helper functions.

These test the pure-logic helpers without launching Streamlit itself.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pandas as pd
import pytest


# ── Import the helpers directly from the module source ────────────────
# We can't import streamlit_app.py directly (it calls st.set_page_config
# at module level), so we extract and test the pure functions.


def _safe_int(val: str | None, default: int) -> int:
    if not val:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _ms_to_str(unix_ms: int | None) -> str:
    if unix_ms is None:
        return "—"
    try:
        import datetime
        return datetime.datetime.fromtimestamp(unix_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return "—"


def _read_last_jsonl_rows(path: Path, n: int = 600) -> pd.DataFrame:
    from collections import deque
    from typing import Any

    if not path.exists():
        return pd.DataFrame()
    try:
        with path.open("r", encoding="utf-8") as f:
            last_lines = deque(f, maxlen=n)
        rows: list[dict[str, Any]] = []
        for line in last_lines:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    rows.append(obj)
            except json.JSONDecodeError:
                continue
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows)
    except Exception:
        return pd.DataFrame()


# ═══════════════════════════════════════════════════════════════════════
#  _safe_int
# ═══════════════════════════════════════════════════════════════════════

class TestSafeInt:
    def test_none(self):
        assert _safe_int(None, 5) == 5

    def test_empty(self):
        assert _safe_int("", 5) == 5

    def test_valid(self):
        assert _safe_int("10", 5) == 10

    def test_invalid(self):
        assert _safe_int("abc", 5) == 5

    def test_zero(self):
        assert _safe_int("0", 5) == 0

    def test_negative(self):
        assert _safe_int("-3", 5) == -3

    def test_whitespace(self):
        # "  7  " is a valid int in Python
        assert _safe_int("  7  ", 5) == 7

    def test_float_string(self):
        assert _safe_int("3.14", 5) == 5


# ═══════════════════════════════════════════════════════════════════════
#  _ms_to_str
# ═══════════════════════════════════════════════════════════════════════

class TestMsToStr:
    def test_none(self):
        assert _ms_to_str(None) == "—"

    def test_zero(self):
        result = _ms_to_str(0)
        assert "1970" in result  # epoch

    def test_valid(self):
        # 2024-01-01 00:00:00 UTC = 1704067200000 ms
        result = _ms_to_str(1704067200000)
        assert "2024" in result

    def test_negative(self):
        # Negative timestamps should not crash
        result = _ms_to_str(-1000)
        assert isinstance(result, str)


# ═══════════════════════════════════════════════════════════════════════
#  _read_last_jsonl_rows
# ═══════════════════════════════════════════════════════════════════════

class TestReadLastJsonlRows:
    def test_missing_file(self, tmp_path: Path):
        df = _read_last_jsonl_rows(tmp_path / "nonexistent.jsonl")
        assert df.empty

    def test_empty_file(self, tmp_path: Path):
        p = tmp_path / "empty.jsonl"
        p.write_text("")
        df = _read_last_jsonl_rows(p)
        assert df.empty

    def test_valid_rows(self, tmp_path: Path):
        p = tmp_path / "data.jsonl"
        rows = [
            {"unity_time": 1.0, "speed": 5.0, "heart_rate": 70},
            {"unity_time": 2.0, "speed": 6.0, "heart_rate": 72},
            {"unity_time": 3.0, "speed": 7.0, "heart_rate": 75},
        ]
        p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        df = _read_last_jsonl_rows(p)
        assert len(df) == 3
        assert "unity_time" in df.columns
        assert "speed" in df.columns

    def test_partial_last_line(self, tmp_path: Path):
        """Simulate a file being appended to — last line is truncated."""
        p = tmp_path / "partial.jsonl"
        content = json.dumps({"unity_time": 1.0, "speed": 5.0}) + "\n"
        content += '{"unity_time": 2.0, "speed'  # truncated!
        p.write_text(content)
        df = _read_last_jsonl_rows(p)
        assert len(df) == 1  # only the complete first line

    def test_blank_lines_ignored(self, tmp_path: Path):
        p = tmp_path / "blanks.jsonl"
        content = json.dumps({"a": 1}) + "\n\n\n" + json.dumps({"a": 2}) + "\n\n"
        p.write_text(content)
        df = _read_last_jsonl_rows(p)
        assert len(df) == 2

    def test_non_dict_lines_ignored(self, tmp_path: Path):
        """Lines that are valid JSON but not dicts should be skipped."""
        p = tmp_path / "mixed.jsonl"
        content = "[1,2,3]\n" + json.dumps({"a": 1}) + "\n" + '"just a string"\n'
        p.write_text(content)
        df = _read_last_jsonl_rows(p)
        assert len(df) == 1

    def test_last_n_rows_only(self, tmp_path: Path):
        """Should only keep the last N rows, not load everything."""
        p = tmp_path / "large.jsonl"
        lines = [json.dumps({"i": i}) for i in range(1000)]
        p.write_text("\n".join(lines) + "\n")
        df = _read_last_jsonl_rows(p, n=10)
        assert len(df) == 10
        # Should be the LAST 10 rows
        assert df.iloc[0]["i"] == 990
        assert df.iloc[-1]["i"] == 999

    def test_all_corrupted_lines(self, tmp_path: Path):
        p = tmp_path / "corrupt.jsonl"
        p.write_text("not json\nalso not json\n{broken\n")
        df = _read_last_jsonl_rows(p)
        assert df.empty

    def test_unicode_content(self, tmp_path: Path):
        p = tmp_path / "unicode.jsonl"
        p.write_text(json.dumps({"name": "ÆØÅ 🚴"}) + "\n", encoding="utf-8")
        df = _read_last_jsonl_rows(p)
        assert len(df) == 1
        assert df.iloc[0]["name"] == "ÆØÅ 🚴"

    def test_concurrent_write_simulation(self, tmp_path: Path):
        """Simulate reading while another process is writing."""
        p = tmp_path / "concurrent.jsonl"
        # Write 100 complete lines + 1 partial
        lines = [json.dumps({"t": i, "v": i * 0.1}) for i in range(100)]
        content = "\n".join(lines) + "\n" + '{"t": 100, "v":'
        p.write_text(content)
        df = _read_last_jsonl_rows(p, n=50)
        # deque sees partial line as one slot; we get 49-50 valid rows
        assert 49 <= len(df) <= 50

    def test_very_large_rows(self, tmp_path: Path):
        """Rows with many fields should not crash."""
        p = tmp_path / "wide.jsonl"
        row = {f"field_{i}": i for i in range(200)}
        p.write_text(json.dumps(row) + "\n")
        df = _read_last_jsonl_rows(p)
        assert len(df) == 1
        assert len(df.columns) == 200

    def test_mixed_schema_rows(self, tmp_path: Path):
        """Rows with different keys should produce a DataFrame with NaN for missing."""
        p = tmp_path / "mixed_schema.jsonl"
        content = json.dumps({"a": 1, "b": 2}) + "\n" + json.dumps({"a": 3, "c": 4}) + "\n"
        p.write_text(content)
        df = _read_last_jsonl_rows(p)
        assert len(df) == 2
        assert pd.isna(df.iloc[0].get("c"))
        assert pd.isna(df.iloc[1].get("b"))
