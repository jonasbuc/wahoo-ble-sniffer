# Blu Sniffer — Bike VR Data Bridge

Stream live bike-sensor data into Unity VR using a Wahoo TICKR FIT heart-rate monitor (BLE) and an Arduino for speed / cadence / steering / brake signals. Data is relayed over WebSocket, logged to SQLite/Parquet, and processed by the live analytics pipeline.

## Architecture

```
Wahoo TICKR FIT  ──BLE──►  bike_bridge.py  ──WS──►  Unity (WahooWsClient.cs)
Arduino          ──UDP──►  bike_bridge.py           BikeMovementController.cs
                                    │
                                    └──► collector_tail.py ──► SQLite / Parquet
```

- **Heart rate**: Wahoo TICKR FIT via Bluetooth LE (Bleak)
- **Bike data** (speed, cadence, steering, brakes): Arduino over UDP
- **Unity consumer**: WebSocket client receives binary frames and drives the VR scene

## Repository Structure

```
.
├── UnityIntegration/              # Unity ↔ Python bridge & C# scripts
│   ├── python/                    #   Bridge, mock server, GUI, collector
│   │   ├── bike_bridge.py         #   WebSocket bridge (TICKR HR + Arduino → Unity)
│   │   ├── mock_wahoo_bridge.py   #   Mock server for testing without hardware
│   │   ├── wahoo_bridge_gui.py    #   Tkinter GUI monitor
│   │   ├── ble_test_connect.py    #   TICKR FIT BLE connection test
│   │   ├── collector_tail.py      #   VRSF binary collector → SQLite/Parquet
│   │   └── db/                    #   DB utilities (views, export, validation)
│   ├── unity/                     #   Unity C# controllers
│   ├── Assets/VrsLogging/         #   VRS session-logging C# scripts
│   ├── UnityClient/               #   WahooWsClient.cs WebSocket client
│   ├── starters/                  #   One-click start scripts (.command/.bat/.ps1)
│   ├── scripts/                   #   Shell helpers (capture logs, check port, …)
│   └── docs/                      #   Guides (QUICKSTART, OVERSIGT, UNITY_SETUP, …)
│
├── live_analytics/                # Real-time analytics pipeline
│   ├── app/                       #   FastAPI ingest & REST API (port 8080)
│   ├── dashboard/                 #   Streamlit dashboard (port 8501)
│   ├── questionnaire/             #   Pre/post-session questionnaire (port 8090)
│   ├── system_check/              #   System Check GUI (port 8095)
│   ├── scripts/                   #   PowerShell launch scripts
│   └── tests/                     #   pytest tests for analytics modules
│
├── Assets/Scripts/LiveAnalytics/  # Unity C# telemetry publisher
│
├── tests/                         # pytest suite
│                                  #   BLE parsing, VRSF format, collector,
│                                  #   Parquet export, mock integration, end-to-end
├── docs/                          # Top-level docs
│   └── PAIRING_HELP.md            #   macOS BLE pairing troubleshooting
│
├── pyproject.toml                 # Build config, dependencies, pytest settings
├── requirements.txt               # pip dependencies (used by CI)
└── .flake8                        # Linter config
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

### 2. Run the Unity bridge

The bridge streams TICKR FIT heart-rate data (and any Arduino UDP events) over WebSocket so Unity can consume them in real time.

**One-click (recommended):**

| Platform   | Script                                                              |
|------------|---------------------------------------------------------------------|
| macOS      | Double-click `UnityIntegration/starters/START_WAHOO_BRIDGE.command` |
| Windows    | Double-click `UnityIntegration/starters/START_WAHOO_BRIDGE.bat`     |
| PowerShell | `.\UnityIntegration\starters\START_WAHOO_BRIDGE.ps1`                |

These launch both the bridge and the GUI monitor with the `--live` flag.

**Manual:**

```bash
python UnityIntegration/python/bike_bridge.py --live --verbose
```

Bridge options:

| Flag                    | Description                                    |
|-------------------------|------------------------------------------------|
| `--live`                | Enable live BLE via Bleak (TICKR FIT HR)       |
| `--port PORT`           | WebSocket port (default 8765)                  |
| `--host HOST`           | Bind address (default localhost)               |
| `--ble-address ADDR`    | Connect to a specific BLE device               |
| `--keepalive-interval`  | Seconds between battery keepalive reads        |
| `--base-backoff`        | Base reconnect backoff (seconds)               |
| `--max-backoff`         | Max reconnect backoff (seconds)                |
| `--verbose`             | Debug logging                                  |

### 3. Test without hardware (mock bridge)

```bash
python UnityIntegration/python/mock_wahoo_bridge.py
```

Generates realistic fake sensor data on the same WebSocket interface — perfect for Unity development without hardware.

### Stop

Press `Ctrl+C` to gracefully shut down and disconnect.

## Testing

```bash
pytest                    # Run all tests
pytest -q                 # Quiet mode
pytest --tb=short -v      # Verbose with short tracebacks
```

Tests cover BLE parsing, VRSF binary format, collector DB, parquet export, mock integration, and end-to-end flows.

## Unity Integration

See [`UnityIntegration/README.md`](UnityIntegration/README.md) for the full Unity setup guide.

**Key C# scripts:**

| Script                       | Location                              | Purpose                                    |
|------------------------------|---------------------------------------|--------------------------------------------|
| `WahooDataReceiver.cs`       | `UnityIntegration/unity/`             | Receives WebSocket data in Unity           |
| `BikeMovementController.cs`  | `UnityIntegration/unity/`             | Translates sensor data to bike movement    |
| `WahooWsClient.cs`           | `UnityIntegration/UnityClient/`       | Low-level WebSocket client                 |
| `VrsSessionLogger.cs`        | `UnityIntegration/Assets/VrsLogging/` | Binary session logging (VRSF format)       |

## Data Storage

The Unity bridge collector (`collector_tail.py`) writes VRSF binary sessions to SQLite and optionally exports to Parquet. See [`UnityIntegration/python/db/SQL_CHEATSHEET.md`](UnityIntegration/python/db/SQL_CHEATSHEET.md) for query examples.

The live analytics pipeline stores telemetry in its own SQLite database under `live_analytics/data/`.

## Platform Notes

### macOS

- Grant Bluetooth permission when prompted (System Settings → Privacy & Security → Bluetooth).
- Close Wahoo Fitness / Zwift before running — they may lock the BLE connection.
- If the TICKR doesn't appear, see [`docs/PAIRING_HELP.md`](docs/PAIRING_HELP.md).

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
| TICKR not found | Wear the TICKR (wet contacts activate it), close competing apps |
| Frequent disconnections | Move closer, check battery, unpair from phones/watches |
| WebSocket connection failed | Make sure the bridge is running before starting Unity |
| Database locked | Only one collector instance should write at a time |

For detailed BLE pairing help on macOS, see [`docs/PAIRING_HELP.md`](docs/PAIRING_HELP.md).

## CI

A GitHub Actions workflow (`ci.yml`) runs on push to `main`:

- Installs all dependencies (BLE bridge + live analytics)
- Lints with **flake8**
- Type-checks `UnityIntegration/` with **mypy**
- Runs the full **pytest** suite with coverage

## License

This project is provided as-is for personal use. Wahoo and TICKR are trademarks of Wahoo Fitness.

---

**Happy training! 🚴‍♂️💓**
