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
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import pandas as pd
import requests
import streamlit as st

# ── Shared time utilities ─────────────────────────────────────────────
# The dashboard may be launched as ``streamlit run streamlit_app.py`` from
# any working directory, so we make the repo root importable explicitly.
_HERE = Path(__file__).resolve()
_REPO_ROOT = _HERE.parent.parent.parent  # Blu Sniffer/
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from live_analytics.app.utils.time_utils import fmt_unix_ms, fmt_iso  # noqa: E402


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
if not DATA_DIR.exists():
    log.warning(
        "DATA_DIR '%s' does not exist.  "
        "Telemetry charts will show 'No telemetry file' until the directory is created.  "
        "Override with the LA_DATA_DIR environment variable if the path is wrong.",
        DATA_DIR,
    )

st.set_page_config(page_title="🚴 Live Analytics", layout="wide")


# ── HTTP session ────────────────────────────────────────────────────
@st.cache_resource
def _http_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"Accept": "application/json"})
    # Disable automatic retries so we see failures immediately instead of
    # hanging for several seconds while requests silently retries.
    adapter = requests.adapters.HTTPAdapter(max_retries=0)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    return s


# ── Backend connectivity state (module-level, survives fragment reruns) ─
# We track consecutive failures so we only emit a terminal warning once
# when the backend goes down and once when it comes back — not on every
# auto-refresh tick.
_api_consecutive_failures: int = 0
_api_was_reachable: bool = True
# Module-level last-error string – updated by _get() whether it is called
# from inside @st.cache_data or directly.  The sidebar reads this so that
# the error display is always current even when _load_sessions() returns a
# cached result without re-running _get().
_last_api_error_msg: str | None = None
# Lock protecting the three module-level counters above.
# _get() is called from a ThreadPoolExecutor (up to 3 concurrent threads)
# so mutations must be serialised.
_api_state_lock = threading.Lock()


# ── Helper functions ────────────────────────────────────────────────
def _get(path: str) -> dict | list | None:
    """GET from the analytics API; returns None on any failure.

    Failure categories are logged distinctly so the terminal always tells you
    exactly what went wrong on the first (and every) failed call:

    - ConnectionError / ConnectionRefusedError  → backend not started / wrong port
    - Timeout                                   → backend overloaded or blocked
    - HTTPError (4xx / 5xx)                     → backend returned an error response
    - JSONDecodeError                           → backend returned non-JSON body
    - Any other Exception                       → unexpected; full traceback logged

    The success-state (consecutive-failure counter, error message) is only
    reset *after* the response has been fully parsed.  This prevents the
    misleading "backend unreachable" warning that would appear when the
    backend IS running but returned a non-JSON body (e.g. a startup HTML page).
    """
    global _api_consecutive_failures, _api_was_reachable, _last_api_error_msg

    url = f"{API_BASE}{path}"
    r = None  # keep reference so we can log the body on JSON parse failure
    try:
        r = _http_session().get(url, timeout=(2, 3))
        r.raise_for_status()
        data = r.json()   # parse FIRST – may raise JSONDecodeError

        # ── Only reach here on full success ──────────────────────────
        with _api_state_lock:
            prev_failures = _api_consecutive_failures
            _api_consecutive_failures = 0
            _api_was_reachable = True
            _last_api_error_msg = None
        if prev_failures > 0:
            log.info(
                "Analytics backend recovered after %d failed request(s) – now reachable at %s",
                prev_failures, API_BASE,
            )
        st.session_state["_last_api_error"] = None
        return data

    except requests.exceptions.ConnectionError as exc:
        category = "connection refused / not reachable"
        msg = f"ConnectionError: {exc}"
    except requests.exceptions.Timeout as exc:
        category = "request timed out"
        msg = f"Timeout: {exc}"
    except requests.exceptions.HTTPError as exc:
        status = r.status_code if r is not None else "?"
        body_preview = (r.text[:300] if r is not None else "") or ""
        category = f"HTTP {status} error"
        msg = f"HTTPError {status}: {exc}  body_preview={body_preview!r}"
    except (ValueError, requests.exceptions.JSONDecodeError) as exc:
        # ValueError covers both the stdlib json.JSONDecodeError (subclass) and
        # older requests versions that raise ValueError for bad JSON.
        status = r.status_code if r is not None else "?"
        body_preview = (r.text[:300] if r is not None else "") or ""
        category = "non-JSON response body"
        msg = (
            f"JSONDecodeError: {exc}  "
            f"(status={status}, body_preview={body_preview!r})"
        )
    except Exception as exc:
        category = "unexpected exception"
        msg = f"{type(exc).__name__}: {exc}"
        log.exception(
            "Unexpected error calling GET %s – this is likely a bug in the dashboard",
            url,
        )

    # ── Failure path ─────────────────────────────────────────────────
    with _api_state_lock:
        _api_consecutive_failures += 1
        n_fail = _api_consecutive_failures
        error_detail = f"GET {path} → {category}: {msg}"
        _last_api_error_msg = error_detail

    st.session_state["_last_api_error"] = error_detail

    if n_fail == 1:
        log.warning(
            "Analytics backend unreachable [%s] – GET %s failed: %s  "
            "(Dashboard is running in degraded mode; data may be stale)",
            category, url, msg,
        )
    elif n_fail % 10 == 0:
        log.warning(
            "Analytics backend still unreachable after %d consecutive failures "
            "[%s] (last attempt: GET %s → %s)",
            n_fail, category, url, msg,
        )
    else:
        log.debug(
            "GET %s failed (#%d) [%s]: %s",
            url, n_fail, category, msg,
        )
    return None


def _ms_to_str(unix_ms: int | None) -> str:
    """Convert a Unix-millisecond timestamp to a human-readable LOCAL time string.

    Delegates to :func:`live_analytics.app.utils.time_utils.fmt_unix_ms` so
    the format is identical to ``local_time`` fields in JSONL session logs,
    e.g. ``"2026-05-04 14:22:18 CEST"``.
    """
    return fmt_unix_ms(unix_ms)


def _iso_to_str(iso: str | None) -> str:
    """Convert a UTC ISO-8601 string to a human-readable LOCAL time string.

    Used for questionnaire ``created_at`` / ``updated_at`` fields from the
    API, e.g. ``"2026-05-04T12:19:21.776249+00:00"`` → ``"2026-05-04 14:19:21 CEST"``.
    """
    return fmt_iso(iso)


def _fmt_metric(val: Any, fmt: str, unit: str = "") -> str:
    """Format a numeric metric value for display in st.metric() or st.write().

    Returns ``"—"`` (an em-dash) when *val* is ``None``.  This makes it visually
    clear to the user that the field is absent from the backend response, rather
    than silently showing a potentially misleading ``0``.

    Args:
        val:  Raw value from the API response (may be ``None`` / JSON null).
        fmt:  Python format-spec string applied to ``float(val)``, e.g. ``".1f"``.
        unit: Optional unit string appended after the formatted number, e.g. ``" m/s"``.

    Examples::

        _fmt_metric(None, ".1f", " m/s")   # → "—"
        _fmt_metric(0,    ".1f", " m/s")   # → "0.0 m/s"
        _fmt_metric(3.14, ".1f", " m/s")   # → "3.1 m/s"
    """
    if val is None:
        return "—"
    try:
        return f"{float(val):{fmt}}{unit}"
    except (TypeError, ValueError):
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
    # The session list is cached for REFRESH_SEC seconds.  Use this button to
    # force an immediate re-fetch (e.g. after a session is created or deleted).
    if st.button("🔄 Refresh sessions", help=f"Session list is cached for {REFRESH_SEC}s"):
        _load_sessions.clear()
        st.rerun()
    st.caption(f"⏱ Session list auto-refreshes every {REFRESH_SEC}s (may be up to {REFRESH_SEC}s stale)")

    sessions = _load_sessions()
    session_ids = [
        s.get("session_id")
        for s in sessions
        if isinstance(s, dict) and s.get("session_id")
    ]
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
            if not isinstance(s, dict):
                continue
            sid = s.get("session_id", "")
            ct = s.get("record_count") or 0
            st.caption(f"`{sid[:12]}…` — {ct} records")
    else:
        st.session_state["_selected_session"] = None
        st.info("No sessions yet – waiting for data…")

    # Show last API error – prefer module-level (always current) over
    # session_state (may be stale on @st.cache_data hits).
    last_err = _last_api_error_msg or st.session_state.get("_last_api_error")
    if last_err:
        st.divider()
        st.error("Last API error")
        st.caption(last_err)


# ── Auto-refreshing live area only ──────────────────────────────────
@st.fragment(run_every=REFRESH_SEC)
def _dashboard_live() -> None:
    """Main live area – runs every REFRESH_SEC seconds independently.

    Wrapped in a top-level try/except so that any unexpected rendering
    exception is logged with full context and shown as a recoverable
    warning rather than crashing the entire page.
    """
    try:
        _render_live()
    except Exception as exc:
        log.exception(
            "Unhandled exception inside _dashboard_live() – fragment will show error box.  "
            "selected_session=%r  last_api_error=%r  exc=%s: %s",
            st.session_state.get("_selected_session"),
            _last_api_error_msg,
            type(exc).__name__, exc,
        )
        st.error(
            f"⚠️ Dashboard rendering error: **{type(exc).__name__}: {exc}**  \n"
            "Check the terminal log for details.  The page will auto-retry on next refresh."
        )
        # Do NOT re-raise: re-raising after st.error() causes Streamlit to render
        # a second error box (its own traceback) alongside the custom message above,
        # producing confusing duplicate error UI in the fragment area.


def _render_live() -> None:
    """Core rendering logic – separated so _dashboard_live can wrap it safely.

    The three backend calls (/healthz, /api/live/latest, /api/sessions/{id})
    are fired in parallel using a ThreadPoolExecutor so that the worst-case
    wait time is max(t1, t2, t3) instead of t1 + t2 + t3 (≤ 1.5 s vs ≤ 4.5 s
    at the configured timeout).
    """
    selected = st.session_state.get("_selected_session")

    # ── Parallel API fetch ───────────────────────────────────────────
    health: dict | None = None
    live: dict | None = None
    detail: dict | None = None

    _paths: dict[str, str] = {
        "health": "/healthz",
        "live": "/api/live/latest",
    }
    if selected:
        _paths["detail"] = f"/api/sessions/{selected}"

    with ThreadPoolExecutor(max_workers=len(_paths)) as ex:
        futures = {ex.submit(_get, path): key for key, path in _paths.items()}
        for fut in as_completed(futures):
            key = futures[fut]
            try:
                result = fut.result()
            except Exception as exc:
                log.warning("Parallel _get(%r) raised: %s: %s", key, type(exc).__name__, exc)
                result = None
            if key == "health":
                health = result
            elif key == "live":
                live = result
            elif key == "detail":
                detail = result

    top1, top2 = st.columns([1, 3])

    with top1:
        if health and isinstance(health, dict) and health.get("status") == "ok":
            if health.get("db_ok") is False:
                st.warning("● DB error")
            else:
                st.success("● Connected")
        else:
            st.error("● Unreachable")

    with top2:
        if live:
            st.caption("Receiving live data from backend")
        else:
            st.caption("No live payload received right now")
        # Surface DB health detail when the backend is up but the DB is broken
        if health and isinstance(health, dict) and health.get("db_ok") is False:
            st.warning(
                f"⚠️ Backend DB unreachable: `{health.get('db_detail', 'unknown error')}`  \n"
                f"Path: `{health.get('db_path', '?')}`"
            )

    st.divider()

    # ── Live latest metrics ──────────────────────────────────────────
    # _fmt_metric() returns "—" for None (JSON null), making it visually
    # clear that the field is absent rather than showing a misleading 0.
    col1, col2, col3, col4 = st.columns(4)

    if isinstance(live, dict):
        scores = live.get("scores") or {}
        col1.metric("🏎️ Speed",      _fmt_metric(live.get("speed"),              ".1f", " m/s"))
        col2.metric("❤️ Heart Rate", _fmt_metric(live.get("heart_rate"),         ".0f", " bpm"))
        col3.metric("😰 Stress",     _fmt_metric(scores.get("stress_score"),     ".1f", " / 100"))
        col4.metric("⚠️ Risk",       _fmt_metric(scores.get("risk_score"),       ".1f", " / 100"))
    else:
        col1.metric("🏎️ Speed", "—")
        col2.metric("❤️ Heart Rate", "—")
        col3.metric("😰 Stress", "—")
        col4.metric("⚠️ Risk", "—")

    st.divider()

    # ── Session detail ───────────────────────────────────────────────
    if not selected:
        st.info("Select a session in the sidebar.")
        return

    if not isinstance(detail, dict):
        log.warning(
            "Could not load session detail for '%s' – _get returned %r",
            selected, type(detail).__name__,
        )
        st.warning(
            f"Could not load details for session `{selected}`.  "
            "Check the terminal log for the exact failure reason."
        )
        return

    st.subheader(f"📋 Session: `{selected}`")

    dcol1, dcol2, dcol3, dcol4 = st.columns(4)
    dcol1.write(f"**Scenario:** {detail.get('scenario_id') or '—'}")
    dcol2.write(f"**Records:** {(detail.get('record_count') or 0):,}")
    dcol3.write(f"**Start:** {_ms_to_str(detail.get('start_unix_ms'))}")
    dcol4.write(f"**End:** {_ms_to_str(detail.get('end_unix_ms'))}")

    ls = detail.get("latest_scores")
    if isinstance(ls, dict):
        st.subheader("📊 Scoring Breakdown")
        sc1, sc2, sc3, sc4, sc5, sc6 = st.columns(6)
        sc1.metric("Stress",     _fmt_metric(ls.get("stress_score"),        ".1f"))
        sc2.metric("Risk",       _fmt_metric(ls.get("risk_score"),          ".1f"))
        sc3.metric("Brake RT",   _fmt_metric(ls.get("brake_reaction_ms"),   ".0f", " ms"))
        sc4.metric("Head Scans", "—" if ls.get("head_scan_count_5s") is None
                                     else str(int(ls["head_scan_count_5s"])))
        sc5.metric("Steer Var",  _fmt_metric(ls.get("steering_variance_3s"), ".2f"))
        sc6.metric("HR Δ 10s",   _fmt_metric(ls.get("hr_delta_10s"),         ".1f", " bpm"))

    st.divider()

    # ── Charts ───────────────────────────────────────────────────────
    st.subheader("📈 Recent Trends")

    jsonl_path = DATA_DIR / "sessions" / selected / "telemetry.jsonl"

    if not jsonl_path.exists():
        log.debug(
            "No telemetry file for session '%s' at '%s'", selected, jsonl_path
        )
        st.info("No telemetry file yet for this session.")
        return

    df = _read_last_jsonl_rows(jsonl_path, n=MAX_CHART_ROWS)

    if df.empty:
        st.info("Waiting for telemetry data…")
        return

    if "unity_time" not in df.columns:
        log.warning(
            "Telemetry file for session '%s' has no 'unity_time' column – "
            "columns present: %s",
            selected, list(df.columns),
        )
        st.warning("Telemetry file does not contain 'unity_time'.")
        return

    try:
        df = df.sort_values("unity_time").drop_duplicates(subset=["unity_time"], keep="last")
    except Exception as exc:
        log.warning("Failed to sort/dedup telemetry DataFrame for session '%s': %s", selected, exc)

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
