# Live Analytics

Real-time telemetry processing and dashboarding for the Wahoo BLE VR Cycling Simulator.

## Architecture

```
Unity VR Simulator
  └── TelemetryPublisher (C#)
        │  WebSocket (ws://127.0.0.1:8765/ws/ingest)
        ▼
  FastAPI Analytics Server (Python 3.11)
  ├── ws_ingest.py      – WebSocket ingest from Unity
  ├── api_sessions.py   – REST API for session data
  ├── ws_dashboard.py   – WebSocket push to dashboard
  ├── scoring/rules.py  – Rule-based stress & risk scoring
  ├── storage/
  │   ├── sqlite_store.py – Session metadata & scores (WAL mode)
  │   └── raw_writer.py   – Per-session JSONL raw telemetry
  └── data/
      ├── live_analytics.db
      └── sessions/{session_id}/telemetry.jsonl

  Streamlit Dashboard
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

| Method | Path | Description |
|---|---|---|
| GET | `/healthz` | Health check |
| GET | `/api/sessions` | List all sessions |
| GET | `/api/sessions/{session_id}` | Session detail with latest scores |
| GET | `/api/live/latest` | Latest live state snapshot |
| WS  | `/ws/dashboard` | Real-time dashboard feed |

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `LA_HTTP_HOST` | `0.0.0.0` | FastAPI bind host |
| `LA_HTTP_PORT` | `8080` | FastAPI HTTP port |
| `LA_WS_INGEST_HOST` | `0.0.0.0` | Ingest WS bind host |
| `LA_WS_INGEST_PORT` | `8765` | Ingest WS port |
| `LA_DASHBOARD_PORT` | `8501` | Streamlit port |
| `LA_DATA_DIR` | `live_analytics/data` | Data directory |
| `LA_DB_PATH` | `live_analytics/data/live_analytics.db` | SQLite DB path |
| `LA_LOG_LEVEL` | `INFO` | Python log level |
| `LA_HR_BASELINE_BPM` | `70.0` | Resting HR for scoring |

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
