# live_analytics/

> **Full documentation is in the root [`README.md`](../README.md).**

This sub-directory README is a concise quick-reference for the `live_analytics/` module.
Installation, startup, and configuration instructions are in the root README.

---

## What's in this directory?

```
live_analytics/
├── app/              FastAPI analytics server (HTTP :8080 + WS ingest :8766)
│   ├── pulse_session_logger.py   PulseSessionLogger — dedicated pulse log per participant/session
│   └── api_pulse_session.py      POST /api/pulse-session/start|end, GET /current
├── dashboard/        Streamlit dashboard (:8501)
├── questionnaire/    Questionnaire service (FastAPI :8090)
├── system_check/     System Check GUI (FastAPI :8095)
├── scripts/          Helper scripts (init_db.py, simulate_ride.py, …)
├── data/             Runtime data (auto-created: live_analytics.db, sessions/, participants/)
├── logs/pulse/       Dedicated pulse-log JSONL files per participant/session (auto-created)
└── tests/            pytest tests for the analytics pipeline
```

## Quick reference — ports

| Service | Port |
|---|---|
| Analytics API (HTTP) | **8080** |
| WS ingest (Unity → server) | **8766** |
| Dashboard | **8501** |
| Questionnaire | **8090** |
| System Check GUI | **8095** |

## Quick reference — environment variables

| Variable | Default |
|---|---|
| `LA_HTTP_PORT` | `8080` |
| `LA_WS_INGEST_PORT` | `8766` |
| `LA_DB_PATH` | `live_analytics/data/live_analytics.db` |
| `LA_SESSIONS_DIR` | `live_analytics/data/sessions` |
| `LA_PARTICIPANTS_DIR` | `live_analytics/data/participants` |
| `LA_PULSE_LOG_DIR` | `logs/pulse` |
| `LA_LOG_LEVEL` | `INFO` |
| `QS_PORT` | `8090` |
| `SC_PORT` | `8095` |

## Start (from the repo root)

```bash
# All services at once
python starters/launcher.py

# Or individually
python -m uvicorn live_analytics.app.main:app --port 8080
streamlit run live_analytics/dashboard/streamlit_app.py -- --api http://127.0.0.1:8080

# Simulate a ride (requires running API)
python live_analytics/scripts/simulate_ride.py --duration 60 --hz 20
```

## Tests

```bash
pytest live_analytics/tests/
```

## Architecture

```
Unity VR Simulator
  └── TelemetryPublisher (C#)
        │  WebSocket (ws://127.0.0.1:8766/ws/ingest)
        ▼
  FastAPI Analytics Server (Python 3.11)
  ├── ws_ingest.py      — WebSocket ingest from Unity
  │     ├── on new session: resolve_participant() → questionnaire API → stores participant_id
  │     ├── writes SESSION_START marker to pulse.jsonl on participant resolve
  │     ├── writes SESSION_END marker to pulse.jsonl on Unity disconnect
  │     ├── calls PulseSessionLogger.start_session() / write_pulse() / close_session()
  │     └── handles explicit {event: start_session|end_session} from Unity C#
  ├── api_sessions.py   — REST API for session data
  │     └── PUT /api/sessions/{id}/participant  — manual participant linking
  ├── api_pulse_session.py  — Dedicated pulse-session API
  │     ├── POST /api/pulse-session/start   — open new pulse-log file
  │     ├── POST /api/pulse-session/end     — close pulse-log file
  │     └── GET  /api/pulse-session/current — active sessions
  ├── pulse_session_logger.py  — PulseSessionLogger class
  │     └── logs/pulse/<id>_<YYYYMMDD_HHMMSSffffff>_pulse_log.jsonl
  │           session_start | pulse | session_end (JSONL)
  ├── ws_dashboard.py   — WebSocket push to dashboard
  ├── scoring/rules.py  — Rule-based stress & risk scoring
  ├── storage/
  │   ├── sqlite_store.py     — Session metadata, scores & participant_id (WAL mode)
  │   ├── raw_writer.py       — Per-session JSONL raw telemetry
  │   ├── participant_logs.py — Per-participant log directories + pulse.jsonl SESSION markers
  │   └── web_api_client.py   — Outbound HTTP (pulse → questionnaire + external DB)
  │         ├── send_pulse() → dual-write: questionnaire.db + external PulseData
  │         └── resolve_participant() → looks up participant from questionnaire API
  └── data/
      ├── live_analytics.db
      ├── sessions/{session_id}.jsonl
      └── participants/
          └── <participant_id>/
              ├── info.json       — Metadata (id, name, created_at)
              ├── pulse.jsonl     — ALL HR samples + SESSION_START/END markers
              └── session.jsonl   — Session start/end events

  Questionnaire Service (:8090)
  ├── questionnaire/app.py  — FastAPI + SPA
  ├── questionnaire/db.py   — SQLite CRUD (participants, pulse_data, answers)
  │     └── get_participant_by_session()  — look-up via session_id
  └── GET /api/participants/by-session/{session_id}  — endpoint for analytics

  External research DB (10.200.130.98:5001)
  └── POST /api/cardatasqlite/loglitepd  — PulseData { UserId=ParticipantNumber, Pulse }

  Streamlit Dashboard (:8501)
  └── dashboard/streamlit_app.py
```

## Prerequisites

- **Python 3.11+** (Windows recommended for primary use)
- **pip** or a virtualenv manager

## Setup (Windows PowerShell)

```powershell
# 1. Navigate to the live_analytics folder
cd live_analytics

# 2. Create a virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 3. Install dependencies
pip install -r requirements.txt

# 4. Initialise the database
python scripts\init_db.py
```

## Running

### Start the analytics server

```powershell
.\scripts\run_server.ps1
```

Or manually:

```powershell
cd live_analytics
python -m live_analytics.app.main
```

The server starts:
| Endpoint | URL |
|---|---|
| HTTP API | `http://127.0.0.1:8080` |
| WS Ingest | `ws://127.0.0.1:8765/ws/ingest` |
| Health check | `GET http://127.0.0.1:8080/healthz` |

### Start the dashboard

```powershell
.\scripts\run_dashboard.ps1
```

Or manually:

```powershell
streamlit run dashboard/streamlit_app.py --server.port 8501
```

Dashboard: `http://127.0.0.1:8501`

## API Endpoints

### Analytics API (:8080)

| Method | Path | Description |
|---|---|---|
| GET | `/healthz` | Health check |
| GET | `/api/sessions` | List all sessions |
| GET | `/api/sessions/{session_id}` | Session detail with latest scores |
| PUT | `/api/sessions/{session_id}/participant` | Link participant to session — body: `{ "participant_id": "7" }` |
| GET | `/api/live/latest` | Latest live state snapshot |
| WS  | `/ws/dashboard` | Real-time dashboard feed |
| POST | `/api/pulse-session/start` | Open new pulse-log file for a participant |
| POST | `/api/pulse-session/end` | Close active pulse-log file (404 if none active) |
| GET  | `/api/pulse-session/current` | All active pulse sessions |
| GET  | `/api/pulse-session/current/{id}` | Active pulse session for one participant |

### Questionnaire API (:8090)

| Method | Path | Description |
|---|---|---|
| POST | `/api/participants` | Create participant |
| GET  | `/api/participants` | List all participants |
| GET  | `/api/participants/{id}` | Get single participant |
| GET  | `/api/participants/by-session/{session_id}` | Get participant by analytics session ID |
| PUT  | `/api/participants/{id}/session` | Link analytics session to participant |
| POST | `/api/pulse` | Receive pulse sample |
| GET  | `/api/pulse/{session_id}` | Get pulse data for a session |

## Environment Variables

### Analytics API

| Variable | Default | Description |
|---|---|---|
| `LA_HTTP_HOST` | `0.0.0.0` | FastAPI bind host |
| `LA_HTTP_PORT` | `8080` | FastAPI HTTP port |
| `LA_WS_INGEST_HOST` | `0.0.0.0` | Ingest WS bind host |
| `LA_WS_INGEST_PORT` | `8766` | Ingest WS port |
| `LA_DASHBOARD_PORT` | `8501` | Streamlit port |
| `LA_DATA_DIR` | `live_analytics/data` | Data directory |
| `LA_DB_PATH` | `live_analytics/data/live_analytics.db` | SQLite DB path |
| `LA_PARTICIPANTS_DIR` | `live_analytics/data/participants` | Per-participant log directories |
| `LA_PULSE_LOG_DIR` | `logs/pulse` | Dedicated pulse-log JSONL files (PulseSessionLogger) |
| `LA_LOG_LEVEL` | `INFO` | Python log level |
| `LA_HR_BASELINE_BPM` | `70.0` | Resting HR for scoring |

### Outbound HTTP / external DB (`web_api_client`)

| Variable | Default | Description |
|---|---|---|
| `QS_BASE_URL` | `http://localhost:8090` | URL for the questionnaire service |
| `EXTERNAL_API_URL` | `https://10.200.130.98:5001` | External research API |
| `EXTERNAL_USER_ID` | `0` | Fallback `UserId` (participant number) — used only when the questionnaire has not yet linked a participant to the session |

## Scoring Metrics

| Metric | Description |
|---|---|
| `stress_score` | 0–100, driven by HR delta and steering variance |
| `risk_score` | 0–100, driven by speed, steering, head scanning |
| `brake_reaction_ms` | Milliseconds from trigger to first brake input |
| `head_scan_count_5s` | Direction-change count in last 5 seconds |
| `steering_variance_3s` | Steering angle variance over last 3 seconds |
| `hr_delta_10s` | Absolute HR change over last 10 seconds |

## Testing

```powershell
cd live_analytics
python -m pytest tests/ -v
```

## Unity Integration

See `Assets/Scripts/LiveAnalytics/` for the C# telemetry publisher.
Attach `TelemetryPublisher` to any persistent GameObject and assign a
`TelemetryConfig` ScriptableObject in the Inspector.

The publisher is **additive** – it does not modify existing VRSF logging
or gameplay controllers.
