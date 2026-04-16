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
| `bike_bridge.py` | TICKR HR + Arduino UDP → WebSocket server (mock + live) |
| `wahoo_bridge_gui.py` | Tkinter GUI monitor |
| `ble_test_connect.py` | Test af TICKR FIT BLE forbindelse |
| `collector_tail.py` | VRSF binary → SQLite / Parquet |
| `db/` | DB utilities (views, export, validering) |

### Unity C# Scripts

| Fil | Beskrivelse |
|-----|-------------|
| `BikeController.cs` | Bevægelse + styring (ArduinoSerialReader + Quest-controller) |
| `WahooWsClient.cs` | Low-level WebSocket klient — puls fra bridge |
| `ArduinoSerialReader.cs` | Seriel hastighed fra Arduino |
| `GroundSensor.cs` | Grounds-check for CharacterController |

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
bridge/                              # BLE bridge & collector
├── bike_bridge.py                   # TICKR HR + Arduino UDP -> WebSocket
├── mock_wahoo_bridge.py             # Mock server (no hardware)
├── wahoo_bridge_gui.py              # GUI monitor
├── collector_tail.py                # VRSF -> SQLite/Parquet
└── db/                              # DB utilities

unity/                               # Unity C# scripts
├── BikeMovementController.cs        # Bevægelse + styring
├── WahooWsClient.cs                 # Puls fra Python-bro
├── VrsLogging/                      # Session logging (VRSF)
└── LiveAnalytics/                   # Telemetri publisher

live_analytics/                      # Real-time analytics pipeline
├── app/                             # FastAPI ingest & API
├── dashboard/                       # Streamlit dashboard
├── questionnaire/                   # Pre/post questionnaire
└── system_check/                    # System health checks

starters/                            # One-click launchers
├── START_ALL.command / .bat         # Start alt
├── START_BRIDGE.command / .bat      # Wahoo BLE bridge
└── START_MOCK_BRIDGE.command / .bat # Mock (ingen hardware)

docs/                                # Dokumentation
```

---

## 🔌 Data Flow

```
TICKR FIT ──BLE──► Python Bridge ──WebSocket──► WahooWsClient.cs (puls)
Arduino   ──Serial──►                            ArduinoSerialReader.cs
                        │                               ↓
                        └──► collector_tail.py    BikeController.cs (bevægelse + styring)
                                    │
                                    ▼
                              SQLite / Parquet
```

---

## 🚀 Opsætning - 4 Steps

1. **Installer Python packages:** `pip install -r requirements.txt`
2. **Start broen:** `python python/bike_bridge.py --live`
3. **Tilføj `BikeController.cs`** + `WahooWsClient.cs` til Unity scene
4. **Tryk Play** i Unity

---

## 💡 Pro Tips

- **Test i Editor først** — kør `bike_bridge.py` uden `--live` for mock-data uden hardware
- **Brug Smoothing** — sæt smoothing factor til 0.3-0.5 for naturlig følelse
- **Debug Overlay** — `WahooWsClient` logger puls i Console
- **Auto-reconnect** — broen håndterer disconnects automatisk

---

**God fornøjelse med dit VR cykelprojekt! 🚴‍♂️🥽**
