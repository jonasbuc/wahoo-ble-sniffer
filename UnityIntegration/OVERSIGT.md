# Unity Integration Oversigt 🎮

**To måder at forbinde Wahoo enheder til Unity:**

## 📋 Filer Overview

### Option A: 100% C# i Unity (Anbefalet) ⭐

| Fil | Beskrivelse |
|-----|-------------|
| `WahooBLEManager.cs` | Direkte Bluetooth LE forbindelse i Unity |
| `VRBikeController.cs` | VR cykel controller (opdateret til BLE manager) |
| `README_CSHARP.md` | Komplet guide til C# løsning |

**Fordele:**
- ✅ Alt kører i Unity - ingen eksterne scripts
- ✅ Deploy til Android/iOS/Quest direkte
- ✅ Native performance
- ✅ Simplere setup

**Ulemper:**
- ⚠️ Kræver Bluetooth LE Unity plugin (gratis på Asset Store)
- ⚠️ Platform-specific permissions (Android/iOS)

### Option B: Python Bridge + WebSocket

| Fil | Beskrivelse |
|-----|-------------|
| `wahoo_unity_bridge.py` | Python BLE → WebSocket server |
| `WahooDataReceiver.cs` | Unity WebSocket client |
| `VRBikeController.cs` | VR cykel controller (original version) |
| `README.md` | Komplet guide til WebSocket løsning |

**Fordele:**
- ✅ Kan også logge til database samtidig
- ✅ Nemmere debugging af BLE forbindelse
- ✅ Kan køre på separat computer

**Ulemper:**
- ⚠️ Kræver Python runtime
- ⚠️ To programmer skal køre samtidig
- ⚠️ Virker kun på computer (ikke mobile builds)

## 🚀 Quick Start

### Bruger du Unity alene?
→ Følg **QUICKSTART.md** → Option A (C#)

### Har du Python setup og vil logge data?
→ Følg **QUICKSTART.md** → Option B (Python Bridge)

## 📁 Fil Struktur

```
UnityIntegration/
│
├── 🎯 C# LØSNING (Anbefalet)
│   ├── WahooBLEManager.cs          # BLE manager til Unity
│   ├── VRBikeController.cs         # VR bike controller
│   └── README_CSHARP.md            # C# guide
│
├── 🐍 PYTHON LØSNING
│   ├── wahoo_unity_bridge.py       # Python WebSocket bridge
│   ├── WahooDataReceiver.cs        # Unity WebSocket client
│   └── README.md                   # WebSocket guide
│
├── 📚 DOKUMENTATION
│   ├── QUICKSTART.md               # Hurtig start (begge options)
│   ├── OVERSIGT.md                 # Denne fil
│   └── package.json                # Unity package manifest
│
└── 📦 DEPENDENCIES
    └── (se README filer for specifik option)
```

## 🔌 Teknisk Sammenligning

| Feature | C# Løsning | Python Løsning |
|---------|------------|----------------|
| **Setup Kompleksitet** | Medium | Lav |
| **Runtime Dependencies** | Unity BLE plugin | Python + libraries |
| **Latency** | ~20-50ms | ~50-100ms |
| **Platform Support** | Android, iOS, Windows, macOS | macOS, Windows, Linux |
| **Mobile/Quest Deploy** | ✅ Ja | ❌ Nej |
| **Database Logging** | ⚠️ Tilføj selv | ✅ Built-in |
| **Debug Overlay** | ✅ Built-in | ✅ Built-in |
| **Auto-reconnect** | ✅ Ja | ✅ Ja |

## 🎯 Use Case Recommendations

### VR Spil til Quest/Mobile
**→ Brug C# Løsning**
- Native deployment
- Ingen eksterne dependencies
- Best performance

### Desktop VR med Data Logging
**→ Brug Python Løsning**
- Kan logge til SQLite samtidig
- Lettere at debugge BLE issues
- Kan køre på separat computer

### Multiplayer/Cloud Gaming
**→ Brug Python Løsning**
- Kan sende data til cloud server
- Lettere at integrere med backends
- Kan stream til flere clients

### Trænings Apps (Mobile)
**→ Brug C# Løsning**
- Deploy direkte til mobile
- Offline support
- App Store ready

## 🛠️ Opsætning Per Option

### Option A (C#) - 3 Steps

1. **Installer Bluetooth LE plugin** i Unity
2. **Tilføj WahooBLEManager.cs** til scene
3. **Tryk Play** og se data!

### Option B (Python) - 4 Steps

1. **Installer Python packages:** `pip install -r requirements.txt`
2. **Kør bridge:** `python wahoo_unity_bridge.py`
3. **Tilføj WahooDataReceiver.cs** til Unity scene
4. **Tryk Play** i Unity

## 🔗 Data Flow

### C# Løsning:
```
KICKR SNAP ─BLE─> Unity (WahooBLEManager) ─> VRBikeController ─> VR Scene
     ↑                                              ↓
     └──────────────────────────────────────────────┘
         Real-time Bluetooth LE (20-50ms latency)
```

### Python Løsning:
```
KICKR SNAP ─BLE─> Python Bridge ─WebSocket─> Unity (WahooDataReceiver) ─> VRBikeController ─> VR Scene
     ↑                 ↓                                                           ↓
     │              SQLite DB                                                VR Scene
     └────────────────────────────────────────────────────────────────────────┘
         BLE (20ms) + WebSocket (30ms) = ~50-100ms total latency
```

## 📊 Data Format (Begge Løsninger)

```csharp
public class CyclingData
{
    public double timestamp;      // Unix timestamp / Unity time
    public int power;             // Watts (0-1500)
    public float cadence;         // RPM (0-150)
    public float speed;           // km/h (0-80)
    public int heart_rate;        // BPM (40-220)
}
```

## 🆘 Support

**C# Løsning problemer?**
→ Se `README_CSHARP.md` troubleshooting sektion

**Python Løsning problemer?**
→ Se `README.md` troubleshooting sektion

**Generelle BLE issues?**
→ Se `../PAIRING_HELP.md` i parent directory

## 🎓 Læringssti

1. **Start Simple:** Test C# løsning i Unity Editor
2. **Test VR:** Tilføj XR Interaction Toolkit
3. **Add Features:** Haptics, audio, visual effects
4. **Polish:** UI, menus, settings
5. **Deploy:** Build til din target platform

## 💡 Pro Tips

- **Test i Editor først** - begge løsninger virker i Unity Editor
- **Brug Smoothing** - sæt smoothing factor til 0.3-0.5 for naturlig følelse
- **Debug Overlay** - begge scripts har built-in debug display
- **Auto-reconnect** - begge håndterer disconnects automatisk

## 🏆 Anbefaling

**Ny til Unity VR?**
→ Start med **C# løsning** - alt i ét program

**Erfaren Unity dev + vil logge data?**
→ Brug **Python løsning** - mere fleksibel

**Building for Quest?**
→ **KUN C# løsning** virker på mobile

---

**God fornøjelse med dit VR cykel projekt! 🚴‍♂️🥽**
