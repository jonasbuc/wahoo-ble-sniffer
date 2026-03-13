# Wahoo BLE Sniffer

Log live BLE data from Wahoo TICKR heart rate monitors and KICKR trainers, stream it over WebSocket to Unity, and analyse recorded sessions — all using standard Bluetooth GATT services (no FIT files, no Wahoo SDK required).

## Repository Structure

```
.
├── wahoo_ble_logger.py            # Standalone BLE logger → SQLite (Python + Bleak)
│
├── UnityIntegration/              # Unity ↔ Python bridge & C# scripts
│   ├── python/                    #   Python bridge, mock server, GUI, collector
│   │   ├── wahoo_unity_bridge.py  #     WebSocket bridge (BLE → Unity, production)
│   │   ├── mock_wahoo_bridge.py   #     Mock WebSocket server (testing w/o hardware)
│   │   ├── wahoo_bridge_gui.py    #     Tkinter status monitor + live HR graph
│   │   ├── collector_tail.py      #     VRSF binary tail → SQLite + Parquet
│   │   └── db/                    #     DB utilities
│   │       ├── create_readable_views.py  #   Creates *_readable SQLite VIEWs
│   │       ├── export_readable_views.py  #   Export views → CSV + Parquet
│   │       ├── pretty_dump_db.py         #   Human-readable DB dump
│   │       ├── validate_db.py            #   Sanity-check values & quaternion norms
│   │       └── SQL_CHEATSHEET.md         #   Useful SQL queries
│   │
│   ├── unity/                     #   Unity C# controllers (attach to GameObjects)
│   │   ├── WahooBLEManager.cs     #     Direct BLE ↔ Unity (Shatalmic plugin)
│   │   ├── WahooDataReceiver.cs   #     WebSocket client (receives bridge data)
│   │   ├── WahooDataReceiver_Optimized.cs  # Optimised variant with binary protocol
│   │   ├── BikeMovementController.cs  #   Moves bike GameObject from speed data
│   │   └── VRBikeController.cs    #     VR bike with Rigidbody physics + audio
│   │
│   ├── Assets/VrsLogging/         #   VRSF session-logging C# library
│   │   ├── VrsSessionLogger.cs    #     Orchestrates all stream writers per session
│   │   ├── VrsFormats.cs          #     Binary record layouts + chunk-header writer
│   │   ├── VrsCrc32.cs            #     CRC32 (IEEE 802.3 / zlib compatible)
│   │   ├── VrsFileWriterFixed.cs  #     Background thread writer (fixed-size streams)
│   │   ├── VrsFileWriterEvents.cs #     Background thread writer (variable events)
│   │   ├── SessionManagerUI.cs    #     Unity UI for session create/stop
│   │   └── SessionHistoryRow.cs   #     History list-row prefab component
│   │
│   ├── UnityClient/               #   WahooWsClient.cs — low-level WebSocket client
│   ├── starters/                  #   One-click launchers (.command / .bat / .ps1)
│   ├── scripts/                   #   Shell helpers (capture logs, check port, …)
│   └── docs/                      #   All guides and references
│       ├── QUICKSTART.md          #     5-min setup (C# Option A or Python Option B)
│       ├── OVERSIGT.md            #     High-level overview (Danish)
│       ├── UNITY_SETUP_GUIDE.md   #     Scene setup + BikeMovementController guide
│       ├── README_VRS.md          #     VRSF binary format + collector guide
│       ├── README_CSHARP.md       #     Full C# BLE setup guide
│       ├── SESSION_HISTORY.md     #     Session history UI wiring guide
│       ├── VERIFICATION.md        #     What is tested and verified working
│       └── START_HER.md           #     Danish quick-start entry point
│
├── WahooBleLoggerCSharp/          # C# BLE logger — .NET 8 alternative to Python
│   ├── Program.cs                 #   Main app (scan → connect → log to SQLite)
│   └── WahooBleLogger.csproj      #   NuGet: InTheHand.BluetoothLE + Sqlite
│
├── analysis/                      # Post-session data exploration
│   ├── quick_analysis.ipynb       #   Jupyter notebook — overview plots
│   ├── run_quick_plots.py         #   Write PNGs to analysis/figs/
│   ├── run_more_plots.py          #   Per-session HR overlays, power boxplots
│   ├── generate_mock_data.py      #   Generate realistic mock Parquet sessions
│   └── recompute_summary.py       #   Recompute session_summary.csv from Parquet
│
├── tests/                         # pytest suite (36 tests)
│   │                              #   BLE parsing, SQLite, VRSF format, collector,
│   │                              #   Parquet export, mock integration, end-to-end
├── docs/                          # Top-level docs
│   └── PAIRING_HELP.md            #   macOS BLE pairing troubleshooting
│
├── collector_out/                  # Generated test data (Parquet + SQLite)
├── pyproject.toml                 # Build config, dependencies, pytest settings
├── requirements.txt               # pip install dependencies (used by CI)
├── .flake8                        # Linter config
└── Blu Sniffer.sln                # .NET solution file (WahooBleLoggerCSharp)
```

## Quick Start

### 1. Clone & set up

```bash
git clone https://github.com/jonasbuc/wahoo-ble-sniffer.git
cd wahoo-ble-sniffer

python3 -m venv .venv
source .venv/bin/activate        # macOS / Linux
# .venv\Scripts\activate         # Windows

pip install -r requirements.txt
```

### 2. Run the BLE logger (standalone)

```bash
python wahoo_ble_logger.py
```

This will auto-discover Wahoo devices, connect, and log heart rate / power / cadence / speed to `training.db` (SQLite).

Options:

```bash
python wahoo_ble_logger.py --debug                             # Show raw BLE packets
python wahoo_ble_logger.py --tickr-address AA:BB:CC:DD:EE:FF   # Connect to a specific device
```

### 3. Run the Unity bridge

The bridge streams BLE data over WebSocket so Unity can consume it in real time.

**One-click (recommended):**

| Platform   | Script                                                              |
|------------|---------------------------------------------------------------------|
| macOS      | Double-click `UnityIntegration/starters/START_WAHOO_BRIDGE.command` |
| Windows    | Double-click `UnityIntegration/starters/START_WAHOO_BRIDGE.bat`     |
| PowerShell | `.\UnityIntegration\starters\START_WAHOO_BRIDGE.ps1`                |

These launch both the bridge and the GUI monitor with the `--live` flag.

**Manual:**

```bash
python UnityIntegration/python/wahoo_unity_bridge.py --live --verbose
```

Bridge options:

| Flag                    | Description                                    |
|-------------------------|------------------------------------------------|
| `--live`                | Enable live BLE via Bleak                      |
| `--port PORT`           | WebSocket port (default 8765)                  |
| `--host HOST`           | Bind address (default localhost)               |
| `--ble-address ADDR`    | Connect to a specific BLE device               |
| `--keepalive-interval`  | Seconds between battery keepalive reads        |
| `--base-backoff`        | Base reconnect backoff (seconds)               |
| `--max-backoff`         | Max reconnect backoff (seconds)                |
| `--verbose`             | Debug logging                                  |

### 4. Test without hardware (mock bridge)

```bash
python UnityIntegration/python/mock_wahoo_bridge.py
```

Generates realistic fake sensor data on the same WebSocket interface — perfect for Unity development without a trainer.

### Stop

Press `Ctrl+C` to gracefully shut down and disconnect.

## Testing

```bash
pytest                    # Run all 36 tests
pytest -q                 # Quiet mode
pytest --tb=short -v      # Verbose with short tracebacks
```

Tests cover BLE parsing, SQLite logging, VRSF binary format, collector DB, parquet export, mock integration, and end-to-end flows.

## Unity Integration

See [`UnityIntegration/README.md`](UnityIntegration/README.md) for the full Unity setup guide, or jump directly to [`UnityIntegration/docs/QUICKSTART.md`](UnityIntegration/docs/QUICKSTART.md) for a 5-minute setup.

**Key C# scripts:**

| Script                           | Location                               | Purpose                                          |
|----------------------------------|----------------------------------------|--------------------------------------------------|
| `WahooDataReceiver.cs`           | `UnityIntegration/unity/`              | Receives WebSocket data in Unity                 |
| `BikeMovementController.cs`      | `UnityIntegration/unity/`              | Translates speed data to bike movement           |
| `VRBikeController.cs`            | `UnityIntegration/unity/`              | VR bike with Rigidbody physics + audio           |
| `WahooWsClient.cs`               | `UnityIntegration/UnityClient/`        | Low-level WebSocket client                       |
| `WahooBLEManager.cs`             | `UnityIntegration/unity/`              | Direct BLE in Unity (no Python required)         |
| `VrsSessionLogger.cs`            | `UnityIntegration/Assets/VrsLogging/`  | Binary session logging (VRSF format)             |

## Analysis

The `analysis/` folder contains Jupyter notebooks and scripts for post-session data exploration:

```bash
# Generate mock data for analysis
python analysis/generate_mock_data.py

# Run plots (outputs PNGs to analysis/figs/)
python analysis/run_quick_plots.py
```

## Data Storage

The standalone logger writes to `training.db` (SQLite, WAL mode):

```sql
CREATE TABLE metrics (
    ts REAL NOT NULL,           -- Unix timestamp
    hr_bpm INTEGER,             -- Heart rate (bpm)
    rr_ms INTEGER,              -- RR-interval (ms)
    power_w INTEGER,            -- Power (watts)
    cadence_rpm REAL,           -- Cadence (rpm)
    speed_kph REAL              -- Speed (km/h)
);
```

The Unity bridge collector (`collector_tail.py`) writes VRSF binary sessions to SQLite and optionally exports to Parquet. See [`UnityIntegration/python/db/SQL_CHEATSHEET.md`](UnityIntegration/python/db/SQL_CHEATSHEET.md) for query examples.

## Platform Notes

### macOS

- Grant Bluetooth permission when prompted (System Settings → Privacy & Security → Bluetooth).
- Close Wahoo Fitness / Zwift / TrainerRoad before running — they may lock the BLE connection.
- If devices don't appear, see [`docs/PAIRING_HELP.md`](docs/PAIRING_HELP.md).

### Windows

- Run `UnityIntegration\starters\INSTALL.bat` first to create the venv and install dependencies.
- Use the `.bat` or `.ps1` starters to launch the bridge.
- Native Windows 10/11 BLE works out of the box; WSL2 Bluetooth passthrough is not supported.

### Linux

- Install `bluez` and grant BLE capabilities:
  ```bash
  sudo setcap cap_net_raw+eip $(readlink -f $(which python3))
  ```

## Troubleshooting

| Problem | Fix |
|---------|-----|
| No devices found | Wake TICKR (wear it), start pedaling on KICKR, close competing apps |
| Frequent disconnections | Move closer, check battery, unpair from phones/watches |
| No KICKR data | Start pedaling — KICKR only sends data when active |
| WebSocket connection failed | Make sure the bridge is running before starting Unity |
| Database locked | Only one logger instance should write at a time |

For detailed BLE pairing help on macOS, see [`docs/PAIRING_HELP.md`](docs/PAIRING_HELP.md).

## CI

Two GitHub Actions workflows run on push to `main`:

- **`ci.yml`** — Installs dependencies, runs pytest, runs mypy on `UnityIntegration/`
- **`python-app.yml`** — Editable install, flake8 lint, pytest with coverage

## Code Documentation

Every source file in this repository has been annotated with comprehensive inline comments, docstrings, and XML `<summary>` tags.  Key areas documented per file:

| File | Key comments added |
|------|--------------------|
| `wahoo_ble_logger.py` | Module docstring, HR byte-layout (bit-0 flag), FTMS flags table |
| `wahoo_unity_bridge.py` | Architecture diagram, BLE modes, wire format table, keepalive |
| `mock_wahoo_bridge.py` | Wire format, TCP_NODELAY, broadcast algorithm |
| `collector_tail.py` | VRSF 40-byte header layout, stream record offsets, WAL rationale |
| `wahoo_bridge_gui.py` | Graph coordinate math (X/Y mapping), pan/zoom algorithm |
| `db/*.py` | Timestamp conversion steps, quaternion norm formula |
| `WahooBLEManager.cs` | Android API-31 permission block, byte-layout of FTMS/CP packets |
| `BikeMovementController.cs` | km/h → m/s formula, 3 movement method variants |
| `VRBikeController.cs` | Wheel rotation derivation, audio pitch-from-cadence |
| `VrsFormats.cs` | Full 40-byte VRSF header layout, all 4 stream record layouts |
| `VrsCrc32.cs` | IEEE 802.3 polynomial, lookup-table precomputation |
| `VrsFileWriterFixed.cs` | CRC order (5 steps), ArrayPool ownership, drain cap |
| `VrsSessionLogger.cs` | Accumulator pattern, NDJSON history, display ID counter |
| `WahooBleLoggerCSharp/Program.cs` | HR bit-0 flag, FTMS field-by-field, DBNull vs SQL NULL |

## License

This project is provided as-is for personal use. Wahoo and KICKR are trademarks of Wahoo Fitness.

---

**Happy training! 🚴‍♂️💓**
