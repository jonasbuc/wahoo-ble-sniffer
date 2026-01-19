# Wahoo BLE Logger

Log live BLE data from Wahoo TICKR heart rate monitors and KICKR trainers directly to SQLite, using standard Bluetooth GATT services (no FIT files, no Wahoo SDK required).

**Available in two implementations:**
- **[Python version](.)** - Using Python 3.11+ and Bleak library (this directory)
- **[C# version](WahooBleLoggerCSharp/)** - Using .NET 8 and InTheHand.BluetoothLE

---

## Python Implementation

A production-quality Python application for logging live BLE data.

## Features

- **Auto-discovery**: Automatically scans for and connects to Wahoo TICKR and KICKR devices
- **Concurrent logging**: Handles multiple devices simultaneously using asyncio
- **Standard BLE protocols**: 
  - Heart Rate Service (0x180D) for TICKR
  - Fitness Machine Service (0x1826/FTMS) for KICKR
- **Comprehensive data capture**:
  - Heart rate (bpm) and RR-intervals (ms)
  - Power (watts), cadence (rpm), speed (km/h)
- **Robust SQLite storage**: WAL mode for reliability during long sessions
- **Auto-reconnect**: Gracefully handles device disconnections
- **Debug mode**: View raw BLE packets and parsing details

## Requirements

- Python 3.11 or higher
- Bluetooth adapter (built-in on macOS/Windows)
- Wahoo TICKR heart rate monitor
- Wahoo KICKR SNAP or other KICKR trainer (optional)

## Setup

### 1. Create a virtual environment

```bash
# Navigate to the project directory
cd "Blu Sniffer"

# Create virtual environment
python3 -m venv venv

# Activate virtual environment
# On macOS/Linux:
source venv/bin/activate

# On Windows:
venv\Scripts\activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

## Running the Logger

### Basic usage (auto-discovery)

```bash
python wahoo_ble_logger.py
```

The program will:
1. Scan for Wahoo devices (looks for "TICKR" and "KICKR" in device names)
2. Connect to any found devices
3. Start logging data to `training.db`
4. Display live metrics in the terminal

### Specify device addresses (optional)

If auto-discovery doesn't work or you want to connect to specific devices:

```bash
python wahoo_ble_logger.py --tickr-address AA:BB:CC:DD:EE:FF --kickr-address 11:22:33:44:55:66
```

**Finding MAC addresses:**
- macOS: System Settings → Bluetooth, or use `system_profiler SPBluetoothDataType`
- Windows: Settings → Bluetooth & devices, or use Device Manager
- Linux: `bluetoothctl` or `hcitool scan`

### Debug mode

View raw BLE packets and parsing details:

```bash
python wahoo_ble_logger.py --debug
```

### Stop the logger

Press `Ctrl+C` to gracefully shut down and disconnect from devices.

## Data Storage

All metrics are stored in `training.db` (SQLite) with the following schema:

```sql
CREATE TABLE metrics (
    ts REAL NOT NULL,           -- Unix timestamp
    hr_bpm INTEGER,             -- Heart rate (bpm)
    rr_ms INTEGER,              -- RR-interval (milliseconds)
    power_w INTEGER,            -- Power output (watts)
    cadence_rpm REAL,           -- Cadence (rpm)
    speed_kph REAL              -- Speed (km/h)
);
```

You can query this database with any SQLite client or Python script:

```python
import sqlite3
conn = sqlite3.connect('training.db')
cursor = conn.cursor()
cursor.execute("SELECT datetime(ts, 'unixepoch', 'localtime'), * FROM metrics ORDER BY ts DESC LIMIT 10")
for row in cursor.fetchall():
    print(row)
```

## Platform-Specific Notes

### macOS

- **Bluetooth permissions**: macOS may prompt for Bluetooth access the first time you run the script. Grant permission in System Settings → Privacy & Security → Bluetooth.
- **Close other apps**: Ensure the Wahoo Fitness app, Zwift, TrainerRoad, or similar apps are closed, as they may lock the BLE connection.
- Built-in Bluetooth works out of the box.

### Windows

- **Bluetooth drivers**: Ensure Bluetooth drivers are up to date.
- **Close other apps**: Close Wahoo Fitness, Zwift, etc.
- Windows 10/11 includes built-in Bluetooth LE support.
- If using WSL2, Bluetooth passthrough may not work—run natively in Windows.

### Linux

- May require additional permissions: `sudo setcap cap_net_raw+eip $(eval readlink -f $(which python))`
- Or run with `sudo` (not recommended for production).
- Ensure `bluez` is installed and running.

## Troubleshooting

### "No Wahoo devices found"

1. **Wake up the TICKR**: Wear the heart rate monitor so it detects your heartbeat and powers on
2. **Activate the KICKR**: Start pedaling to wake up the trainer
3. **Check Bluetooth**: Ensure Bluetooth is enabled on your computer
4. **Close competing apps**: Quit Wahoo Fitness app, Zwift, TrainerRoad, Peloton app, etc.
5. **Try manual pairing**: Find the MAC address and use `--tickr-address` / `--kickr-address`

### "Failed to connect" or frequent disconnections

1. **Move closer**: Ensure devices are within 10 meters of your computer
2. **Remove interference**: Move away from WiFi routers, microwaves, or other 2.4 GHz devices
3. **Check battery**: Replace TICKR battery if low (CR2032); ensure KICKR is plugged in
4. **Restart Bluetooth**: Toggle Bluetooth off/on on your computer
5. **Unpair from other devices**: Remove pairing from phones, watches, etc.

### "FTMS parsing error" in debug mode

- The KICKR may send data with different flags than expected
- Enable `--debug` to see raw packets
- Some older KICKR models may have slightly different FTMS implementations
- The parser is designed to be robust and skip unknown fields

### Device only appears briefly then disconnects

- Another application may be connecting to it automatically
- Check for auto-connect settings in Wahoo Fitness, Zwift, etc.
- On macOS, check System Settings → Bluetooth for paired devices and remove if needed

### "No data appearing" from KICKR

- **Start pedaling**: The KICKR only sends data when active
- Some metrics (cadence, power) only appear when you're actually riding
- Speed may be 0 if the trainer is in ERG mode and not simulating road speed

### Database locked errors

- If you're running multiple instances of the script, only one should write to the database
- WAL mode should prevent most locking issues
- Check that `training.db-wal` and `training.db-shm` files aren't corrupted

## Advanced Usage

### Running as a background service

On Linux/macOS, you can use `screen` or `tmux`:

```bash
screen -S wahoo
python wahoo_ble_logger.py
# Press Ctrl+A, then D to detach
# Reattach with: screen -r wahoo
```

Or create a systemd service (Linux) / launchd plist (macOS).

### Exporting data

Export to CSV for analysis:

```bash
sqlite3 training.db -header -csv "SELECT datetime(ts, 'unixepoch', 'localtime') as timestamp, * FROM metrics" > training.csv
```

### Multiple sessions

The logger appends to the database. To start fresh for each workout:

```bash
# Backup previous data
mv training.db training_backup_$(date +%Y%m%d_%H%M%S).db

# Run logger (will create new database)
python wahoo_ble_logger.py
```

## Technical Details

### BLE Services & Characteristics

**TICKR (Heart Rate)**
- Service: `0x180D` (Heart Rate Service)
- Characteristic: `0x2A37` (Heart Rate Measurement)
- Format: Follows Bluetooth SIG specification
  - Flags byte indicates data format
  - Heart rate in uint8 or uint16
  - Optional RR-intervals in 1/1024 second units

**KICKR (Fitness Machine / FTMS)**
- Service: `0x1826` (Fitness Machine Service)
- Characteristic: `0x2AD2` (Indoor Bike Data)
- Format: FTMS specification with flags-based field presence
  - Instantaneous Speed (0.01 km/h resolution)
  - Instantaneous Cadence (0.5 rpm resolution)
  - Instantaneous Power (1 watt resolution, signed)

### Reconnection Strategy

- Monitors connection state every second
- On disconnect: waits 5 seconds before attempting reconnect
- Scans again if device disappeared (e.g., moved out of range)
- Continues indefinitely until manually stopped

### Database Design

- WAL (Write-Ahead Logging) mode for better concurrency and crash resistance
- Indexed by timestamp for efficient time-range queries
- Each row represents a single metric update from either device
- NULL values for metrics not present in that update

## License

This project is provided as-is for personal use. Wahoo and KICKR are trademarks of Wahoo Fitness.

## Support

For issues:
1. Run with `--debug` flag to see detailed logs
2. Check that devices work with official Wahoo app first
3. Verify Bluetooth functionality with other BLE devices
4. Review troubleshooting section above

---

**Happy training! 🚴‍♂️💓**
