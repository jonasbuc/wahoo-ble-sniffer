"""
Live Analytics – Streamlit Dashboard

Reads live data from the FastAPI server via REST.
Run with:
    streamlit run streamlit_app.py -- --api http://127.0.0.1:8080 --refresh 5
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import os
import sys
from collections import deque
from pathlib import Path
from typing import Any

import pandas as pd
import requests
import streamlit as st


# ── Logging ──────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("live_analytics_dashboard")


# ── CLI / Environment Configuration ─────────────────────────────────
def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--api", default=None)
    parser.add_argument("--refresh", type=int, default=None)
    args, _ = parser.parse_known_args(sys.argv[1:])
    return args


_args = _parse_args()

API_BASE = (
    _args.api
    or os.getenv("LA_API_BASE")
    or "http://127.0.0.1:8080"
).rstrip("/")

def _safe_int(val: str | None, default: int) -> int:
    """Parse an int, returning *default* on None/empty/invalid."""
    if not val:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default

REFRESH_SEC = max(
    2,
    _args.refresh or _safe_int(os.getenv("LA_DASH_REFRESH_SEC"), 5),
)

DATA_DIR = Path(
    os.getenv(
        "LA_DATA_DIR",
        str(Path(__file__).resolve().parent.parent / "data"),
    ) or str(Path(__file__).resolve().parent.parent / "data")
)

MAX_CHART_ROWS = _safe_int(os.getenv("LA_DASH_MAX_CHART_ROWS"), 600)

# ── Startup diagnostics ─────────────────────────────────────────────
log.info("── Dashboard startup ───────────────────────────────")
log.info("  API_BASE       = %s", API_BASE)
log.info("  REFRESH_SEC    = %d", REFRESH_SEC)
log.info("  DATA_DIR       = %s (exists=%s)", DATA_DIR, DATA_DIR.exists())
log.info("  MAX_CHART_ROWS = %d", MAX_CHART_ROWS)
log.info("───────────────────────────────────────────────────")

st.set_page_config(page_title="🚴 Live Analytics", layout="wide")


# ── HTTP session ────────────────────────────────────────────────────
@st.cache_resource
def _http_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"Accept": "application/json"})
    return s


# ── Helper functions ────────────────────────────────────────────────
def _get(path: str) -> dict | list | None:
    """GET from the analytics API; returns None on failure and stores error info."""
    url = f"{API_BASE}{path}"
    try:
        r = _http_session().get(url, timeout=(1, 1.5))
        r.raise_for_status()
        st.session_state["_last_api_error"] = None
        return r.json()
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        st.session_state["_last_api_error"] = f"{path} -> {msg}"
        log.warning("GET failed for %s: %s", url, msg)
        return None


def _ms_to_str(unix_ms: int | None) -> str:
    """Convert unix-ms to human-readable local time string."""
    if unix_ms is None:
        return "—"
    try:
        return datetime.datetime.fromtimestamp(unix_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return "—"


def _read_last_jsonl_rows(path: Path, n: int = 600) -> pd.DataFrame:
    """
    Read only the last n JSONL rows.
    This avoids loading the entire telemetry file into memory on every refresh.
    Ignores incomplete/truncated lines while the file is actively being appended.
    """
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
                # likely a partially-written line while another process appends
                continue

        if not rows:
            return pd.DataFrame()

        return pd.DataFrame(rows)

    except Exception as exc:
        log.warning("Could not read JSONL %s: %s", path, exc)
        return pd.DataFrame()


@st.cache_data(ttl=REFRESH_SEC, show_spinner=False)
def _load_sessions() -> list[dict[str, Any]]:
    """Fetch session list from the API, cached for REFRESH_SEC seconds.

    The session list is displayed in the sidebar which re-renders on every
    full page rerun.  Without caching this fires an HTTP request on every
    rerun, including reruns triggered by the auto-refresh fragment.
    """
    sessions = _get("/api/sessions")
    if isinstance(sessions, list):
        return sessions
    return []


def _ensure_selected_session(session_ids: list[str]) -> None:
    """Keep _selected_session in sync — uses a private key to avoid
    conflicting with the selectbox widget key on the same rerun."""
    current = st.session_state.get("_selected_session")
    if current is None or current not in session_ids:
        st.session_state["_selected_session"] = session_ids[0] if session_ids else None


# ── Static title ────────────────────────────────────────────────────
st.title("🚴 Live Analytics Dashboard")


# ── Sidebar (kept outside fragment for stability) ──────────────────
with st.sidebar:
    st.header("⚙️ Dashboard")
    st.caption(f"API: `{API_BASE}`")
    st.caption(f"Refresh: every {REFRESH_SEC}s")
    st.caption(f"Data dir: `{DATA_DIR}`")

    if st.button("🔄 Reload app"):
        st.rerun()

    st.divider()
    st.header("📂 Sessions")

    sessions = _load_sessions()
    session_ids = [s.get("session_id") for s in sessions if s.get("session_id")]
    _ensure_selected_session(session_ids)

    if session_ids:
        current = st.session_state.get("_selected_session")
        current_index = session_ids.index(current) if current in session_ids else 0

        def _on_session_change() -> None:
            st.session_state["_selected_session"] = st.session_state["_session_selectbox"]

        st.selectbox(
            "Select session",
            session_ids,
            index=current_index,
            key="_session_selectbox",
            on_change=_on_session_change,
        )

        for s in sessions:
            sid = s.get("session_id", "")
            ct = s.get("record_count", 0)
            st.caption(f"`{sid[:12]}…` — {ct} records")
    else:
        st.session_state["_selected_session"] = None
        st.info("No sessions yet – waiting for data…")

    last_err = st.session_state.get("_last_api_error")
    if last_err:
        st.divider()
        st.error("Last API error")
        st.caption(last_err)


# ── Auto-refreshing live area only ──────────────────────────────────
@st.fragment(run_every=REFRESH_SEC)
def _dashboard_live() -> None:
    selected = st.session_state.get("_selected_session")

    # Health / live status
    health = _get("/healthz")
    live = _get("/api/live/latest")

    top1, top2 = st.columns([1, 3])

    with top1:
        if health and isinstance(health, dict) and health.get("status") == "ok":
            st.success("● Connected")
        else:
            st.error("● Unreachable")

    with top2:
        if live:
            st.caption("Receiving live data from backend")
        else:
            st.caption("No live payload received right now")

    st.divider()

    # Live latest metrics
    col1, col2, col3, col4 = st.columns(4)

    if isinstance(live, dict):
        scores = live.get("scores", {}) or {}
        col1.metric("🏎️ Speed", f"{float(live.get('speed', 0)):.1f} m/s")
        col2.metric("❤️ Heart Rate", f"{float(live.get('heart_rate', 0)):.0f} bpm")
        col3.metric("😰 Stress", f"{float(scores.get('stress_score', 0)):.1f} / 100")
        col4.metric("⚠️ Risk", f"{float(scores.get('risk_score', 0)):.1f} / 100")
    else:
        col1.metric("🏎️ Speed", "—")
        col2.metric("❤️ Heart Rate", "—")
        col3.metric("😰 Stress", "—")
        col4.metric("⚠️ Risk", "—")

    st.divider()

    # Session detail
    if not selected:
        st.info("Select a session in the sidebar.")
        return

    detail = _get(f"/api/sessions/{selected}")
    if not isinstance(detail, dict):
        st.warning("Could not load selected session details.")
        return

    st.subheader(f"📋 Session: `{selected}`")

    dcol1, dcol2, dcol3, dcol4 = st.columns(4)
    dcol1.write(f"**Scenario:** {detail.get('scenario_id') or '—'}")
    dcol2.write(f"**Records:** {detail.get('record_count', 0):,}")
    dcol3.write(f"**Start:** {_ms_to_str(detail.get('start_unix_ms'))}")
    dcol4.write(f"**End:** {_ms_to_str(detail.get('end_unix_ms'))}")

    ls = detail.get("latest_scores")
    if isinstance(ls, dict):
        st.subheader("📊 Scoring Breakdown")
        sc1, sc2, sc3, sc4, sc5, sc6 = st.columns(6)
        sc1.metric("Stress", f"{float(ls.get('stress_score', 0)):.1f}")
        sc2.metric("Risk", f"{float(ls.get('risk_score', 0)):.1f}")
        sc3.metric("Brake RT", f"{float(ls.get('brake_reaction_ms', 0)):.0f} ms")
        sc4.metric("Head Scans", f"{int(ls.get('head_scan_count_5s', 0))}")
        sc5.metric("Steer Var", f"{float(ls.get('steering_variance_3s', 0)):.2f}")
        sc6.metric("HR Δ 10s", f"{float(ls.get('hr_delta_10s', 0)):.1f} bpm")

    st.divider()

    # Charts
    st.subheader("📈 Recent Trends")

    jsonl_path = DATA_DIR / "sessions" / selected / "telemetry.jsonl"

    if not jsonl_path.exists():
        st.info("No telemetry file yet for this session.")
        return

    df = _read_last_jsonl_rows(jsonl_path, n=MAX_CHART_ROWS)

    if df.empty:
        st.info("Waiting for telemetry data…")
        return

    if "unity_time" not in df.columns:
        st.warning("Telemetry file does not contain 'unity_time'.")
        return

    try:
        df = df.sort_values("unity_time").drop_duplicates(subset=["unity_time"], keep="last")
    except Exception as exc:
        log.warning("Failed to sort/dedup telemetry DataFrame: %s", exc)

    chart_col1, chart_col2 = st.columns(2)

    with chart_col1:
        if "speed" in df.columns:
            st.line_chart(df.set_index("unity_time")[["speed"]], height=220)
            st.caption("Speed (m/s)")
        else:
            st.info("No speed data yet.")

    with chart_col2:
        if "heart_rate" in df.columns:
            st.line_chart(df.set_index("unity_time")[["heart_rate"]], height=220)
            st.caption("Heart Rate (bpm)")
        else:
            st.info("No heart-rate data yet.")

    chart_col3, chart_col4 = st.columns(2)

    with chart_col3:
        if "steering_angle" in df.columns:
            st.line_chart(df.set_index("unity_time")[["steering_angle"]], height=220)
            st.caption("Steering Angle (°)")
        else:
            st.info("No steering data yet.")

    with chart_col4:
        brake_cols = [c for c in ("brake_front", "brake_rear") if c in df.columns]
        if brake_cols:
            st.line_chart(df.set_index("unity_time")[brake_cols], height=220)
            st.caption("Brake Pressure")
        else:
            st.info("No brake data yet.")


# ── Render ──────────────────────────────────────────────────────────
_dashboard_live()
