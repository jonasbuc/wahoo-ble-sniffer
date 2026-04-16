# Blu Sniffer — Bike VR Data Bridge & Live Analytics

Stream live bike-sensor data into a **Unity VR cycling simulator** using a Wahoo TICKR FIT heart-rate monitor (BLE) and an Arduino for speed / cadence / steering / brake signals. Data is relayed over WebSocket, logged to SQLite / Parquet, and processed by a **real-time analytics pipeline** with a live dashboard, questionnaire system, and system health checks.

## Architecture

```
                                   Live Analytics Pipeline
                                  +------------------------------------+
                                  |  Analytics API    :8080            |
Wahoo TICKR FIT --BLE-->          |  WS Ingest        :8765            |
                        bike_     |  Questionnaire    :8090            |
Arduino --------UDP-->  bridge.py |  System Check GUI :8095            |
                          |       |  Dashboard        :8501            |
                          |       +------------------------------------+
                     WS :8765
                          |
                   Unity (WahooWsClient.cs)
                   BikeMovementController.cs
                          |
                   collector_tail.py --> SQLite / Parquet
```

- **Heart rate**: Wahoo TICKR FIT via Bluetooth LE (Bleak)
- **Bike data** (speed, cadence, steering, brakes): Arduino over UDP
- **Unity consumer**: WebSocket client receives binary frames and drives the VR scene
- **Live analytics**: FastAPI ingest, Streamlit dashboard, pre/post questionnaire, system health checks

## Repository Structure

```
.
├── starters/                      # One-click install & launch scripts
│   ├── INSTALL.command / .bat     #   Set up venv + all dependencies
│   ├── START_ALL.command / .bat   #   Launch ALL services + live status
│   ├── START_BRIDGE.command / .bat#   Wahoo BLE bridge + GUI
│   ├── START_MOCK_BRIDGE.*        #   Simulated data (no hardware)
│   ├── START_GUI.command / .bat   #   Standalone bridge GUI monitor
│   └── launcher.py                #   Master orchestrator (starts services,
│                                  #   shows live health status in terminal)
│
├── live_analytics/                # Real-time analytics pipeline
│   ├── app/                       #   FastAPI ingest & REST API (:8080)
│   │   ├── main.py                #     Server entry point (lifespan)
│   │   ├── api_sessions.py        #     /healthz, session CRUD
│   │   ├── ws_ingest.py           #     WebSocket telemetry ingest (:8765)
│   │   ├── ws_dashboard.py        #     WebSocket dashboard feed
│   │   ├── models/                #     Pydantic data models
│   │   ├── scoring/               #     Real-time scoring engine
│   │   └── storage/               #     SQLite + JSONL raw writer
│   ├── dashboard/                 #   Streamlit dashboard (:8501)
│   ├── questionnaire/             #   Pre/post-session questionnaire (:8090)
│   │   ├── app.py                 #     FastAPI server
│   │   ├── questions.py           #     Question definitions
│   │   └── static/                #     Web UI
│   ├── system_check/              #   System health checks (:8095)
│   │   ├── app.py                 #     FastAPI GUI server
│   │   ├── checks.py              #     All health check implementations
│   │   ├── run_checks.py          #     CLI runner (python -m ...)
│   │   └── static/                #     Web UI
│   ├── scripts/                   #   Utility scripts
│   │   ├── init_db.py             #     Initialize databases
│   │   └── simulate_ride.py       #     Simulate telemetry for testing
│   └── tests/                     #   pytest tests for analytics modules
│
├── bridge/                        # Python BLE bridge & data collector
│   ├── bike_bridge.py             #   WebSocket bridge (HR + Arduino -> Unity)
│   ├── mock_wahoo_bridge.py       #   Mock server (no hardware needed)
│   ├── wahoo_bridge_gui.py        #   Tkinter GUI monitor
│   ├── collector_tail.py          #   VRSF binary collector -> SQLite/Parquet
│   └── db/                        #   DB utilities (views, export, validation)
│
├── unity/                         # Unity C# scripts (all in one place)
│   ├── BikeMovementController.cs  #   Translates sensor data to bike movement
│   ├── WahooWsClient.cs           #   Low-level WebSocket client
│   ├── VrsLogging/                #   VRSF binary session logging
│   └── LiveAnalytics/             #   Telemetry publisher
│
├── tests/                         # pytest suite (BLE parsing, VRSF format,
│                                  #   collector DB, parquet export, mock, e2e)
├── scripts/                       # Shell helpers (capture logs, check port)
├── docs/                          # All documentation
├── pyproject.toml                 # Build config, deps, pytest settings
└── requirements.txt               # pip dependencies
```

## Quick Start

### 1. Install (one-click)

| Platform | Script |
|----------|--------|
| macOS    | Double-click `starters/INSTALL.command` |
| Windows  | Double-click `starters/INSTALL.bat` |

This creates a `.venv`, installs all dependencies, and initializes databases.

**Or manually:**

```bash
git clone https://github.com/jonasbuc/wahoo-ble-sniffer.git
cd wahoo-ble-sniffer

python3 -m venv .venv
source .venv/bin/activate          # macOS / Linux
# .venv\Scripts\activate           # Windows

pip install -r requirements.txt
pip install -e .
```

### 2. Start everything (one-click)

| Platform | Script |
|----------|--------|
| macOS    | Double-click `starters/START_ALL.command` |
| Windows  | Double-click `starters/START_ALL.bat` |

This launches **all services** and shows a live terminal dashboard:

```
  ================================================
       Bike VR - Master Launcher
  ================================================

  * Databases ready

  *  Analytics API          :8080   running
  *  Questionnaire API      :8090   running
  *  System Check GUI       :8095   running
  *  Dashboard              :8501   running

  * All 4 services running!  (3s)

  URLs:
    System Check  -> http://127.0.0.1:8095
    Dashboard     -> http://127.0.0.1:8501
    Analytics API -> http://127.0.0.1:8080
    Questionnaire -> http://127.0.0.1:8090

  Press Ctrl+C to stop all services
```

**Launcher options:**

```bash
python starters/launcher.py                  # start all (no BLE bridge)
python starters/launcher.py --bridge         # also start Wahoo BLE bridge
python starters/launcher.py --bridge --mock  # use simulated bike data
python starters/launcher.py --no-dashboard   # skip Streamlit dashboard
```

### 3. Start the BLE bridge separately

| Platform | Real hardware | Simulated data |
|----------|---------------|----------------|
| macOS    | `starters/START_BRIDGE.command` | `starters/START_MOCK_BRIDGE.command` |
| Windows  | `starters/START_BRIDGE.bat` | `starters/START_MOCK_BRIDGE.bat` |

The bridge scripts automatically open the GUI monitor alongside.

**Manual:**

```bash
python bridge/bike_bridge.py --live --verbose
```

Bridge options:

| Flag | Description |
|------|-------------|
| `--live` | Enable live BLE via Bleak (TICKR FIT HR) |
| `--port PORT` | WebSocket port (default 8765) |
| `--host HOST` | Bind address (default localhost) |
| `--ble-address ADDR` | Connect to a specific BLE device |
| `--verbose` | Debug logging |

### 4. Test without hardware (mock bridge)

```bash
python bridge/mock_wahoo_bridge.py
```

Generates realistic fake sensor data on the same WebSocket interface - perfect for Unity development without hardware.

### Stop

Press `Ctrl+C` to gracefully shut down all services.

## Services

| Service | Port | Description |
|---------|------|-------------|
| **Analytics API** | 8080 | FastAPI REST API + WebSocket telemetry ingest |
| **WS Ingest** | 8765 | WebSocket endpoint for Unity telemetry stream |
| **Questionnaire** | 8090 | Pre/post-session questionnaire web UI |
| **System Check** | 8095 | Live system health dashboard (web UI + API) |
| **Dashboard** | 8501 | Streamlit real-time analytics dashboard |

## System Check

The system check verifies that all components are working:

- Quest headset connection (ADB)
- SQLite databases (analytics + questionnaire)
- Wahoo BLE bridge (WebSocket)
- Analytics API & Questionnaire API (HTTP health)
- VRSF log files (session integrity)
- Session verification by ID

**Web UI:** http://127.0.0.1:8095 (started automatically by `START_ALL`)

**CLI:**

```bash
python -m live_analytics.system_check                    # all checks
python -m live_analytics.system_check --check bridge     # single check
python -m live_analytics.system_check --session SIM_123  # verify session
python -m live_analytics.system_check --json             # JSON output
```

## Testing

```bash
pytest                      # run all 482 tests
pytest -q                   # quiet mode
pytest --tb=short -v        # verbose with short tracebacks
pytest tests/               # bridge & collector tests only
pytest live_analytics/tests # analytics pipeline tests only
```

Tests cover: BLE parsing, VRSF binary format, collector DB, parquet export, mock integration, end-to-end flows, analytics API, questionnaire API, system check, and session verification.

## Unity Integration

See [`docs/QUICKSTART.md`](docs/QUICKSTART.md) for the full setup guide.

**Key C# scripts:**

| Script | Location | Purpose |
|--------|----------|---------|
| `WahooWsClient.cs` | `unity/` | Low-level WebSocket client |
| `BikeMovementController.cs` | `unity/` | Translates sensor data to bike movement |
| `VrsSessionLogger.cs` | `unity/VrsLogging/` | Binary session logging (VRSF format) |
| `TelemetryPublisher.cs` | `unity/LiveAnalytics/` | Real-time telemetry publisher |

## Data Storage

- **Live analytics**: SQLite (WAL mode) under `live_analytics/data/`, JSONL raw files per session
- **VRSF sessions**: Binary files written by Unity, tailed by `collector_tail.py` into SQLite / Parquet
- **Questionnaire**: SQLite under `live_analytics/questionnaire/data/`

See [`bridge/db/SQL_CHEATSHEET.md`](bridge/db/SQL_CHEATSHEET.md) for query examples.

## Platform Notes

### Windows (primary)

- Run `starters\INSTALL.bat` first to create the venv and install everything.
- Use the `.bat` starters to launch services.
- Native Windows 10/11 BLE works out of the box.
- All scripts are tested for Windows `cmd.exe` compatibility (ASCII-safe, `chcp 65001`).

### macOS

- Grant Bluetooth permission when prompted (System Settings > Privacy > Bluetooth).
- Close Wahoo Fitness / Zwift before running (they may lock the BLE connection).
- If the TICKR doesn't appear, see [`docs/PAIRING_HELP.md`](docs/PAIRING_HELP.md).

### Linux

- Install `bluez` and grant BLE capabilities:
  ```bash
  sudo setcap cap_net_raw+eip $(readlink -f $(which python3))
  ```

## Troubleshooting

| Problem | Fix |
|---------|-----|
| TICKR not found | Wear the TICKR (wet contacts activate it), close competing apps |
| Frequent disconnections | Move closer, check battery, unpair from phones/watches |
| WebSocket connection failed | Make sure the bridge is running before starting Unity |
| Database locked | Only one collector instance should write at a time |
| Services won't start | Run `starters/INSTALL` first, check `python --version` >= 3.11 |

For detailed BLE pairing help on macOS, see [`docs/PAIRING_HELP.md`](docs/PAIRING_HELP.md).

## License

This project is provided as-is for personal use. Wahoo and TICKR are trademarks of Wahoo Fitness.
