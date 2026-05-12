# Blu Sniffer — Bike VR Data Bridge & Live Analytics

Stream live bike-sensor data into a **Unity VR cycling simulator** using a Wahoo TICKR FIT heart-rate monitor (BLE) and an Arduino for speed / cadence / steering / brake signals. Data is relayed over WebSocket, logged to SQLite and JSONL files, and processed by a **real-time analytics pipeline** with a live dashboard, questionnaire system, and system health-check GUI.

---

## Architecture

```
Wahoo TICKR FIT ──BLE──┐
                        ├── bridge/bike_bridge.py ── ws://localhost:8765 ──► Unity (WahooWsClient.cs)
Arduino ────────UDP────┘                                                         │
                                                                                 │ telemetry
                                                                         WS ingest :8766
                                                                                 │
                                                               live_analytics/app/ws_ingest.py
                                                               ┌────────────────────────────┐
                                                               │  SQLite + JSONL raw files  │
                                                               │  Real-time scoring engine  │
                                                               │  /api/live/latest (REST)   │
                                                               └────────────┬───────────────┘
                                                                            │
                                                               Streamlit dashboard :8501
                                                               Questionnaire UI   :8090
                                                               System Check GUI   :8095
```

### Main components

| Component | Entry point | Port(s) |
|---|---|---|
| Analytics API (FastAPI HTTP + WS ingest) | `live_analytics/app/main.py` | **8080** (HTTP) / **8766** (WS ingest) |
| Streamlit dashboard | `live_analytics/dashboard/streamlit_app.py` | **8501** |
| Questionnaire service (FastAPI) | `live_analytics/questionnaire/app.py` | **8090** |
| System Check GUI (FastAPI) | `live_analytics/system_check/app.py` | **8095** |
| External research API | `10.200.130.98:5001` | **5001** (ekstern — puls-dual-write) |
| Wahoo BLE bridge | `bridge/bike_bridge.py` | **8765** (WS to Unity) |
| Mock bridge (no hardware) | `bridge/mock_wahoo_bridge.py` | **8765** |
| Bridge GUI monitor (Tkinter) | `bridge/wahoo_bridge_gui.py` | — |
| Master launcher | `starters/launcher.py` | — |

---

## Repository structure

```
.
├── starters/                         # One-click install & launch scripts
│   ├── INSTALL.command / .bat        #   Create venv, install deps, init DB
│   ├── START_ALL.command / .bat      #   Launch all services (calls launcher.py)
│   ├── START_BRIDGE.command / .bat   #   Real BLE bridge + GUI monitor
│   ├── START_MOCK_BRIDGE.command / .bat  # Simulated bridge (no hardware)
│   ├── START_GUI.command / .bat      #   Standalone bridge GUI monitor
│   ├── launcher.py                   #   Master orchestrator
│   └── preflight.py                  #   Pre-start environment validation
│
├── live_analytics/
│   ├── app/                          # FastAPI analytics server
│   │   ├── main.py                   #   Entry point: starts HTTP (:8080) + WS ingest (:8766)
│   │   ├── config.py                 #   Configuration via env vars (LA_*)
│   │   ├── env_utils.py              #   int_env / float_env helpers
│   │   ├── api_sessions.py           #   /healthz, /api/sessions, /api/live/latest
│   │   │                             #   PUT /api/sessions/{id}/participant  ← NY
│   │   ├── api_pulse_session.py      #   /api/pulse-session/* – dedikeret puls-session API  ← NY
│   │   ├── pulse_session_logger.py   #   PulseSessionLogger – logs/pulse/<id>_<ts>_pulse_log.jsonl  ← NY
│   │   ├── ws_ingest.py              #   WebSocket ingest server (:8766)
│   │   │                             #   Auto-resolver: henter participant_id ved ny session ← NY
│   │   ├── ws_dashboard.py           #   WebSocket dashboard feed (/ws/dashboard)
│   │   ├── models/                   #   Pydantic v2 data models
│   │   ├── scoring/                  #   Real-time scoring (features.py, rules.py, anomaly.py)
│   │   └── storage/
│   │       ├── sqlite_store.py       #   Session metadata & scores (WAL mode)
│   │       │                         #   sessions.participant_id kolonne  ← NY
│   │       │                         #   set_session_participant()  ← NY
│   │       ├── raw_writer.py         #   Per-session JSONL raw telemetry
│   │       ├── participant_logs.py   #   Per-deltager log-mappe + pulse.jsonl  ← NY
│   │       │                         #   append_pulse_session_marker() — SESSION_START/END  ← NY
│   │       └── web_api_client.py     #   Udgående HTTP-kald (puls → QS + ekstern DB)
│   │                                 #   resolve_participant() + _participant_cache  ← NY
│   ├── dashboard/
│   │   └── streamlit_app.py          # Streamlit dashboard (:8501)
│   ├── questionnaire/
│   │   ├── app.py                    #   FastAPI questionnaire service (:8090)
│   │   │                             #   GET /api/participants/by-session/{id}  ← NY
│   │   │                             #   GET /api/participants/oldest-unlinked   ← NY (FIFO)
│   │   ├── config.py                 #   Configuration via env vars (QS_*)
│   │   │                             #   ANALYTICS_API_URL  ← NY
│   │   ├── db.py                     #   SQLite CRUD
│   │   │                             #   get_oldest_unlinked_participant() ORDER BY ASC  ← NY (FIFO fix)
│   │   │                             #   create_participant() FIFO guard  ← NY
│   │   ├── questions.py              #   Pre/post question definitions
│   │   ├── models.py                 #   Pydantic models
│   │   │                             #   ParticipantCreate: integer-only validator  ← NY
│   │   └── static/                   #   SPA web UI (served at /)
│   │       └── index.html            #   Testperson-ID input: type=number, integers only  ← NY│   ├── system_check/
│   │   ├── app.py                    #   FastAPI system-check GUI (:8095)
│   │   ├── __init__.py               #   Configuration via env vars (SC_*)
│   │   ├── checks.py                 #   All health-check implementations
│   │   ├── run_checks.py             #   CLI entry point (python -m live_analytics.system_check)
│   │   └── static/                   #   SPA web UI (served at /)
│   ├── scripts/
│   │   ├── init_db.py                #   Initialise SQLite databases
│   │   ├── simulate_ride.py          #   Stream fake telemetry to WS ingest for testing
│   │   ├── backfill_from_jsonl.py    #   Re-import raw JSONL into the DB
│   │   ├── run_server.bat / .ps1     #   Windows helpers to start analytics API
│   │   └── run_dashboard.bat / .ps1  #   Windows helpers to start dashboard
│   ├── data/                         #   Runtime data (auto-created)
│   │   ├── live_analytics.db         #     SQLite analytics database (WAL mode)
│   │   ├── sessions/                 #     Per-session JSONL raw-event files
│   │   └── participants/             #     Per-deltager logmapper (pulse.jsonl, session.jsonl)
│   │       └── <participant_id>/     #     Oprettes automatisk ved registrering i questionnaire
│   │           ├── info.json         #       Deltager-metadata (id, navn, created_at)
│   │           ├── pulse.jsonl       #       Alle HR-samples + SESSION_START/END markører
│   │           └── session.jsonl     #       Session start/slut events
│   └── tests/                        # pytest – analytics pipeline
├── bridge/                           # BLE bridge & data tools
│   ├── bike_bridge.py                #   WebSocket bridge (Wahoo HR + Arduino → Unity)
│   ├── mock_wahoo_bridge.py          #   Mock server (no hardware)
│   ├── wahoo_bridge_gui.py           #   Tkinter live monitor
│   ├── collector_tail.py             #   VRSF binary collector → SQLite / Parquet
│   ├── populate_test_data.py         #   Seed test data into the collector DB
│   └── db/                           #   DB utilities (views, export, validation)
│
├── unity/                            # Unity C# scripts
│   ├── WahooWsClient.cs              #   WebSocket client (bridge consumer)
│   ├── BikeMovementController.cs     #   Translates sensor data to bike movement
│   ├── SpawnZoneTrigger.cs           #   Sends timestamped events on collider hit
│   ├── DBSender.cs                   #   Pulse logger → CARLogs/pulse.txt  ← NY
│   │                                 #   Line 1: participant_id (int, fetched from API)
│   │                                 #   Remaining lines: unix_ms|bpm at 1 Hz
│   │                                 #   Polls GET /api/sessions/{id} every 5 s until resolved
│   ├── VrsLogging/                   #   VRSF binary session logging
│   └── LiveAnalytics/                #   Telemetry publisher scripts
│       ├── TelemetryPublisher.cs     #   SessionId property (unix-ms string) used by DBSender
│       ├── TelemetryBuffer.cs
│       ├── TelemetryConfig.cs
│       ├── TelemetryModels.cs
│       └── LiveFeedbackClient.cs
│
├── tests/                            # pytest – bridge, collector, parser, VRSF
│   └── mock_dbsender/                # Standalone C# mock run for DBSender logic  ← NY
│       ├── Program.cs                #   14 tests: file format, header rewrite, JSON extraction
│       └── mock_dbsender.csproj
├── logs/                             # Service log files (auto-created by launcher)
│   ├── *.log                         #   Rotated service stdout/stderr (analytics, questionnaire, …)
│   └── pulse/                        #   Dedikeret puls-log pr. testperson/session  ← NY
│       └── <id>_<YYYYMMDD_HHMMSSffffff>_pulse_log.jsonl
│                                     #   session_start | pulse | session_end records
├── analysis/                         # Offline analysis notebooks & scripts
├── docs/                             # Additional documentation
├── scripts/                          # Shell helpers (BLE capture, port check)
├── pyproject.toml                    # Build config, project metadata, pytest paths
└── requirements.txt                  # pip dependencies
```

---

## Requirements

| Requirement | Version |
|---|---|
| Python | **≥ 3.11** |
| OS | macOS, Windows 10/11, Linux |
| BLE (optional) | Bluetooth adapter + OS BLE permissions |
| ADB (optional) | Android SDK Platform Tools (for Quest headset check) |

Python package dependencies are declared in both `requirements.txt` and `pyproject.toml`. Key packages:

```
bleak>=0.21.0         # BLE (Wahoo bridge only)
websockets>=12.0
fastapi>=0.104,<1
uvicorn[standard]>=0.24,<1
pydantic>=2,<3
streamlit>=1.33,<2
pandas>=2.0,<3
numpy>=1.26,<2
requests>=2.31,<3
aiofiles>=23.0
sqlalchemy>=2.0
httpx>=0.27,<1        # HTTP client (preflight health checks)
pyarrow>=10.0.0       # Parquet output (collector tests)
pytest>=7.0
pytest-asyncio>=0.21.0
pytest-cov>=4.0
```

---

## Installation

### One-click (recommended)

| Platform | Script |
|---|---|
| macOS | Double-click `starters/INSTALL.command` |
| Windows | Double-click `starters/INSTALL.bat` |

The install script:
1. Checks Python ≥ 3.11 is available
2. Creates `.venv` in the repository root
3. Installs all dependencies (`pip install -r requirements.txt && pip install -e .`)
4. Runs `starters/preflight.py` to verify the environment
5. Runs `live_analytics/scripts/init_db.py` to create the SQLite databases
6. Makes all `.command` / `.sh` scripts executable (macOS)

### Manual (any platform)

```bash
# 1. Clone
git clone https://github.com/jonasbuc/wahoo-ble-sniffer.git
cd wahoo-ble-sniffer

# 2. Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate          # macOS / Linux
# .venv\Scripts\activate           # Windows

# 3. Install dependencies
pip install -r requirements.txt
pip install -e .

# 4. Verify the environment
python starters/preflight.py

# 5. Initialise databases
python live_analytics/scripts/init_db.py
```

After step 5 the following files are created automatically:
- `live_analytics/data/live_analytics.db` — analytics SQLite database
- `live_analytics/data/sessions/` — directory for per-session JSONL files
- `live_analytics/questionnaire/data/questionnaire.db` — questionnaire SQLite database

---

## Running the project

### Start everything (one-click)

| Platform | Script |
|---|---|
| macOS | Double-click `starters/START_ALL.command` |
| Windows | Double-click `starters/START_ALL.bat` |

Both scripts delegate to `starters/launcher.py` via the `.venv` Python interpreter.

### Start from the terminal

```bash
# All services (no BLE bridge)
python starters/launcher.py

# All services + real Wahoo BLE bridge
python starters/launcher.py --bridge

# All services + mock bridge (no hardware required)
python starters/launcher.py --bridge --mock

# Skip the Streamlit dashboard
python starters/launcher.py --no-dashboard
```

The launcher starts services in this order:
1. Analytics API (`:8080` HTTP + `:8766` WS ingest)
2. Questionnaire API (`:8090`)
3. System Check GUI (`:8095`)
4. Streamlit Dashboard (`:8501`)
5. *(optional)* Wahoo BLE Bridge or Mock Bridge (`:8765`)

It waits for each service to respond on its port before marking it as `running`, then prints a live status panel:

```
  ================================================
       Bike VR - Master Launcher
  ================================================

  *  Analytics API          :8080   running
  *  Questionnaire API      :8090   running
  *  System Check GUI       :8095   running
  *  Dashboard              :8501   running

  * All 4 services running!  (4s)

  URLs:
    System Check  -> http://127.0.0.1:8095
    Dashboard     -> http://127.0.0.1:8501
    Analytics API -> http://127.0.0.1:8080
    Questionnaire -> http://127.0.0.1:8090

  Press Ctrl+C to stop all services
```

Press **Ctrl+C** to shut everything down.

### Start the BLE bridge separately

```bash
# Real hardware (Wahoo TICKR FIT via BLE)
python bridge/bike_bridge.py

# Simulated data (no hardware needed)
python bridge/mock_wahoo_bridge.py

# GUI monitor only (bridge must already be running)
python bridge/wahoo_bridge_gui.py --url ws://localhost:8765
```

One-click bridge starters:

| Platform | Real hardware | Simulated |
|---|---|---|
| macOS | `starters/START_BRIDGE.command` | `starters/START_MOCK_BRIDGE.command` |
| Windows | `starters/START_BRIDGE.bat` | `starters/START_MOCK_BRIDGE.bat` |

The `.command` / `.bat` bridge starters automatically open the GUI monitor in a separate terminal window once the bridge port is open.

### Start services individually (advanced)

```bash
# Analytics API + WS ingest server (both start in the same process)
python -m live_analytics.app.main

# Questionnaire service
python -m live_analytics.questionnaire.app

# System Check GUI
python -m live_analytics.system_check.app

# Streamlit dashboard
streamlit run live_analytics/dashboard/streamlit_app.py \
  --server.port 8501 --server.headless true
```

### Simulate a ride (no Unity, no hardware)

With the analytics API running:

```bash
python live_analytics/scripts/simulate_ride.py --duration 60 --hz 20
```

Streams fake telemetry directly to the WS ingest port (`:8766`) and populates the dashboard live.

---

## Configuration

All services are configured via **environment variables**. Every variable has a sensible default so the system runs out of the box without any configuration file.

### Analytics API (`live_analytics/app/config.py`)

| Variable | Default | Description |
|---|---|---|
| `LA_BASE_DIR` | `live_analytics/` directory | Base path |
| `LA_DATA_DIR` | `<LA_BASE_DIR>/data` | Data directory |
| `LA_DB_PATH` | `<LA_DATA_DIR>/live_analytics.db` | SQLite database path |
| `LA_SESSIONS_DIR` | `<LA_DATA_DIR>/sessions` | Per-session JSONL directory |
| `LA_PARTICIPANTS_DIR` | `<LA_DATA_DIR>/participants` | Per-deltager logmapper |
| `LA_PULSE_LOG_DIR` | `<LA_BASE_DIR>/logs/pulse` | Dedikeret puls-log dir (PulseSessionLogger) |
| `LA_HTTP_HOST` | `0.0.0.0` | API bind address |
| `LA_HTTP_PORT` | `8080` | API HTTP port |
| `LA_WS_INGEST_HOST` | `0.0.0.0` | WS ingest bind address |
| `LA_WS_INGEST_PORT` | `8766` | WS ingest port |
| `LA_SCORING_WINDOW_SEC` | `5.0` | Sliding window size for live scoring |
| `LA_HR_BASELINE_BPM` | `70.0` | Resting HR baseline for scoring |
| `LA_LOG_LEVEL` | `INFO` | Logging level |

### Udgående HTTP-kald / ekstern DB (`live_analytics/app/storage/web_api_client.py`)

| Variable | Default | Description |
|---|---|---|
| `QS_BASE_URL` | `http://localhost:8090` | Base URL til lokalt questionnaire-service |
| `EXTERNAL_API_URL` | `https://10.200.130.98:5001` | Ekstern forsknings-API (self-signed TLS) |
| `EXTERNAL_USER_ID` | `0` | Fallback `UserId` (TestPersonNumber) til ekstern DB — bruges kun hvis questionnaire ikke har linket en deltager til sessionen endnu |

### Streamlit dashboard (`live_analytics/dashboard/streamlit_app.py`)

| Variable | Default | Description |
|---|---|---|
| `LA_API_BASE` | `http://127.0.0.1:8080` | Analytics API URL (also `--api` CLI flag) |
| `LA_DASH_REFRESH_SEC` | `5` | Auto-refresh interval in seconds (also `--refresh` CLI flag) |
| `LA_DATA_DIR` | `live_analytics/data` | Path to local data files |
| `LA_DASH_MAX_CHART_ROWS` | `600` | Maximum rows kept in chart history |

### Questionnaire service (`live_analytics/questionnaire/config.py`)

| Variable | Default | Description |
|---|---|---|
| `QS_BASE_DIR` | `live_analytics/questionnaire/` | Base path |
| `QS_DATA_DIR` | `<QS_BASE_DIR>/data` | Data directory |
| `QS_DB_PATH` | `<QS_DATA_DIR>/questionnaire.db` | SQLite database path |
| `QS_HOST` | `0.0.0.0` | Bind address |
| `QS_PORT` | `8090` | Port |
| `QS_LOG_LEVEL` | `INFO` | Logging level |

### System Check GUI (`live_analytics/system_check/__init__.py`)

| Variable | Default | Description |
|---|---|---|
| `SC_HOST` | `0.0.0.0` | Bind address |
| `SC_PORT` | `8095` | Port |
| `SC_ANALYTICS_DB` | `live_analytics/data/live_analytics.db` | Analytics DB to probe |
| `SC_QUESTIONNAIRE_DB` | `live_analytics/questionnaire/data/questionnaire.db` | Questionnaire DB to probe |
| `SC_BRIDGE_WS_URL` | `ws://127.0.0.1:8765` | Bridge WebSocket URL to probe |
| `SC_ANALYTICS_API_URL` | `http://127.0.0.1:8080` | Analytics API URL to probe |
| `SC_QUESTIONNAIRE_API_URL` | `http://127.0.0.1:8090` | Questionnaire API URL to probe |
| `SC_VRS_LOG_BASE` | `<repo_root>/Logs` | Directory Unity writes VRSF session logs to |
| `SC_LOG_LEVEL` | `INFO` | Logging level |

---

## Runtime data folders

| Path | Created by | Description |
|---|---|---|
| `live_analytics/data/` | `init_db.py` / `ensure_dirs()` at startup | Analytics data root |
| `live_analytics/data/live_analytics.db` | `init_db.py` / first startup | SQLite analytics DB (WAL mode) |
| `live_analytics/data/sessions/` | `ensure_dirs()` at startup | Per-session `<session_id>.jsonl` raw event files |
| `live_analytics/data/participants/` | questionnaire API / `create_participant_log_dir()` | Per-deltager logmapper (auto-oprettet ved registrering) |
| `live_analytics/questionnaire/data/` | `ensure_dirs()` at startup | Questionnaire data root |
| `live_analytics/questionnaire/data/questionnaire.db` | `init_db.py` / first startup | SQLite questionnaire DB |
| `logs/` | Launcher on first run | Service stdout/stderr log files (rotated at 2 MB, 3 backups) |
| `logs/pulse/` | `ensure_dirs()` / `init_pulse_logger()` at startup | Dedikerede puls-log JSONL-filer pr. testperson/session |

All directories are created automatically on first startup. A pre-existing `live_analytics/data/.gitkeep` keeps the `data/` directory tracked by git before the database is created.

---

## Service interaction

```
Unity (TelemetryPublisher.cs)
        │  JSON batches  ws://localhost:8766
        ▼
live_analytics/app/ws_ingest.py
  • upserts session into SQLite
  • appends raw records to per-session JSONL file
  • maintains in-memory sliding window (default 5 s)
  • calls compute_scores() → stores in latest_scores dict
  • broadcasts score update to /ws/dashboard subscribers
  • [NY] ved ny session: henter participant_id fra questionnaire API
    og gemmer det på sessionen i SQLite (asynkron baggrundsopgave)
        │
        ├── GET /api/live/latest  ◄── Streamlit dashboard (polls every REFRESH_SEC)
        ├── GET /api/sessions     ◄── Streamlit dashboard
        └── WS  /ws/dashboard     ◄── Streamlit dashboard (push updates)

live_analytics/app/storage/web_api_client.py  [NY dual-write + participant-resolver]
  • send_pulse() sender puls til to destinationer simultaneously:
      1. POST :8090/api/pulse  →  questionnaire.db  (rig schema med session_id etc.)
      2. POST 10.200.130.98:5001/api/cardatasqlite/loglitepd
             →  ekstern SQLite PulseData { UserId=TestPersonNumber, Pulse }
  • resolve_participant(session_id): slår deltager op via questionnaire API og cacher
    resultatet — bruges som UserId i ekstern DB (fallback: EXTERNAL_USER_ID env var)
  • Fejl i én destination blokerer aldrig den anden

live_analytics/app/storage/participant_logs.py  [NY per-deltager puls-log]
  • Opretter live_analytics/data/participants/<participant_id>/ ved registrering
  • pulse.jsonl: ALLE HR-samples (heart_rate > 0) skrives per batch — ikke kun den
    sidst kendte. Records med heart_rate = 0 (headpose/relay) springes over.
  • SESSION_START-markør skrives til pulse.jsonl, når participant resolver
  • SESSION_END-markør skrives til pulse.jsonl, når Unity disconnecter
  • session.jsonl: session_start / session_end events (adskilt fra pulse-data)

live_analytics/app/pulse_session_logger.py  [NY dedikeret puls-log pr. session]
  • PulseSessionLogger-klassen med start_session(), write_pulse(), close_session()
  • Skriver til logs/pulse/<participant_id>_<YYYYMMDD_HHMMSSffffff>_pulse_log.jsonl
  • Én fil pr. testperson pr. session — adskilt fra participants/-mappen
  • session_start → pulse (alle samples) → session_end — rent JSONL-format
  • Auto-lukker gammel session hvis en ny starter for samme deltager
  • Eksponeret via HTTP API: POST /api/pulse-session/start|end, GET /current

Questionnaire service (:8090)
  • standalone FastAPI process med eget SQLite-DB
  • REST API + static SPA served from live_analytics/questionnaire/static/
  • participants-tabel: participant_id (TestPersonNumber), session_id, svar, puls
  • [NY] GET /api/participants/by-session/{session_id}: opslag fra analytics → QS

Operatør-workflow for at koble en testperson til en session:
  1. POST /api/participants  →  opret testperson (f.eks. participant_id="7")
     • Testperson-ID er et positivt heltal — valideret i UI, Pydantic-model og DB
     • Gentagen indsendelse af et allerede-linket ID (operator-fejl) opdaterer kun
       kosmetiske felter; session_id berøres IKKE (FIFO guard i db.py)
  2. PUT /api/participants/7/session  { "session_id": "..." }  (questionnaire API)
     ELLER
     PUT /api/sessions/.../participant  { "participant_id": "7" }  (analytics API)
  Herefter bruges TestPersonNumber=7 automatisk som UserId i alle eksterne DB-skrivninger.

Unity DBSender.cs  [NY puls-log med deltager-ID]
  • Skriver CARLogs/pulse.txt ved session-start (én fil pr. kørsel)
  • Linje 1: participant_id (heltal) — hentes fra GET /api/sessions/{session_id}
    og polles hvert 5/10/30 s, indtil questionnaire har linket en deltager
  • Øvrige linjer: unix_ms|bpm (1 Hz)
  • session_id læses fra TelemetryPublisher.SessionId (assign i Inspector)
  • Hvis participant aldrig resolver: linje 1 forbliver "PENDING"
  • Se docs/DBSENDER.md for import-script til pulse_data-tabellen

System Check GUI (:8095)
  • probes all other services (HTTP health + WebSocket TCP check)
  • checks SQLite database integrity
  • checks Quest headset via ADB
  • checks VRSF session log files
  • REST API + static SPA served from live_analytics/system_check/static/

Wahoo BLE Bridge (:8765)
  • reads from Wahoo TICKR FIT via BLE (bleak) and Arduino via UDP
  • forwards combined frames to Unity via WebSocket
  • optional GUI monitor: bridge/wahoo_bridge_gui.py
```

---

## REST API reference

All HTTP endpoints are served on port **8080**.

### Analytics API

| Method | Path | Description |
|---|---|---|
| `GET` | `/healthz` | API status + SQLite DB reachability |
| `GET` | `/api/sessions` | List all sessions (summary) |
| `GET` | `/api/sessions/{session_id}` | Session detail + latest scores |
| `PUT` | `/api/sessions/{session_id}/participant` | Kobl deltager til session — body: `{ "participant_id": "P001" }` |
| `POST` | `/api/sessions/trigger-relink` | Re-kør participant resolution for alle aktive sessions uden deltager (kaldt automatisk ved ny deltager-registrering) |
| `GET` | `/api/live/latest` | Latest live telemetry across all active sessions |
| `WS` | `/ws/dashboard` | Push live score updates to dashboard clients |

**Pulse Session API** (dedikeret puls-log pr. testperson):

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/pulse-session/start` | Åbn ny puls-log-fil — body: `{ "test_person_id": "TP_001" }` |
| `POST` | `/api/pulse-session/end` | Luk aktiv puls-log-fil — body: `{ "test_person_id": "TP_001" }` — 404 hvis ingen aktiv session |
| `GET` | `/api/pulse-session/current` | Alle aktive puls-sessions (alle deltagere) |
| `GET` | `/api/pulse-session/current/{test_person_id}` | Aktiv session for én deltager — 404 hvis ingen aktiv |

WebSocket ingest (port **8766**, separate `websockets` server):

| Protocol | URL | Description |
|---|---|---|
| WebSocket | `ws://localhost:8766` | Unity telemetry ingest (JSON `TelemetryBatch` messages) |

### Questionnaire API (port 8090)

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/participants` | Opret testperson — `participant_id` **skal** være et positivt heltal (422 ellers) |
| `GET` | `/api/participants` | Alle testpersoner |
| `GET` | `/api/participants/{participant_id}` | Hent enkelt testperson |
| `GET` | `/api/participants/by-session/{session_id}` | Hent testperson via analytics-session ID |
| `GET` | `/api/participants/oldest-unlinked` | Hent ældste deltager uden session (FIFO — bruges af analytics auto-linker) |
| `PUT` | `/api/participants/{participant_id}/session` | Kobl analytics session til testperson |
| `DELETE` | `/api/participants/{participant_id}` | Slet testperson og alle svar |
| `POST` | `/api/participants/{participant_id}/answers/{phase}` | Gem enkelt svar (pre/post) |
| `PUT` | `/api/participants/{participant_id}/answers/{phase}` | Gem alle svar i bulk (pre/post) |
| `GET` | `/api/participants/{participant_id}/answers/{phase}` | Hent alle svar |
| `GET` | `/api/participants/{participant_id}/progress` | Besvarelsesprogress |
| `POST` | `/api/pulse` | Modtag puls-sample (fra ws_ingest) |
| `GET` | `/api/pulse/{session_id}` | Hent puls-data for en session |

---

## System Check

The System Check GUI at **http://127.0.0.1:8095** verifies:

- Meta Quest 3 headset connected via ADB
- Analytics SQLite database readable
- Questionnaire SQLite database readable
- Wahoo BLE bridge WebSocket reachable (`ws://localhost:8765`)
- Analytics API HTTP health
- Questionnaire API HTTP health
- VRSF session log files present and parseable
- Individual session integrity by ID

**CLI:**

```bash
# Run all checks
python -m live_analytics.system_check

# Run a single named check
python -m live_analytics.system_check --check bridge

# Verify a specific session ID
python -m live_analytics.system_check --session SIM_1234567890

# JSON output
python -m live_analytics.system_check --json
```

---

## Testing

```bash
# Run all 1140 tests
pytest

# Quiet output
pytest -q

# With short tracebacks
pytest --tb=short -q

# Bridge & collector tests only
pytest tests/

# Analytics pipeline tests only
pytest live_analytics/tests/

# Single file
pytest live_analytics/tests/test_features.py -v
```

Test coverage:
- **`tests/`** — BLE parsing, VRSF binary format, collector DB, Parquet export, mock integration, end-to-end flows, disconnections, GUI
- **`live_analytics/tests/`** — analytics API endpoints, WS ingest, scoring pipeline, SQLite store, raw writer, participant logs (pulse.jsonl SESSION_START/END markers, alle HR-samples), `PulseSessionLogger` (31 tests: lifecycle, edge cases, multi-participant, API endpoints), configuration, crash diagnostics, fresh-clone bootstrap, regression tests
- **`live_analytics/questionnaire/tests/`** — questionnaire API endpoints, DB CRUD, error handling
  - integer-only `participant_id` validation (422 for non-integers, `"007"` → `"7"`)
  - FIFO guard: re-registering a linked ID must not clear `session_id`
- **`live_analytics/system_check/tests/`** — system check probes, app endpoints, VRSF log inspection
- **`tests/mock_dbsender/`** — standalone C# mock run (14 tests, no Unity required):
  - `dotnet run` from `tests/mock_dbsender/` — exercises file creation, PENDING header,
    pulse line format (`unix_ms|bpm`), participant header rewrite, post-resolve format

Coverage target: **≥ 88 %** (measured with `pytest --cov`). Run with:

```bash
pytest --cov=live_analytics --cov=bridge --cov-report=term-missing -q
```

---

## Fresh GitHub clone — complete walkthrough

```bash
# 1. Clone
git clone https://github.com/jonasbuc/wahoo-ble-sniffer.git
cd wahoo-ble-sniffer

# 2. Create virtual environment
python3 -m venv .venv
source .venv/bin/activate        # macOS / Linux
# .venv\Scripts\activate         # Windows

# 3. Install dependencies
pip install -r requirements.txt
pip install -e .

# 4. Verify the environment
python starters/preflight.py
# Expected: all 5 checks pass (Python ≥ 3.11, venv active, all imports OK, ...)

# 5. Initialise databases
python live_analytics/scripts/init_db.py
# Creates: live_analytics/data/live_analytics.db
#          live_analytics/questionnaire/data/questionnaire.db

# 6. Start all services
python starters/launcher.py
# macOS one-click: double-click starters/START_ALL.command

# 7. Confirm services are up
curl http://127.0.0.1:8080/healthz
# Expected: {"status":"ok","db_ok":true,...}

# 8. Open in browser
#   Dashboard:      http://127.0.0.1:8501
#   Questionnaire:  http://127.0.0.1:8090
#   System Check:   http://127.0.0.1:8095

# 9. (Optional) Stream simulated telemetry
python live_analytics/scripts/simulate_ride.py --duration 60 --hz 20
```

Nothing outside the repository is required. No Docker, no cloud services, no pre-existing database files.

---

## Troubleshooting

### `ModuleNotFoundError` on startup

The virtual environment is not activated or dependencies are not installed:

```bash
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

### Launcher exits: "Virtual environment not found"

Run the full installer first:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
python live_analytics/scripts/init_db.py
```

### `/healthz` returns `"db_ok": false`

The database has not been initialised. Run:

```bash
python live_analytics/scripts/init_db.py
```

### Dashboard shows "API unavailable" or empty session list

1. Confirm the API is running: `curl http://127.0.0.1:8080/healthz`
2. Check `LA_API_BASE` or the `--api` flag points to the correct address
3. Start the analytics API **before** starting the dashboard

### Port already in use

```bash
# macOS / Linux — find and kill the process on a port
lsof -ti :8080 | xargs kill -9

# Windows
netstat -ano | findstr :8080
taskkill /PID <pid> /F
```

### Wahoo TICKR FIT not found

- Wear the TICKR FIT — wet contacts activate it
- Close Wahoo Fitness / Zwift (they may hold the BLE connection)
- macOS: grant Bluetooth permission (System Settings → Privacy & Security → Bluetooth)
- Run `python bridge/ble_test_connect.py` for a standalone BLE scan
- See [`docs/PAIRING_HELP.md`](docs/PAIRING_HELP.md) for full pairing guidance

### Frequent BLE disconnections

- Move closer to the machine
- Check battery level on the TICKR FIT
- Unpair the device from other phones or watches before use

### Unity cannot connect to bridge

The bridge must be running **before** Unity connects. Start `python bridge/bike_bridge.py` (or `START_BRIDGE.command`), then press Play in Unity.

### System Check reports "ADB not found"

Install Android SDK Platform Tools and put `adb` on your PATH:

```bash
brew install --cask android-platform-tools  # macOS
```

### Reading service logs

The launcher writes each service's stdout/stderr to `logs/<service>.log`. Logs rotate at 2 MB (3 backups: `.log.1`, `.log.2`, `.log.3`):

```bash
tail -100 logs/analytics.log
tail -100 logs/questionnaire.log
tail -100 logs/system_check.log
tail -100 logs/dashboard.log
```

---

## Platform notes

### macOS

- Grant Bluetooth permission when prompted (System Settings → Privacy & Security → Bluetooth)
- Close Wahoo Fitness / Zwift before running the bridge
- All `.command` files must be executable — the installer runs `chmod +x starters/*.command` automatically

### Windows

- Run `starters\INSTALL.bat` first to create the venv and install everything
- Use the `.bat` starters to launch services
- Native Windows 10/11 BLE works without additional drivers
- All scripts are compatible with `cmd.exe` and use `chcp 65001` for UTF-8 output

#### Installing from a GitHub ZIP on Windows (clean machine)

If you download the project as a ZIP instead of cloning with Git, follow
these exact steps on the Windows machine:

1. Go to the GitHub repository page → **Code** → **Download ZIP**
2. Extract the ZIP to a folder of your choice (e.g. `C:\BikeVR\`)
   - The extracted folder will be named `wahoo-ble-sniffer-main` — you can
     rename it to anything; the project uses relative paths internally.
3. Install **Python 3.11 or newer** from <https://www.python.org/downloads/>
   - During install, check **"Add Python to PATH"**
4. Double-click `starters\INSTALL.bat` inside the extracted folder.
   - This creates `.venv\`, installs all dependencies, and initialises the DB.
   - Each step is checked independently — if `pip install` fails you will see
     the exact error before the installer stops.
5. Double-click `starters\START_ALL.bat` to launch all services.

**Troubleshooting Windows-specific issues:**

| Symptom | Cause | Fix |
|---|---|---|
| `Python not found` in INSTALL.bat | Python not on PATH | Re-install Python, check "Add to PATH" |
| Unicode boxes (ÔùÜ) in console | Console not UTF-8 | Run `chcp 65001` before the script, or use Windows Terminal |
| Service Check shows "Unreachable" for running services | IPv6 `::1` vs IPv4 `127.0.0.1` mismatch | All service URLs now use `127.0.0.1` — should not occur after this fix |
| `bleak` / BLE not working | Missing WinRT runtime | Requires Windows 10 1903+ (build 18362+) |
| Streamlit page blank | Backend not ready yet | Wait ~10 s; the launcher waits for Analytics API before starting Streamlit |

### Linux

- Install `bluez` and grant BLE capabilities:
  ```bash
  sudo setcap cap_net_raw+eip $(readlink -f $(which python3))
  ```

---

## Data storage

| Store | Location | Written by |
|---|---|---|
| Analytics SQLite (WAL) | `live_analytics/data/live_analytics.db` | `init_db.py` / WS ingest |
| — sessions.participant_id | kolonne i ovenstående DB | `ws_ingest` (auto-resolve) / `PUT /api/sessions/{id}/participant` |
| Per-session raw JSONL | `live_analytics/data/sessions/<session_id>.jsonl` | WS ingest (first event) |
| Per-deltager pulse log | `live_analytics/data/participants/<id>/pulse.jsonl` | `ws_ingest` → `participant_logs.append_pulse()` — **alle** HR-samples med SESSION_START/END markører |
| Per-deltager session log | `live_analytics/data/participants/<id>/session.jsonl` | `ws_ingest` → `participant_logs.append_session_event()` |
| Per-deltager info | `live_analytics/data/participants/<id>/info.json` | `questionnaire/app.py` ved oprettelse |
| **Dedikeret puls-log** | `logs/pulse/<id>_<YYYYMMDD_HHMMSSffffff>_pulse_log.jsonl` | `PulseSessionLogger` — én fil pr. session, JSONL med `session_start` / `pulse` / `session_end` records |
| Questionnaire SQLite | `live_analytics/questionnaire/data/questionnaire.db` | `init_db.py` / questionnaire API |
| — pulse_data tabel | del af questionnaire.db | `web_api_client.send_pulse()` via `/api/pulse` endpoint |
| Ekstern SQLite (PulseData) | `10.200.130.98:5001` (ekstern server) | `web_api_client.send_pulse()` dual-write |
| VRSF binary sessions | `Logs/` (Unity-controlled path) | Unity `VrsSessionLogger.cs` |
| Collector SQLite / Parquet | `collector_out/` | `bridge/collector_tail.py` |

> **Puls-flow:** puls skrives til **fire** destinationer:
> 1. `participants/<id>/pulse.jsonl` — lokalt filsystem, alle samples, SESSION_START/END markører
> 2. `logs/pulse/<id>_<ts>_pulse_log.jsonl` — dedikeret fil pr. session via `PulseSessionLogger` (session_start / pulse / session_end JSONL records)
> 3. `questionnaire.db` via questionnaire API (én sample per batch)
> 4. Ekstern forsknings-DB via `web_api_client` (én sample per batch)
>
> `live_analytics.db`'s `sessions`-tabel gemmer kun `participant_id` som et fremmednøgle-link.

The analytics database is opened in **WAL mode** with a thread-safe connection pool, allowing concurrent reads from the dashboard while the ingest server is writing.

---

## Unity integration

See [`unity/LiveAnalytics/`](unity/LiveAnalytics/) for the C# telemetry publisher.

| Script | Location | Purpose |
|---|---|---|
| `WahooWsClient.cs` | `unity/` | WebSocket client for the BLE bridge (`:8765`) |
| `BikeMovementController.cs` | `unity/` | Translates sensor data to in-game bike movement |
| `SpawnZoneTrigger.cs` | `unity/` | Sends timestamped trigger events on collider hit |
| `TelemetryPublisher.cs` | `unity/LiveAnalytics/` | Batches + sends telemetry to ingest server (`:8766`); sends `start_session` / `end_session` signals to `PulseSessionLogger` |
| `TelemetryBuffer.cs` | `unity/LiveAnalytics/` | In-memory batch buffer |
| `TelemetryConfig.cs` | `unity/LiveAnalytics/` | Ingest server URL and batch settings |
| `TelemetryModels.cs` | `unity/LiveAnalytics/` | Serialisation models |
| `LiveFeedbackClient.cs` | `unity/LiveAnalytics/` | Receives live scores from `/api/live/latest` |
| `VrsSessionLogger.cs` | `unity/VrsLogging/` | Binary VRSF session logging |

The ingest endpoint (`ws://127.0.0.1:8766`) expects JSON messages matching the `TelemetryBatch` Pydantic model defined in `live_analytics/app/models/`.

Full setup guide: [`docs/QUICKSTART.md`](docs/QUICKSTART.md)

---

## How to implement — complete integration guide

This section walks through every step required to wire the full system together: from a blank machine to a running VR cycling session with live pulse logging, scoring, questionnaire, and dashboard.

---

### Step 1 — Install the project

**macOS (one-click):**
```bash
# Double-click in Finder, or run from terminal:
open starters/INSTALL.command
```

**Windows (one-click):**
Double-click `starters\INSTALL.bat`.

**Manual (any platform):**
```bash
git clone https://github.com/jonasbuc/wahoo-ble-sniffer.git
cd wahoo-ble-sniffer
python3 -m venv .venv
source .venv/bin/activate          # macOS / Linux
# .venv\Scripts\activate           # Windows
pip install -r requirements.txt
pip install -e .
python live_analytics/scripts/init_db.py
```

After this you will have:
- `.venv/` — Python virtual environment with all dependencies
- `live_analytics/data/live_analytics.db` — analytics SQLite DB
- `live_analytics/questionnaire/data/questionnaire.db` — questionnaire SQLite DB

---

### Step 2 — Verify the environment

```bash
python starters/preflight.py
```

Expected output: all checks green. If any check fails, fix it before proceeding (the output tells you exactly what is missing).

---

### Step 3 — Start all services

**macOS:** double-click `starters/START_ALL.command`, or from terminal:
```bash
python starters/launcher.py
```

**Windows:** double-click `starters\START_ALL.bat`.

The launcher starts services in order and waits for each to respond before starting the next:

| # | Service | Port | URL |
|---|---|---|---|
| 1 | Analytics API + WS ingest | 8080 / 8766 | http://127.0.0.1:8080 |
| 2 | Questionnaire | 8090 | http://127.0.0.1:8090 |
| 3 | System Check GUI | 8095 | http://127.0.0.1:8095 |
| 4 | Dashboard | 8501 | http://127.0.0.1:8501 |

Confirm everything is up:
```bash
curl http://127.0.0.1:8080/healthz
# Expected: {"status":"ok","db_ok":true,...}
```

---

### Step 4 — Start the BLE bridge (or mock)

**Real Wahoo TICKR FIT + Arduino hardware:**
```bash
python bridge/bike_bridge.py
# macOS one-click: starters/START_BRIDGE.command
# Windows:         starters\START_BRIDGE.bat
```

**No hardware / simulated data:**
```bash
python bridge/mock_wahoo_bridge.py
# macOS one-click: starters/START_MOCK_BRIDGE.command
# Windows:         starters\START_MOCK_BRIDGE.bat
```

The bridge opens a WebSocket server on `ws://localhost:8765`. Unity must connect to this address via `WahooWsClient.cs`.

---

### Step 5 — Register a test participant (before the headset goes on)

Register the test person and complete the pre-session questionnaire **before** the headset is put on. Once Unity starts, the analytics server will automatically detect the pre-registered participant and link them to the new session — no manual linking step needed.

**Option A — Questionnaire web UI:**

Open `http://127.0.0.1:8090` in a browser and register the participant through the UI.

**Option B — API call:**

```bash
# Register participant TP_001
curl -X POST http://127.0.0.1:8090/api/participants \
  -H "Content-Type: application/json" \
  -d '{"participant_id": "TP_001", "name": "Jonas"}'
```

**How auto-linking works:**
When a new Unity session starts, the analytics server calls `GET /api/participants/oldest-unlinked` on the questionnaire service. This uses **FIFO ordering** — the first person to register gets the first running session. This prevents a newly created P2 from being accidentally linked to a session already mid-ride for P1.

Once linked, the analytics server will:
- Write `SESSION_START` to `data/participants/TP_001/pulse.jsonl`
- Open `logs/pulse/TP_001_<timestamp>_pulse_log.jsonl` via `PulseSessionLogger`
- Tag all pulse samples and score snapshots with `participant_id = "TP_001"`

---

### Step 6 — Set up Unity and start the session (put on headset)

1. Open your Unity project (Unity 2021+).
2. Copy all scripts from `unity/LiveAnalytics/` into your Unity `Assets/Scripts/LiveAnalytics/` folder:
   - `TelemetryPublisher.cs`
   - `TelemetryBuffer.cs`
   - `TelemetryConfig.cs`
   - `TelemetryModels.cs`
   - `LiveFeedbackClient.cs`
3. Copy `unity/WahooWsClient.cs` into `Assets/Scripts/`.
4. Attach `TelemetryPublisher` to any persistent `GameObject` in your scene (e.g. a `GameManager` object).
5. Create a `TelemetryConfig` ScriptableObject:
   - In the Unity menu: **Assets → Create → Live Analytics → TelemetryConfig**
   - Set `ingestUrl` to `ws://127.0.0.1:8766`
   - Set `scenarioId` to something meaningful, e.g. `"forest_01"`
   - Set `gameplayHz` = `20`, `headposeHz` = `10`, `maxBatchSize` = `10`
6. Assign the `TelemetryConfig` asset to the `config` field on `TelemetryPublisher` in the Inspector.
7. Wire sensor data into `TelemetryPublisher`'s public fields from your own scripts:

```csharp
// Example: in your bike controller Update() loop
telemetryPublisher.externalHeartRate   = wahooWsClient.HeartRate;
telemetryPublisher.externalSpeed       = bikeSpeed;           // m/s
telemetryPublisher.externalSteeringAngle = steeringAngle;    // degrees
telemetryPublisher.externalBrakeFront  = brakeFront;         // 0–255
telemetryPublisher.externalBrakeRear   = brakeRear;          // 0–255
```

8. Have the test person put on the headset, then press **Play** in Unity. `TelemetryPublisher` will:
   - Connect to `ws://127.0.0.1:8766`
   - Send a `start_session` signal after 1.5 s
   - Stream JSON `TelemetryBatch` messages at `gameplayHz`
   - Send an `end_session` signal on `OnDestroy` / `OnApplicationQuit`

---

### Step 7 — Manage pulse sessions via API (optional)

The `PulseSessionLogger` opens and closes dedicated pulse log files automatically when Unity connects/disconnects. You can also control it manually via HTTP:

```bash
# Open a pulse log file for TP_001
curl -X POST http://127.0.0.1:8080/api/pulse-session/start \
  -H "Content-Type: application/json" \
  -d '{"test_person_id": "TP_001"}'

# Check what sessions are currently logging
curl http://127.0.0.1:8080/api/pulse-session/current

# Close the log file when the session is done
curl -X POST http://127.0.0.1:8080/api/pulse-session/end \
  -H "Content-Type: application/json" \
  -d '{"test_person_id": "TP_001"}'
```

Each pulse log file looks like this:
```jsonl
{"type": "session_start", "participant_id": "TP_001", "session_id": "1746000000000", "started_at": "2025-04-30T10:00:00+00:00", "scenario_id": "forest_01"}
{"type": "pulse", "participant_id": "TP_001", "session_id": "1746000000000", "unix_ms": 1746000001000, "pulse": 82, "recorded_at": "2025-04-30T10:00:01+00:00"}
{"type": "pulse", "participant_id": "TP_001", "session_id": "1746000000000", "unix_ms": 1746000001050, "pulse": 83, "recorded_at": "2025-04-30T10:00:01+00:00"}
{"type": "session_end", "participant_id": "TP_001", "session_id": "1746000000000", "ended_at": "2025-04-30T10:45:00+00:00", "pulse_record_count": 540}
```

---

### Step 8 — Monitor live data

| What | Where |
|---|---|
| Live scores + charts | http://127.0.0.1:8501 (Streamlit dashboard) |
| System health | http://127.0.0.1:8095 (System Check GUI) |
| Questionnaire responses | http://127.0.0.1:8090 |
| Latest telemetry (JSON) | `GET http://127.0.0.1:8080/api/live/latest` |
| Session list (JSON) | `GET http://127.0.0.1:8080/api/sessions` |
| Bridge data stream | `python bridge/wahoo_bridge_gui.py` (Tkinter monitor) |

---

### Step 9 — Run without Unity hardware (simulate a ride)

With all services running:
```bash
python live_analytics/scripts/simulate_ride.py --duration 60 --hz 20
```

This streams 60 seconds of synthetic telemetry directly to the WS ingest port (`:8766`) and populates the dashboard in real time. Useful for testing the full pipeline without a VR headset or sensors.

---

### Step 10 — Run the test suite

```bash
# Full suite (1140 tests)
pytest

# Analytics pipeline only
pytest live_analytics/tests/ -v

# With coverage
pytest --cov=live_analytics --cov=bridge --cov-report=term-missing -q
```

---

### End-to-end flow summary

```
① Install & init DBs
       │
② Start all services (launcher.py)
       │
③ Start bridge (real or mock) → ws://localhost:8765
       │
④ Register test participant in questionnaire (http://localhost:8090)
       │  fill in pre-session questionnaire answers before the headset is put on
       │  participant is stored with no session_id yet
       │
⑤ Press Play in Unity (put on headset)
       │  TelemetryPublisher connects to ws://localhost:8766
       │  sends start_session signal after 1.5 s
       │  analytics server calls GET /api/participants/oldest-unlinked (FIFO)
       │  auto-links session_id → participant_id (no manual step needed)
       │  SESSION_START written to pulse.jsonl + PulseSessionLogger opens dedicated file
       │
⑥ Ride session in progress
       │  HR + gameplay + headpose batches stream at 20 Hz
       │  scores computed every batch → live dashboard updates
       │  every HR sample written to pulse.jsonl AND logs/pulse/<id>_<ts>_pulse_log.jsonl
       │
⑦ Stop Unity (or press Stop in Editor)
       │  TelemetryPublisher sends end_session signal
       │  ws_ingest writes SESSION_END to pulse.jsonl
       │  PulseSessionLogger writes session_end and closes file
       │
⑧ Review results
       Dashboard → http://localhost:8501
       Pulse log  → logs/pulse/<id>_<ts>_pulse_log.jsonl
       Raw JSONL  → live_analytics/data/sessions/<session_id>.jsonl
       SQLite     → live_analytics/data/live_analytics.db
```

---

This project is provided as-is for personal / research use. Wahoo and TICKR are trademarks of Wahoo Fitness.
