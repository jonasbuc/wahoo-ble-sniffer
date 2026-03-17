# Unity VR Bike Integration

Stream live cykeldata til Unity VR via en Wahoo TICKR FIT pulsmonitor (BLE) og en Arduino for hastighed / kadence / styring / bremser.

## ⚡ Arkitektur

```
Wahoo TICKR FIT  ──BLE──►  bike_bridge.py  ──WS──►  WahooWsClient.cs  (puls)
Arduino          ──Serial──►                          ArduinoSerialReader.cs (hastighed)
                                    │                        ↓
                                    └──► collector_tail.py  BikeController.cs  (bevægelse + styring)
                                                  ↓
                                           SQLite / Parquet
```

- **Puls**: Wahoo TICKR FIT via Bluetooth LE → Python-bro → `WahooWsClient.cs`
- **Hastighed**: Arduino seriel → `ArduinoSerialReader.cs` → `BikeController.cs`
- **Styring**: Meta Quest-controller → `BikeController.cs`

---

## 📋 Requirements

### Python Side (Data Bridge)
- Python 3.11+
- `bleak` — Bluetooth LE
- `websockets` — WebSocket server
- macOS / Windows / Linux med Bluetooth

### Unity Side
- Unity 2021.3+ (LTS anbefalet)
- NativeWebSocket package
- VR headset (Meta Quest, Valve Index, etc.) — valgfrit

---

## 🚀 Quick Start

### Step 1: Installer Python Afhængigheder

```bash
pip install -r requirements.txt
```

### Step 2: Installer Unity Package

1. Åbn dit Unity projekt
2. Window → Package Manager
3. Klik **+** → "Add package from git URL"
4. Indtast: `https://github.com/endel/NativeWebSocket.git#upm`
5. Klik **Add**

### Step 3: Tilføj Scripts til Unity

1. Kopier `BikeController.cs` til `Assets/Scripts/`
2. Kopier `WahooWsClient.cs` til `Assets/Scripts/`
3. Kopier `ArduinoSerialReader.cs` + `GroundSensor.cs` til `Assets/Scripts/`
4. Tilføj `CharacterController` + `BikeController` komponenter til dit cykel-GameObject
5. Sæt alle Inspector-referencer (se [UNITY_SETUP_GUIDE.md](docs/UNITY_SETUP_GUIDE.md))
6. (Valgfri) Opret et tomt GameObject "WahooWsClient" til puls

### Step 4: Start broen (anbefalet)

Sørg for at din TICKR FIT er på (den aktiveres når den bæres mod huden).

**One-click:**

| Platform   | Script                                                              |
|------------|---------------------------------------------------------------------|
| macOS      | Double-click `starters/START_WAHOO_BRIDGE.command`                  |
| Windows    | Double-click `starters/START_WAHOO_BRIDGE.bat`                      |

**Manuel:**

```bash
python UnityIntegration/python/bike_bridge.py --live
```

Du skal se:

```
Scanning for TICKR FIT...
Found TICKR at C7:52:A1:6F:EB:57
✓ Devices ready!
✓ WebSocket server: ws://localhost:8765
```

### Step 5: Kør Unity

Tryk **Play** i Unity. `WahooWsClient` forbinder automatisk til broen.

Tjek Console:
```
[WahooWsClient] Connected
```

### Step 6: Test uden hardware (mock bridge)

```bash
python UnityIntegration/python/bike_bridge.py
```

Genererer realistiske fake sensordata på samme WebSocket interface.

---

## 🎮 Brug af Data i dit VR Spil

### Puls fra WahooWsClient

```csharp
using UnityEngine;

public class MyVRGame : MonoBehaviour
{
    private WahooWsClient wsClient;

    void Start()
    {
        wsClient = FindObjectOfType<WahooWsClient>();
        wsClient.OnHeartRate += OnHR;
    }

    void OnHR(int bpm)
    {
        Debug.Log($"HR: {bpm} bpm");
        // Brug bpm til UI, intensitetslogik, etc.
    }

    void OnDestroy() => wsClient.OnHeartRate -= OnHR;
}
```

### Hastighed fra ArduinoSerialReader

`BikeController` læser `arduinoSerialReader.speed` direkte. Vil du bruge speed i dit eget script:

```csharp
private ArduinoSerialReader arduino;

void Start() => arduino = FindObjectOfType<ArduinoSerialReader>();

void Update()
{
    float speed = arduino != null ? arduino.speed : 0f;
}
```

---

## 🔧 Konfiguration

### WahooWsClient Indstillinger

| Indstilling | Beskrivelse | Standard |
|-------------|-------------|---------|
| Server URL | WebSocket adresse | `ws://localhost:8765` |
| Auto Connect | Forbind ved Start() | ✅ Aktiveret |

---

## 📊 Dataformat

### Binary frame (primær — 12 bytes)

Sendes ved hver HR-opdatering (~20 Hz):

```
struct.pack("di", timestamp, heart_rate)
  d = double  timestamp   (8 bytes, Unix epoch seconds)
  i = int32   heart_rate  (4 bytes, BPM)
```

### JSON event (fra Arduino via UDP)

Bike-data (speed, cadence, steering, brakes) sendes som JSON events fra Arduino:

```json
{"event": "hall_hit",  "source": "udp", "timestamp": 1704067200.1}
{"event": "steering",  "angle": 12.5,   "source": "udp", "timestamp": ...}
{"event": "speed",     "value": 18.3,   "source": "udp", "timestamp": ...}
```

| Felt | Type | Beskrivelse |
|------|------|-------------|
| timestamp | double | Unix epoch seconds (8 bytes) |
| heart_rate | int32 | Puls fra TICKR FIT (4 bytes, BPM) |

---

## 🐛 Fejlfinding

### TICKR FIT ikke fundet
- Sæt TICKR på (elektroder skal røre huden)
- Luk Wahoo Fitness appen hvis den kører
- macOS: Unpair fra Systemindstillinger hvis tidligere parret

### "WebSocket connection failed" i Unity
- Sørg for Python broen kører inden Unity startes
- Tjek at port 8765 ikke bruges af anden app

### Data virker forsinket
- Localhost latency burde være <1ms
- Kontrollér at bridge kører og sender (~1 Hz mock, eller live ved HR-ændring)

### Hyppige disconnects
- Hold TICKR inden for 5 meter af computeren
- Fjern interferens — sluk andre Bluetooth-enheder
- macOS: Reset Bluetooth modul (hold Shift+Option, klik BT-ikon → Debug → Reset)

---

## 📁 Projekt Struktur

```
UnityIntegration/
├── python/                         # Python bridge scripts
│   ├── bike_bridge.py              #   TICKR HR + Arduino UDP → WebSocket (mock + live)
│   ├── wahoo_bridge_gui.py         #   Tkinter status monitor
│   ├── ble_test_connect.py         #   TICKR FIT BLE forbindelsestest
│   ├── collector_tail.py           #   VRSF binary tail → SQLite + Parquet
│   └── db/                         #   DB utilities
│
├── unity/                          # Unity C# controller scripts
│   ├── BikeController.cs           #   Bevægelse + styring (ArduinoSerialReader + Quest)
│   └── BikeMovementController.cs   #   Indeholder BikeController (se ovenstående)
│
├── Assets/VrsLogging/              # VRSF session-logging bibliotek
│   ├── VrsSessionLogger.cs
│   ├── VrsFormats.cs
│   ├── VrsCrc32.cs
│   ├── VrsFileWriterFixed.cs
│   ├── VrsFileWriterEvents.cs
│   ├── SessionManagerUI.cs
│   └── SessionHistoryRow.cs
│
├── UnityClient/                    # WahooWsClient.cs (low-level WS)
├── starters/                       # One-click starters (.command/.bat/.ps1)
├── scripts/                        # Shell helpers
└── docs/                           # Alle guides
    ├── QUICKSTART.md
    ├── OVERSIGT.md
    ├── UNITY_SETUP_GUIDE.md
    ├── README_VRS.md
    ├── SESSION_HISTORY.md
    ├── VERIFICATION.md
    └── START_HER.md
```

---

## 💡 Eksempler på brug

1. **VR Cykelspil**: Spiller kører gennem virtuelle verdener med rigtig puls
2. **Fitnesstracking**: Pulszonesporing under strukturerede træninger
3. **Rehabilitering**: Overvåg patientindsats i VR-baseret fysioterapi

---

## ⚡ Performance

- WebSocket overhead: ~1-2ms latency på localhost
- Arduino UDP → Unity: <5ms
- Unity CPU impact: Ubetydelig (<0.1% på moderne CPU'er)
- Memory: ~2MB til WebSocket klient

---

**God fornøjelse med dit VR cykelprojekt! 🚴‍♂️🥽**

