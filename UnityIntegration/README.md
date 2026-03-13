# Wahoo to Unity VR Integration

Stream live data fra dine Wahoo BLE-enheder (trainers, speed/cadence sensors og TICKR) til Unity for VR cycling simulations.

## ⚡ TL;DR - Hvad Virker NU

✅ **Python WebSocket Bridge** - 100% testet og verified  
⚠️ **C# Unity BLE** - Kræver ekstra plugin (ikke testet endnu)

**Min anbefaling:** Start med Python bridge! Se [VERIFICATION.md](VERIFICATION.md) for bewis.

---

## 🎯 To Løsninger

### Option A: Python WebSocket Bridge (ANBEFALET) ⭐

**Status:** ✅ Verificeret working

**Fordele:**
- Stream **real-time power, cadence, and speed** from power-capable trainers to Unity
- Stream **heart rate** from TICKR (optional)
- Control a VR bike in Unity using actual cycling data
- Build immersive VR cycling experiences with real physical input

## 📋 Requirements

### Python Side (Data Bridge)
- Python 3.11+
- Bleak library for BLE
- WebSockets library for Unity communication
- macOS/Windows/Linux with Bluetooth

### Unity Side
- Unity 2021.3+ (LTS recommended)
- NativeWebSocket package for WebSocket client
- VR headset (Meta Quest, Valve Index, etc.) - optional but recommended

## 🚀 Quick Start

### Step 1: Install Python Dependencies

```bash
pip install bleak websockets
```

### Step 2: Install Unity Package

1. Open your Unity project
2. Open Package Manager (Window → Package Manager)
3. Click the **+** button → "Add package from git URL"
4. Enter: `https://github.com/endel/NativeWebSocket.git#upm`
5. Click **Add**

### Step 3: Add Scripts to Unity

1. Copy `WahooDataReceiver.cs` and `VRBikeController.cs` to your Unity project's `Assets/Scripts/` folder
2. Create an empty GameObject in your scene: **GameObject → Create Empty**
3. Rename it to "WahooData"
4. Add the `WahooDataReceiver` component to it
5. Add your bike model to the scene
6. Add the `VRBikeController` component to your bike
7. Assign the WahooDataReceiver reference in the Inspector

### Step 4: Start the Bridge (recommended)

Make sure your trainer/sensor is on and you're pedaling (many devices wake up when pedaling starts).

Preferred: use the provided start scripts which launch the bridge and, optionally, the GUI monitor.

- On macOS: double-click `UnityIntegration/starters/START_WAHOO_BRIDGE.command` — this will spawn two Terminal windows: the GUI (in a new window) and the bridge (in the current window). Both processes receive the `--live` flag by default.
- On Windows: double-click `UnityIntegration/starters/START_WAHOO_BRIDGE.bat` — the GUI opens in its own window and the bridge runs in the main window; both are started with the `--live` flag.

If you prefer to run the bridge directly from the terminal, you can still run:

```bash
# from the repository root
python UnityIntegration/python/wahoo_unity_bridge.py --live
```

You should see:

```
Scanning for trainer/sensor...
Found trainer at C7:52:A1:6F:EB:57
✓ Devices ready!
✓ WebSocket server: ws://localhost:8765

Next steps:
1. Start Unity
2. Attach the WahooDataReceiver script to a GameObject
3. Press Play in Unity
```

### Step 5: Run Unity

Press **Play** in Unity. The WahooDataReceiver will automatically connect to the bridge.

Check the Console for:
```
[WahooData] ✓ Connected to Wahoo bridge!
[WahooData] Power: 150W | Cadence: 75rpm | Speed: 25.3km/h | HR: 142bpm
```

## 🎮 Using the Data in Your VR Game

### Basic Example: Access Current Values

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
            float power = wahooData.Power;        // Watts
            float cadence = wahooData.Cadence;    // RPM
            float speed = wahooData.Speed;        // km/h
            int heartRate = wahooData.HeartRate;  // BPM

            // Use these values to control your game!
        }
    }
}
```

### Advanced Example: Event-Driven Updates

```csharp
void Start()
{
    wahooData = FindObjectOfType<WahooDataReceiver>();
    wahooData.OnDataReceived += HandleNewData;
}

void HandleNewData(WahooDataReceiver.CyclingData data)
{
    Debug.Log($"New power: {data.power}W");
    
    // Trigger effects based on power zones
    if (data.power > 200)
    {
        ActivateHighIntensityEffect();
    }
}
```

### Example: Physics-Based Movement

The included `VRBikeController.cs` shows how to:
- Use real speed data to move the bike in VR
- Animate wheels based on actual speed
- Adjust audio pitch/volume based on cadence and power
- Smoothly interpolate values for natural feel

## 🔧 Configuration

### WahooDataReceiver Settings

| Setting | Description | Default |
|---------|-------------|---------|
| Server URL | WebSocket address | `ws://localhost:8765` |
| Auto Connect | Connect on Start() | ✅ Enabled |
| Reconnect Delay | Seconds between reconnect attempts | 3.0s |
| Enable Smoothing | Smooth rapid value changes | ✅ Enabled |
| Smoothing Factor | 0 = no smoothing, 1 = max | 0.3 |

### VRBikeController Settings

| Setting | Description | Default |
|---------|-------------|---------|
| Max Speed | Speed limit in km/h | 50 km/h |
| Acceleration | Speed increase rate | 2.0 |
| Deceleration | Speed decrease rate | 3.0 |
| Wheel Radius | For rotation animation | 0.35m |

## 📊 Data Format

The WebSocket sends JSON messages every time new data arrives from the cycling device:

```json
{
  "timestamp": 1704067200.123,
  "power": 180,
  "cadence": 85.5,
  "speed": 28.3,
  "heart_rate": 145
}
```

| Field | Type | Unit | Description |
|-------|------|------|-------------|
| timestamp | float | seconds | Unix timestamp |
| power | int | W | Instantaneous power |
| cadence | float | RPM | Pedaling cadence |
| speed | float | km/h | Current speed |
| heart_rate | int | BPM | Heart rate (if TICKR connected) |

## 🐛 Troubleshooting

### "No device found containing the target name"

- Make sure your sensor/trainer is powered on
- **Start pedaling** (many devices wake up when they detect movement)
- On macOS: unpair from System Settings if previously paired
- Run `python python/quick_find.py` from parent directory to verify device is visible

### "WebSocket connection failed" in Unity

- Make sure the Python bridge is running first
- Check firewall settings - allow localhost connections
- Verify port 8765 is not in use by another application
- Check Unity Console for specific error messages

### Data seems delayed or jumpy

- Increase **Smoothing Factor** in WahooDataReceiver (try 0.5)
- Check network latency (though localhost should be <1ms)
- Reduce Unity frame rate if very high (cap at 90 FPS for VR)

### Device disconnects frequently

- Check Bluetooth range - keep your sensor/trainer within 5 meters of computer
- Remove interference - turn off other Bluetooth devices
- On macOS: reset Bluetooth module (hold Shift+Option, click BT icon, Debug → Reset)

## 🎨 VR Integration Tips

### 1. **Haptic Feedback**
Use power data to drive controller vibration:
```csharp
if (wahooData.Power > 250)
{
    // High power - strong vibration
    OVRInput.SetControllerVibration(1, 0.8f, OVRInput.Controller.RTouch);
}
```

### 2. **Visual Effects**
Create sweat particles based on heart rate zones:
```csharp
float hrPercent = wahooData.HeartRate / 180f; // Assuming max HR = 180
sweatParticles.emissionRate = hrPercent * 100;
```

### 3. **Difficulty Scaling**
Adjust game difficulty based on actual power output:
```csharp
float normalizedPower = wahooData.GetNormalizedPower(maxPower: 300f);
hillSteepness = Mathf.Lerp(0f, 15f, normalizedPower);
```

### 4. **Multiplayer Sync**
Broadcast power data over network for multiplayer races:
```csharp
photonView.RPC("UpdateRiderPower", RpcTarget.All, wahooData.Power);
```

## 📁 Project Structure

```
UnityIntegration/
├── python/                         # Python bridge scripts
│   ├── wahoo_unity_bridge.py       #   Production BLE → WebSocket bridge
│   ├── mock_wahoo_bridge.py        #   Mock server (no hardware needed)
│   ├── wahoo_bridge_gui.py         #   Tkinter status monitor + live HR graph
│   ├── collector_tail.py           #   VRSF binary tail → SQLite + Parquet
│   └── db/                         #   DB utilities
│       ├── create_readable_views.py
│       ├── export_readable_views.py
│       ├── pretty_dump_db.py
│       ├── validate_db.py
│       └── SQL_CHEATSHEET.md
│
├── unity/                          # Unity C# controller scripts
│   ├── WahooBLEManager.cs          #   Direct BLE (Shatalmic plugin)
│   ├── WahooDataReceiver.cs        #   WebSocket client (bridge → Unity)
│   ├── WahooDataReceiver_Optimized.cs
│   ├── BikeMovementController.cs   #   Bike movement from speed data
│   └── VRBikeController.cs         #   VR bike with Rigidbody + audio
│
├── Assets/VrsLogging/              # VRSF session-logging library
│   ├── VrsSessionLogger.cs         #   Orchestrates all writers
│   ├── VrsFormats.cs               #   Binary record layouts
│   ├── VrsCrc32.cs                 #   CRC32 (IEEE 802.3)
│   ├── VrsFileWriterFixed.cs       #   Fixed-size stream writer
│   ├── VrsFileWriterEvents.cs      #   Variable events writer
│   ├── SessionManagerUI.cs         #   Unity UI for sessions
│   └── SessionHistoryRow.cs        #   History row prefab component
│
├── UnityClient/                    # WahooWsClient.cs (low-level WS)
├── starters/                       # One-click launchers (.command/.bat/.ps1)
├── scripts/                        # Shell helpers
└── docs/                           # All guides
    ├── QUICKSTART.md               #   5-min setup guide
    ├── OVERSIGT.md                 #   High-level overview (Danish)
    ├── UNITY_SETUP_GUIDE.md        #   Scene setup + movement guide
    ├── README_VRS.md               #   VRSF format + collector guide
    ├── README_CSHARP.md            #   Full C# BLE setup guide
    ├── SESSION_HISTORY.md          #   Session history UI wiring
    ├── VERIFICATION.md             #   What is tested and verified
    └── START_HER.md                #   Danish entry point
```

## 🔗 Related Files

- `../wahoo_ble_logger.py` — Standalone Python BLE logger with SQLite (no WebSocket)
- `../WahooBleLoggerCSharp/` — C# BLE logger (.NET 8 — no Python required)
- `../docs/PAIRING_HELP.md` — Bluetooth pairing troubleshooting (macOS)

## 💡 Example Use Cases

1. **VR Cycling Game**: Players ride through virtual worlds at their actual speed
2. **Fitness App**: Track power zones and heart rate during structured workouts
3. **Multiplayer Racing**: Compete with friends using real bike data
4. **Training Simulation**: Visualize climbs and descents with accurate resistance
5. **Rehabilitation**: Monitor patient effort in VR-based physical therapy

## 🚴 Hardware Setup Tips

1. Position your VR headset near your trainer/sensor for best tracking
2. Use a fan - VR + cycling = hot! 🔥
3. Keep a towel nearby for the headset
4. Use over-ear headphones or VR headset audio
5. Ensure good ventilation in your play space

## ⚡ Performance Notes

- WebSocket overhead: ~1-2ms latency on localhost
- Data rate: ~10-20 messages/second (depends on device update rate)
- Unity CPU impact: Negligible (<0.1% on modern CPUs)
- Memory: ~2MB for WebSocket client
- VR headroom: 60+ FPS with proper optimization

## 📝 License

Same as parent project - use freely for personal or commercial VR projects!

## 🤝 Contributing

Found a bug or have an improvement? Open an issue in the main repo!

---

**Happy VR cycling! 🚴‍♂️🥽**
