# live_analytics/

> **Se den fulde dokumentation i rodets [`README.md`](../README.md).**

Dette undermappens README er bevaret som en kort reference til `live_analytics/`-modulet.
Alle installations-, opstarts- og konfigurationsinstruktioner er i rodets README.

---

## Hvad er i denne mappe?

```
live_analytics/
├── app/              FastAPI analytics-server (HTTP :8080 + WS ingest :8766)
├── dashboard/        Streamlit dashboard (:8501)
├── questionnaire/    Questionnaire-service (FastAPI :8090)
├── system_check/     System Check GUI (FastAPI :8095)
├── scripts/          Hjælpe-scripts (init_db.py, simulate_ride.py, …)
├── data/             Runtime-data (auto-oprettet: live_analytics.db, sessions/)
└── tests/            pytest-tests for analytics-pipeline
```

## Hurtig reference — porte

| Service | Port |
|---|---|
| Analytics API (HTTP) | **8080** |
| WS ingest (Unity → server) | **8766** |
| Dashboard | **8501** |
| Questionnaire | **8090** |
| System Check GUI | **8095** |

## Hurtig reference — miljøvariable

| Variabel | Standard |
|---|---|
| `LA_HTTP_PORT` | `8080` |
| `LA_WS_INGEST_PORT` | `8766` |
| `LA_DB_PATH` | `live_analytics/data/live_analytics.db` |
| `LA_SESSIONS_DIR` | `live_analytics/data/sessions` |
| `LA_LOG_LEVEL` | `INFO` |
| `QS_PORT` | `8090` |
| `SC_PORT` | `8095` |

## Start (fra repo-roden)

```bash
# Alle services på én gang
python starters/launcher.py

# Eller enkeltvis
python -m uvicorn live_analytics.app.main:app --port 8080
streamlit run live_analytics/dashboard/streamlit_app.py -- --api http://127.0.0.1:8080

# Simulér en tur (kræver kørende API)
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
  ├── ws_ingest.py      – WebSocket ingest fra Unity
  │     └── ved ny session: resolve_participant() → questionnaire API → gemmer participant_id
  ├── api_sessions.py   – REST API for session-data
  │     └── PUT /api/sessions/{id}/participant  – manuel deltager-kobling
  ├── ws_dashboard.py   – WebSocket push til dashboard
  ├── scoring/rules.py  – Rule-based stress & risk scoring
  ├── storage/
  │   ├── sqlite_store.py – Session metadata, scores & participant_id (WAL mode)
  │   ├── raw_writer.py   – Per-session JSONL raw telemetry
  │   └── web_api_client.py – Udgående HTTP (puls → QS + ekstern DB)
  │         ├── send_pulse() → dual-write: questionnaire.db + ekstern PulseData
  │         └── resolve_participant() → slår deltager op fra questionnaire API
  └── data/
      ├── live_analytics.db
      └── sessions/{session_id}/telemetry.jsonl

  Questionnaire Service (:8090)
  ├── questionnaire/app.py  – FastAPI + SPA
  ├── questionnaire/db.py   – SQLite CRUD (participants, pulse_data, svar)
  │     └── get_participant_by_session()  – opslag via session_id
  └── GET /api/participants/by-session/{session_id}  – endpoint til analytics

  Ekstern forsknings-DB (10.200.130.98:5001)
  └── POST /api/cardatasqlite/loglitepd  – PulseData { UserId=TestPersonNumber, Pulse }

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
| PUT | `/api/sessions/{session_id}/participant` | Kobl testperson til session — body: `{ "participant_id": "P001" }` |
| GET | `/api/live/latest` | Latest live state snapshot |
| WS  | `/ws/dashboard` | Real-time dashboard feed |

### Questionnaire API (:8090)

| Method | Path | Description |
|---|---|---|
| POST | `/api/participants` | Opret testperson |
| GET  | `/api/participants` | Alle testpersoner |
| GET  | `/api/participants/{id}` | Hent enkelt testperson |
| GET  | `/api/participants/by-session/{session_id}` | Hent testperson via analytics-session ID |
| PUT  | `/api/participants/{id}/session` | Kobl analytics session til testperson |
| POST | `/api/pulse` | Modtag puls-sample |
| GET  | `/api/pulse/{session_id}` | Hent puls for en session |

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
| `LA_LOG_LEVEL` | `INFO` | Python log level |
| `LA_HR_BASELINE_BPM` | `70.0` | Resting HR for scoring |

### Udgående HTTP / ekstern DB (`web_api_client`)

| Variable | Default | Description |
|---|---|---|
| `QS_BASE_URL` | `http://localhost:8090` | URL til questionnaire-service |
| `EXTERNAL_API_URL` | `https://10.200.130.98:5001` | Ekstern forsknings-API |
| `EXTERNAL_USER_ID` | `0` | Fallback `UserId` (TestPersonNumber) — bruges kun hvis questionnaire ikke har linket en deltager til sessionen |

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
