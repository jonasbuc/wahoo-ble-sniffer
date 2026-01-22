# Unity VR Cycling - Quick Start Guide

Kom i gang med VR cykling på 5 minutter! 🚴‍♂️

**To muligheder:**
- **Option A:** 100% C# i Unity (anbefalet - simplest!)
- **Option B:** Python bridge + Unity WebSocket

---

## Option A: 100% C# Løsning (Anbefalet) ⭐

### 1. Installer Bluetooth LE Plugin i Unity

1. Åbn Unity Asset Store
2. Søg: **"Bluetooth LE for iOS, tvOS and Android"**
3. Download og importer (gratis!)

### 2. Setup Unity Scene

**Wahoo Manager:**
1. GameObject → Create Empty → Omdøb til "WahooManager"
2. Add Component → **WahooBLEManager**
3. I Inspector:
   - Kickr Name Filter: `KICKR`
   - ✅ Auto Connect
   - Enable Smoothing: ✅

**VR Bike:**
1. Tilføj din cykel model
2. Add Component → Rigidbody
3. Add Component → **VRBikeController**
4. Træk "WahooManager" til Wahoo BLE feltet

### 3. Forbered KICKR

1. Tænd for KICKR SNAP
2. **Begynd at træde** (vågner ved bevægelse)
3. macOS: Unpair fra System Settings hvis tidligere parret

### 4. Tryk Play!

Se debug overlay:
```
KICKR: ✓
Power: 150W
Cadence: 75rpm
```

✅ **Færdig!** Ingen Python, ingen eksterne scripts!

Se `README_CSHARP.md` for detaljer.

---

## Option B: Python Bridge Løsning

### 1. Installer Python Afhængigheder

```bash
cd "/Users/jonasbuchner/Blu Sniffer"
pip install -r requirements.txt
```

### 2. Forbered din KICKR

1. Tænd for KICKR SNAP
2. **Begynd at træde** (den vågner når du træder)
3. På macOS: Hvis den var parret før, unpair den:
   - System Settings → Bluetooth → KICKR SNAP → Forget Device

### 3. Start Bridge Server

```bash
cd UnityIntegration
python wahoo_unity_bridge.py
```

Du skal se:
```
✓ Devices ready!
✓ WebSocket server: ws://localhost:8765

Next steps:
1. Start Unity
2. Attach the WahooDataReceiver script to a GameObject
3. Press Play in Unity
```

### 4. Setup Unity (Første gang)

#### A. Installer NativeWebSocket Package

1. Åbn din Unity project
2. Window → Package Manager
3. Klik **+** → "Add package from git URL"
4. Indtast: `https://github.com/endel/NativeWebSocket.git#upm`
5. Klik **Add**

#### B. Tilføj Scripts

1. Træk `WahooDataReceiver.cs` til `Assets/Scripts/`
2. Træk `VRBikeController.cs` til `Assets/Scripts/`

##### C. Setup Scene

**Wahoo Data Manager:**
1. GameObject → Create Empty
2. Omdøb til "WahooData"
3. Add Component → WahooDataReceiver
4. Sæt Server URL til: `ws://localhost:8765`
5. Enable "Auto Connect"

**VR Bike:**
1. Importer din cykel model
2. Add Component → Rigidbody
3. Add Component → VRBikeController
4. Sleep i Inspector:
   - Wahoo Data → træk "WahooData" GameObject hertil
   - Bike Model → træk din cykel model hertil
   - Front Wheel → træk forhjul hertil
   - Rear Wheel → træk baghjul hertil

### 5. Test Det!

1. Sørg for Python bridge kører
2. Tryk **Play** i Unity
3. Begynd at træde på KICKR
4. Se hastighedsmåleren i Unity stige! 🚀

### 5. Test Det!

1. Sørg for Python bridge kører
2. Tryk **Play** i Unity
3. Begynd at træde på KICKR
4. Se hastighedsmåleren i Unity stige! 🚀

---

## Hvilken Option Skal Jeg Vælge?

**Option A (C#)** hvis:
- ✅ Du vil deploye til mobile/Quest
- ✅ Du vil have alt i Unity
- ✅ Du vil undgå eksterne dependencies

**Option B (Python)** hvis:
- ✅ Du allerede har Python setup
- ✅ Du logger data til database
- ✅ Du kun tester på computer

**Anbefaling:** Start med **Option A** - det er simplest! 🎯

---

## Debug Tips

Hvis det ikke virker:

**Check Console i Unity:**
```
[WahooData] ✓ Connected to Wahoo bridge!  ← God!
[WahooData] Connection failed             ← Kør Python bridge først
```

**Check Python terminal:**
```
✓ Connected to KICKR SNAP                 ← God!
No device found containing 'KICKR'        ← Træd på pedaler!
```

## Næste Skridt

Se `README.md` for:
- Avancerede features
- VR haptic feedback
- Multiplayer setup
- Performance optimization

## Data Du Får

- **Power** (W) - Din aktuelle effekt
- **Cadence** (RPM) - Pedal frekvens  
- **Speed** (km/h) - Hastighed
- **Heart Rate** (BPM) - Hvis TICKR er tilsluttet

Alle værdier er real-time med <10ms latency!

---

**Held og lykke med dit VR projekt! 🎮**
