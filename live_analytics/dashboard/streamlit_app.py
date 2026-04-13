"""
Live Analytics – Streamlit Dashboard

Reads live data from the FastAPI server via REST and WebSocket.
Run with:  streamlit run streamlit_app.py -- --api http://127.0.0.1:8080
"""

from __future__ import annotations

import datetime
import os
import time

import pandas as pd
import requests
import streamlit as st

# ── Configuration ────────────────────────────────────────────────────
API_BASE = os.getenv("LA_API_BASE", "http://127.0.0.1:8080")
REFRESH_INTERVAL = float(os.getenv("LA_DASH_REFRESH_SEC", "1.5"))

st.set_page_config(page_title="🚴 Live Analytics", layout="wide")


# ── Helper functions ─────────────────────────────────────────────────

def _get(path: str) -> dict | list | None:
    """GET from the analytics API; returns None on failure."""
    try:
        r = requests.get(f"{API_BASE}{path}", timeout=2)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        st.sidebar.warning(f"API error: {exc}")
        return None


def _ms_to_str(unix_ms: int | None) -> str:
    """Convert unix-ms to human-readable local time string."""
    if not unix_ms:
        return "—"
    return datetime.datetime.fromtimestamp(unix_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")


# ── Sidebar: sessions + health ───────────────────────────────────────
st.sidebar.header("🔌 Server")
health = _get("/healthz")
if health and health.get("status") == "ok":
    st.sidebar.success("● Connected")
else:
    st.sidebar.error("● Unreachable")

st.sidebar.header("📂 Sessions")
sessions = _get("/api/sessions")
session_ids: list[str] = []
if sessions:
    session_ids = [s["session_id"] for s in sessions]
    selected = st.sidebar.selectbox("Select session", session_ids, index=0)
    for s in sessions:
        ct = s.get("record_count", 0)
        st.sidebar.caption(f"`{s['session_id'][:12]}…`  —  {ct} records")
else:
    selected = None
    st.sidebar.info("No sessions yet – waiting for data…")


# ── Title ─────────────────────────────────────────────────────────────
st.title("🚴  Live Analytics Dashboard")

# ── Live latest metrics ──────────────────────────────────────────────
live = _get("/api/live/latest")

col1, col2, col3, col4 = st.columns(4)

if live:
    scores = live.get("scores", {})
    col1.metric("🏎️ Speed", f"{live.get('speed', 0):.1f} m/s")
    col2.metric("❤️ Heart Rate", f"{live.get('heart_rate', 0):.0f} bpm")
    col3.metric("😰 Stress", f"{scores.get('stress_score', 0):.1f} / 100")
    col4.metric("⚠️ Risk", f"{scores.get('risk_score', 0):.1f} / 100")
else:
    col1.metric("🏎️ Speed", "—")
    col2.metric("❤️ Heart Rate", "—")
    col3.metric("😰 Stress", "—")
    col4.metric("⚠️ Risk", "—")

st.divider()

# ── Session detail ───────────────────────────────────────────────────
if selected:
    detail = _get(f"/api/sessions/{selected}")
    if detail:
        st.subheader(f"📋 Session: `{selected}`")
        dcol1, dcol2, dcol3, dcol4 = st.columns(4)
        dcol1.write(f"**Scenario:** {detail.get('scenario_id') or '—'}")
        dcol2.write(f"**Records:** {detail.get('record_count', 0):,}")
        dcol3.write(f"**Start:** {_ms_to_str(detail.get('start_unix_ms'))}")
        dcol4.write(f"**End:** {_ms_to_str(detail.get('end_unix_ms'))}")

        # Scoring breakdown
        ls = detail.get("latest_scores")
        if ls:
            st.subheader("📊 Scoring Breakdown")
            sc1, sc2, sc3, sc4, sc5, sc6 = st.columns(6)
            sc1.metric("Stress", f"{ls.get('stress_score', 0):.1f}")
            sc2.metric("Risk", f"{ls.get('risk_score', 0):.1f}")
            sc3.metric("Brake RT", f"{ls.get('brake_reaction_ms', 0):.0f} ms")
            sc4.metric("Head Scans", f"{ls.get('head_scan_count_5s', 0)}")
            sc5.metric("Steer Var", f"{ls.get('steering_variance_3s', 0):.2f}")
            sc6.metric("HR Δ 10s", f"{ls.get('hr_delta_10s', 0):.1f} bpm")

        st.divider()

        # ── Live charts from JSONL data ──────────────────────────────
        st.subheader("📈 Recent Trends")

        # Read last N records from the session's JSONL file
        jsonl_path = os.path.join(
            os.getenv("LA_DATA_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")),
            "sessions", selected, "telemetry.jsonl",
        )
        if os.path.exists(jsonl_path):
            try:
                df = pd.read_json(jsonl_path, lines=True)
                # Keep only last 600 rows (~30s at 20Hz)
                df = df.tail(600)
                if not df.empty and "unity_time" in df.columns:
                    chart_col1, chart_col2 = st.columns(2)
                    with chart_col1:
                        st.line_chart(df.set_index("unity_time")[["speed"]], height=220)
                        st.caption("Speed (m/s)")
                    with chart_col2:
                        st.line_chart(df.set_index("unity_time")[["heart_rate"]], height=220)
                        st.caption("Heart Rate (bpm)")

                    if "steering_angle" in df.columns:
                        chart_col3, chart_col4 = st.columns(2)
                        with chart_col3:
                            st.line_chart(df.set_index("unity_time")[["steering_angle"]], height=220)
                            st.caption("Steering Angle (°)")
                        with chart_col4:
                            brake_cols = [c for c in ["brake_front", "brake_rear"] if c in df.columns]
                            if brake_cols:
                                st.line_chart(df.set_index("unity_time")[brake_cols], height=220)
                                st.caption("Brake Pressure")
                else:
                    st.info("Waiting for telemetry data…")
            except Exception as exc:
                st.warning(f"Could not read JSONL: {exc}")
        else:
            st.info("No telemetry file yet for this session.")

# ── Auto-refresh ─────────────────────────────────────────────────────
time.sleep(REFRESH_INTERVAL)
st.rerun()
