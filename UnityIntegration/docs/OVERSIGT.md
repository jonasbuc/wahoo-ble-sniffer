# Unity Integration Oversigt 🎮

**Systemarkitektur: Arduino + TICKR FIT → Python Bridge → Unity**

## 📡 Datakilder

| Kilde | Data | Transport |
|-------|------|-----------|
| Wahoo TICKR FIT | Puls (BPM) | Bluetooth LE → Python (Bleak) |
| Arduino | Hastighed, kadence, styring, bremser | UDP → Python |

Python-broen sender alt videre til Unity over WebSocket.

---

## 📋 Filer Overview

### Python Bridge

| Fil | Beskrivelse |
|-----|-------------|
| `bike_bridge.py` | TICKR HR + Arduino UDP → WebSocket server |
| `mock_wahoo_bridge.py` | Mock server til test uden hardware |
| `wahoo_bridge_gui.py` | Tkinter GUI monitor |
| `ble_test_connect.py` | Test af TICKR FIT BLE forbindelse |
| `collector_tail.py` | VRSF binary → SQLite / Parquet |
| `db/` | DB utilities (views, export, validering) |

### Unity C# Scripts

| Fil | Beskrivelse |
|-----|-------------|
| `WahooDataReceiver.cs` | WebSocket klient (modtager bridge-data) |
| `WahooDataReceiver_Optimized.cs` | Optimeret version |
| `BikeMovementController.cs` | Cykelbevægelse fra sensordata |
| `WahooWsClient.cs` | Low-level WebSocket klient |

### Session Logging (VRSF)

| Fil | Beskrivelse |
|-----|-------------|
| `Assets/VrsLogging/VrsSessionLogger.cs` | Orchestrerer alle writers |
| `Assets/VrsLogging/VrsFormats.cs` | Binary record layouts |
| `python/collector_tail.py` | Tail VRSF → SQLite/Parquet |
| `docs/README_VRS.md` | VRSF format guide |

---

## 📁 Fil Struktur

```
UnityIntegration/
│
├── 🐍 PYTHON BRIDGE
│   ├── python/bike_bridge.py    # TICKR HR + Arduino UDP → WebSocket
│   ├── python/mock_wahoo_bridge.py     # Mock server (ingen hardware)
│   ├── python/wahoo_bridge_gui.py      # GUI monitor
│   ├── python/ble_test_connect.py      # TICKR BLE test
│   └── python/collector_tail.py        # VRSF → SQLite/Parquet
│
├── 🎮 UNITY SCRIPTS
│   ├── unity/WahooDataReceiver.cs      # WebSocket klient
│   ├── unity/BikeMovementController.cs # Cykelbevægelse
│   └── UnityClient/WahooWsClient.cs    # Low-level WS klient
│
├── 📊 SESSION LOGGING (VRSF)
│   ├── Assets/VrsLogging/VrsSessionLogger.cs
│   ├── Assets/VrsLogging/VrsFormats.cs
│   └── docs/README_VRS.md
│
├── 📚 DOKUMENTATION (docs/)
│   ├── QUICKSTART.md                   # Hurtig start
│   ├── OVERSIGT.md                     # Denne fil
│   ├── UNITY_SETUP_GUIDE.md            # Scene setup guide
│   ├── README_VRS.md                   # VRSF binary format
│   ├── SESSION_HISTORY.md              # Session history UI
│   ├── VERIFICATION.md                 # Hvad er testet og virker
│   └── START_HER.md                    # Dansk entry point
│
└── 📦 STARTERS
    ├── starters/START_WAHOO_BRIDGE.command  # macOS one-click
    ├── starters/START_WAHOO_BRIDGE.bat      # Windows one-click
    └── starters/START_MOCK_BRIDGE.command   # Mock (ingen hardware)
```

---

## 🔌 Data Flow

```
TICKR FIT ──BLE──► Python Bridge ──WebSocket──► Unity (WahooDataReceiver)
Arduino   ──UDP──► Python Bridge                       │
                        │                              ▼
                        └──► collector_tail.py    BikeMovementController
                                    │
                                    ▼
                              SQLite / Parquet
```

---

## 🚀 Opsætning - 4 Steps

1. **Installer Python packages:** `pip install -r requirements.txt`
2. **Start broen:** `python python/bike_bridge.py --live`
3. **Tilføj `WahooDataReceiver.cs`** til Unity scene
4. **Tryk Play** i Unity

---

## 💡 Pro Tips

- **Test i Editor først** — brug mock_wahoo_bridge.py uden hardware
- **Brug Smoothing** — sæt smoothing factor til 0.3-0.5 for naturlig følelse
- **Debug Overlay** — WahooDataReceiver har built-in debug display
- **Auto-reconnect** — broen håndterer disconnects automatisk

---

**God fornøjelse med dit VR cykelprojekt! 🚴‍♂️🥽**
