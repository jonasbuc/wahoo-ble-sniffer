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
│   │   ├── ws_ingest.py              #   WebSocket ingest server (:8766)
│   │   ├── ws_dashboard.py           #   WebSocket dashboard feed (/ws/dashboard)
│   │   ├── models/                   #   Pydantic v2 data models
│   │   ├── scoring/                  #   Real-time scoring (features.py, rules.py, anomaly.py)
│   │   └── storage/                  #   SQLite pool (sqlite_store.py) + JSONL writer (raw_writer.py)
│   ├── dashboard/
│   │   └── streamlit_app.py          # Streamlit dashboard (:8501)
│   ├── questionnaire/
│   │   ├── app.py                    #   FastAPI questionnaire service (:8090)
│   │   ├── config.py                 #   Configuration via env vars (QS_*)
│   │   ├── db.py                     #   SQLite CRUD
│   │   ├── questions.py              #   Pre/post question definitions
│   │   ├── models.py                 #   Pydantic models
│   │   └── static/                   #   SPA web UI (served at /)
│   ├── system_check/
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
│   │   └── sessions/                 #     Per-session JSONL raw-event files
│   └── tests/                        # pytest – analytics pipeline
│
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
│   ├── VrsLogging/                   #   VRSF binary session logging
│   └── LiveAnalytics/                #   Telemetry publisher scripts
│       ├── TelemetryPublisher.cs
│       ├── TelemetryBuffer.cs
│       ├── TelemetryConfig.cs
│       ├── TelemetryModels.cs
│       └── LiveFeedbackClient.cs
│
├── tests/                            # pytest – bridge, collector, parser, VRSF
├── logs/                             # Service log files (auto-created by launcher)
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
| `LA_HTTP_HOST` | `0.0.0.0` | API bind address |
| `LA_HTTP_PORT` | `8080` | API HTTP port |
| `LA_WS_INGEST_HOST` | `0.0.0.0` | WS ingest bind address |
| `LA_WS_INGEST_PORT` | `8766` | WS ingest port |
| `LA_SCORING_WINDOW_SEC` | `5.0` | Sliding window size for live scoring |
| `LA_HR_BASELINE_BPM` | `70.0` | Resting HR baseline for scoring |
| `LA_LOG_LEVEL` | `INFO` | Logging level |

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
| `live_analytics/questionnaire/data/` | `ensure_dirs()` at startup | Questionnaire data root |
| `live_analytics/questionnaire/data/questionnaire.db` | `init_db.py` / first startup | SQLite questionnaire DB |
| `logs/` | Launcher on first run | Service stdout/stderr log files (rotated at 2 MB, 3 backups) |

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
        │
        ├── GET /api/live/latest  ◄── Streamlit dashboard (polls every REFRESH_SEC)
        ├── GET /api/sessions     ◄── Streamlit dashboard
        └── WS  /ws/dashboard     ◄── Streamlit dashboard (push updates)

Streamlit dashboard (:8501)
  • reads LA_API_BASE (default http://127.0.0.1:8080)
  • polls REST + subscribes to /ws/dashboard

Questionnaire service (:8090)
  • standalone FastAPI process with its own SQLite DB
  • REST API + static SPA served from live_analytics/questionnaire/static/

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

| Method | Path | Description |
|---|---|---|
| `GET` | `/healthz` | API status + SQLite DB reachability |
| `GET` | `/api/sessions` | List all sessions (summary) |
| `GET` | `/api/sessions/{session_id}` | Session detail + latest scores |
| `GET` | `/api/live/latest` | Latest live telemetry across all active sessions |
| `WS` | `/ws/dashboard` | Push live score updates to dashboard clients |

WebSocket ingest (port **8766**, separate `websockets` server):

| Protocol | URL | Description |
|---|---|---|
| WebSocket | `ws://localhost:8766` | Unity telemetry ingest (JSON `TelemetryBatch` messages) |

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
# Run all 966 tests
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
- **`live_analytics/tests/`** — analytics API endpoints, WS ingest, scoring pipeline, SQLite store, raw writer, configuration, crash diagnostics, fresh-clone bootstrap, regression tests
- **`live_analytics/questionnaire/tests/`** — questionnaire API endpoints, DB CRUD, error handling
- **`live_analytics/system_check/tests/`** — system check probes, app endpoints, VRSF log inspection

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
| Per-session raw JSONL | `live_analytics/data/sessions/<session_id>.jsonl` | WS ingest (first event) |
| Questionnaire SQLite | `live_analytics/questionnaire/data/questionnaire.db` | `init_db.py` / questionnaire API |
| VRSF binary sessions | `Logs/` (Unity-controlled path) | Unity `VrsSessionLogger.cs` |
| Collector SQLite / Parquet | `collector_out/` | `bridge/collector_tail.py` |

The analytics database is opened in **WAL mode** with a thread-safe connection pool, allowing concurrent reads from the dashboard while the ingest server is writing.

---

## Unity integration

See [`unity/LiveAnalytics/`](unity/LiveAnalytics/) for the C# telemetry publisher.

| Script | Location | Purpose |
|---|---|---|
| `WahooWsClient.cs` | `unity/` | WebSocket client for the BLE bridge (`:8765`) |
| `BikeMovementController.cs` | `unity/` | Translates sensor data to in-game bike movement |
| `SpawnZoneTrigger.cs` | `unity/` | Sends timestamped trigger events on collider hit |
| `TelemetryPublisher.cs` | `unity/LiveAnalytics/` | Batches + sends telemetry to ingest server (`:8766`) |
| `TelemetryBuffer.cs` | `unity/LiveAnalytics/` | In-memory batch buffer |
| `TelemetryConfig.cs` | `unity/LiveAnalytics/` | Ingest server URL and batch settings |
| `TelemetryModels.cs` | `unity/LiveAnalytics/` | Serialisation models |
| `LiveFeedbackClient.cs` | `unity/LiveAnalytics/` | Receives live scores from `/api/live/latest` |
| `VrsSessionLogger.cs` | `unity/VrsLogging/` | Binary VRSF session logging |

The ingest endpoint (`ws://127.0.0.1:8766`) expects JSON messages matching the `TelemetryBatch` Pydantic model defined in `live_analytics/app/models/`.

Full setup guide: [`docs/QUICKSTART.md`](docs/QUICKSTART.md)

---

## License

This project is provided as-is for personal / research use. Wahoo and TICKR are trademarks of Wahoo Fitness.
