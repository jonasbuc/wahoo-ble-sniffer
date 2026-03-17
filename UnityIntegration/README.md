# Unity VR Bike Integration

Stream live cykeldata til Unity VR via en Wahoo TICKR FIT pulsmonitor (BLE) og en Arduino for hastighed / kadence / styring / bremser.

## ⚡ Arkitektur

```
Wahoo TICKR FIT  ──BLE──►  bike_bridge.py  ──WS──►  Unity (WahooWsClient.cs)
Arduino          ──UDP──►  bike_bridge.py           BikeMovementController.cs
                                    │
                                    └──► collector_tail.py ──► SQLite / Parquet
```

- **Puls**: Wahoo TICKR FIT via Bluetooth LE (Bleak)
- **Cykeldata** (hastighed, kadence, styring, bremser): Arduino via UDP
- **Unity klient**: WebSocket modtager binære frames og styrer VR-scenen

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

1. Kopier `WahooDataReceiver.cs` til `Assets/Scripts/`
2. Kopier `BikeMovementController.cs` til `Assets/Scripts/`
3. Opret et tomt GameObject i din scene: **GameObject → Create Empty**
4. Omdøb det til "WahooData"
5. Tilføj `WahooDataReceiver` komponenten
6. Tilføj din cykelmodel til scenen
7. Tilføj `BikeMovementController` til cyklen
8. Sæt WahooDataReceiver-referencen i Inspector

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

Tryk **Play** i Unity. `WahooDataReceiver` forbinder automatisk til broen.

Tjek Console:
```
[WahooData] ✓ Connected to bridge!
[WahooData] HR: 142bpm
```

### Step 6: Test uden hardware (mock bridge)

```bash
python UnityIntegration/python/bike_bridge.py
```

Genererer realistiske fake sensordata på samme WebSocket interface.

---

## 🎮 Brug af Data i dit VR Spil

### Basis eksempel: Adgang til aktuelle værdier

```csharp
using UnityEngine;

public class MyVRGame : MonoBehaviour
{
    private WahooDataReceiver wahooData;

    void Start()
    {
        wahooData = FindObjectOfType<WahooDataReceiver>();
    }

    void Update()
    {
        if (wahooData.IsConnected)
        {
            int heartRate = wahooData.HeartRate;  // BPM fra TICKR FIT
            // Cykeldataene (speed, cadence, steering, brakes) kommer fra Arduino
        }
    }
}
```

### Event-drevet opdatering

```csharp
void Start()
{
    wahooData = FindObjectOfType<WahooDataReceiver>();
    wahooData.OnDataReceived += HandleNewData;
}

void HandleNewData(WahooDataReceiver.CyclingData data)
{
    Debug.Log($"HR: {data.heart_rate} bpm");
}
```

---

## 🔧 Konfiguration

### WahooDataReceiver Indstillinger

| Indstilling | Beskrivelse | Standard |
|-------------|-------------|---------|
| Server URL | WebSocket adresse | `ws://localhost:8765` |
| Auto Connect | Forbind ved Start() | ✅ Aktiveret |
| Reconnect Delay | Sekunder mellem genforbindelsesforsøg | 3.0s |
| Enable Smoothing | Udglatning af hurtige værdiskift | ✅ Aktiveret |
| Smoothing Factor | 0 = ingen udglatning, 1 = max | 0.3 |

---

## 📊 Dataformat

WebSocket sender JSON-beskeder ved hver ny HR-opdatering:

```json
{
  "timestamp": 1704067200.123,
  "power": 0.0,
  "cadence": 0.0,
  "speed": 0.0,
  "heart_rate": 145
}
```

| Felt | Type | Enhed | Beskrivelse |
|------|------|-------|-------------|
| timestamp | float | sekunder | Unix timestamp |
| power | float | W | Altid 0.0 (Arduino ikke power-sensor) |
| cadence | float | RPM | Altid 0.0 (kommer fra Arduino separat) |
| speed | float | km/h | Altid 0.0 (kommer fra Arduino separat) |
| heart_rate | int | BPM | Puls fra TICKR FIT |

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
- Øg **Smoothing Factor** i WahooDataReceiver (prøv 0.5)
- Localhost latency burde være <1ms

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
│   ├── WahooDataReceiver.cs        #   WebSocket klient (bridge → Unity)
│   ├── WahooDataReceiver_Optimized.cs
│   └── BikeMovementController.cs   #   Cykelbevægelse fra sensordata
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

