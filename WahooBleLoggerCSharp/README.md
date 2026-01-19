# Wahoo BLE Logger - C# Edition

A C# implementation of the Wahoo BLE Logger that logs live BLE data from Wahoo TICKR heart rate monitors and KICKR trainers to SQLite.

## Features

- **Auto-discovery**: Automatically scans for and connects to Wahoo TICKR and KICKR devices
- **Concurrent logging**: Handles multiple devices simultaneously using async/await
- **Standard BLE protocols**: 
  - Heart Rate Service (0x180D) for TICKR
  - Cycling Power Service (0x1818) for KICKR
  - Fitness Machine Service (0x1826/FTMS) fallback
- **Comprehensive data capture**:
  - Heart rate (bpm) and RR-intervals (ms)
  - Power (watts), cadence (rpm), speed (km/h)
- **Robust SQLite storage**: WAL mode for reliability
- **Auto-reconnect**: Gracefully handles device disconnections
- **Cross-platform**: Works on Windows, macOS, and Linux

## Requirements

- .NET 8.0 SDK or later
- Bluetooth adapter (built-in on most systems)
- Wahoo TICKR heart rate monitor (optional)
- Wahoo KICKR trainer (optional)

## Setup

### 1. Install .NET 8.0

**macOS:**
```bash
brew install dotnet@8
```

**Windows:**
Download from https://dotnet.microsoft.com/download

**Linux:**
Follow instructions at https://learn.microsoft.com/dotnet/core/install/linux

### 2. Build the project

```bash
cd WahooBleLoggerCSharp
dotnet restore
dotnet build
```

## Running the Logger

### Basic usage (auto-discovery)

```bash
dotnet run
```

The program will:
1. Scan for Wahoo devices (looks for "TICKR" and "KICKR" in device names)
2. Connect to any found devices
3. Start logging data to `training.db`
4. Display live metrics in the terminal

### Specify device addresses (optional)

```bash
dotnet run -- --tickr-address "AA:BB:CC:DD:EE:FF" --kickr-address "11:22:33:44:55:66"
```

### Debug mode

```bash
dotnet run -- --debug
```

### Show all discovered devices

```bash
dotnet run -- --show-all-devices
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

Query the database:

```bash
sqlite3 training.db "SELECT datetime(ts, 'unixepoch', 'localtime'), * FROM metrics ORDER BY ts DESC LIMIT 10"
```

## Platform-Specific Notes

### macOS

- **Bluetooth permissions**: macOS may prompt for Bluetooth access
- **Unpair devices**: For best results, unpair TICKR and KICKR from System Settings → Bluetooth before running
- **Close other apps**: Ensure Wahoo Fitness app, Zwift, etc. are closed

### Windows

- **Bluetooth drivers**: Ensure Bluetooth drivers are up to date
- **Windows 10/11**: Built-in Bluetooth LE support
- **Close other apps**: Close Wahoo Fitness, Zwift, etc.

### Linux

- **BlueZ**: Requires BlueZ 5.0 or later
- **Permissions**: May need to run with sudo or configure permissions

## Troubleshooting

### "No Wahoo devices found"

1. **Wake up the TICKR**: Wear the heart rate monitor
2. **Activate the KICKR**: Start pedaling
3. **Check Bluetooth**: Ensure Bluetooth is enabled
4. **Close competing apps**: Quit Wahoo Fitness, Zwift, etc.
5. **Unpair from system**: Remove pairing from OS Bluetooth settings

### Connection issues

1. **Move closer**: Ensure devices are within 10 meters
2. **Remove interference**: Move away from WiFi routers, microwaves
3. **Check battery**: Replace TICKR battery (CR2032); ensure KICKR is plugged in
4. **Restart Bluetooth**: Toggle Bluetooth off/on

### Build errors

```bash
# Clean and rebuild
dotnet clean
dotnet restore
dotnet build
```

## Project Structure

```
WahooBleLoggerCSharp/
├── Program.cs                 # Main application
├── WahooBleLogger.csproj      # Project file
└── README.md                  # This file
```

## Dependencies

- **Microsoft.Data.Sqlite** (8.0.0): SQLite database access
- **InTheHand.BluetoothLE** (4.0.37): Cross-platform Bluetooth LE library

## Comparison with Python Version

| Feature | C# Version | Python Version |
|---------|------------|----------------|
| Language | C# (.NET 8) | Python 3.11+ |
| BLE Library | InTheHand.BluetoothLE | Bleak |
| Database | Microsoft.Data.Sqlite | sqlite3 (built-in) |
| Performance | Faster startup, lower memory | Slower startup |
| Deployment | Single executable (publish) | Requires Python runtime |
| Platform Support | Windows, macOS, Linux | Windows, macOS, Linux |

## Publishing

Create a standalone executable:

```bash
# macOS (ARM64)
dotnet publish -c Release -r osx-arm64 --self-contained

# macOS (x64)
dotnet publish -c Release -r osx-x64 --self-contained

# Windows
dotnet publish -c Release -r win-x64 --self-contained

# Linux
dotnet publish -c Release -r linux-x64 --self-contained
```

The executable will be in `bin/Release/net8.0/{runtime}/publish/`

## License

This project is provided as-is for personal use. Wahoo and KICKR are trademarks of Wahoo Fitness.

---

**Happy training! 🚴‍♂️💓**
