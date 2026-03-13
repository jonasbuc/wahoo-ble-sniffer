# Wahoo BLE Sniffer

Log live BLE data from Wahoo TICKR heart rate monitors and KICKR trainers, stream it over WebSocket to Unity, and analyse recorded sessions — all using standard Bluetooth GATT services (no FIT files, no Wahoo SDK required).

## Repository Structure

```
.
├── python/                        # Standalone BLE logger (Python + Bleak)
│   ├── wahoo_ble_logger.py        #   Main logger → SQLite
│   ├── find_wahoo_devices.py      #   Scan for Wahoo devices
│   └── quick_find.py              #   Quick BLE device check
│
├── UnityIntegration/              # Unity ↔ Python bridge & C# scripts
│   ├── python/                    #   Bridge, mock server, GUI, collector
│   │   ├── wahoo_unity_bridge.py  #     WebSocket bridge (BLE → Unity)
│   │   ├── mock_wahoo_bridge.py   #     Mock server for testing without hardware
│   │   ├── wahoo_bridge_gui.py    #     Tkinter GUI monitor
│   │   ├── collector_tail.py      #     VRSF binary collector → SQLite/Parquet
│   │   └── db/                    #     DB utilities (views, export, validation)
│   ├── unity/                     #   Unity C# controllers
│   ├── Assets/VrsLogging/         #   VRS session-logging C# scripts
│   ├── UnityClient/               #   WahooWsClient.cs WebSocket client
│   ├── starters/                  #   One-click start scripts (.command/.bat/.ps1)
│   ├── scripts/                   #   Shell helpers (capture logs, check port, …)
│   └── docs/                      #   Guides (QUICKSTART, OVERSIGT, UNITY_SETUP, …)
│
├── WahooBleLoggerCSharp/          # C# BLE logger (.NET 8 + InTheHand.BluetoothLE)
│
├── analysis/                      # Data analysis notebooks & plot scripts
│   ├── quick_analysis.ipynb       #   Jupyter notebook with overview plots
│   ├── run_quick_plots.py         #   Programmatic plot generation
│   └── generate_mock_data.py      #   Generate realistic mock Parquet data
│
├── tests/                         # pytest test suite (36 tests)
├── docs/                          # Top-level documentation
│   └── PAIRING_HELP.md            #   macOS BLE pairing troubleshooting
├── collector_out/                  # Generated test data (Parquet + SQLite)
│
├── pyproject.toml                 # Build config, dependencies, pytest settings
├── requirements.txt               # pip dependencies (used by CI)
├── .flake8                        # Linter config
└── Blu Sniffer.sln                # .NET solution (WahooBleLoggerCSharp)
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
python python/wahoo_ble_logger.py
```

This will auto-discover Wahoo devices, connect, and log heart rate / power / cadence / speed to `training.db` (SQLite).

Options:

```bash
python python/wahoo_ble_logger.py --debug                             # Show raw BLE packets
python python/wahoo_ble_logger.py --tickr-address AA:BB:CC:DD:EE:FF   # Connect to a specific device
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

See [`UnityIntegration/README.md`](UnityIntegration/README.md) for the full Unity setup guide.

**Key C# scripts:**

| Script                       | Location                              | Purpose                                  |
|------------------------------|---------------------------------------|------------------------------------------|
| `WahooDataReceiver.cs`       | `UnityIntegration/unity/`             | Receives WebSocket data in Unity         |
| `BikeMovementController.cs`  | `UnityIntegration/unity/`             | Translates sensor data to bike movement  |
| `VRBikeController.cs`        | `UnityIntegration/unity/`             | VR-specific bike controller              |
| `WahooWsClient.cs`           | `UnityIntegration/UnityClient/`       | Low-level WebSocket client               |
| `VrsSessionLogger.cs`        | `UnityIntegration/Assets/VrsLogging/` | Binary session logging (VRSF format)     |

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

## License

This project is provided as-is for personal use. Wahoo and KICKR are trademarks of Wahoo Fitness.

---

**Happy training! 🚴‍♂️💓**
