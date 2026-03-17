# Unity VR Cykling - Quick Start Guide

Kom i gang med VR cykling på 5 minutter! 🚴‍♂️

**Hardware:**
- Wahoo TICKR FIT (puls via BLE)
- Arduino (hastighed, kadence, styring, bremser via UDP)

---

## Step 1: Installer Python Afhængigheder

```bash
# Fra repository root
pip install -r requirements.txt
```

---

## Step 2: Test uden hardware (anbefalet første gang)

```bash
python3 UnityIntegration/python/mock_wahoo_bridge.py
```

Du ser:
```
✓ WebSocket server: ws://localhost:8765
📡 HR: 72bpm (mock)
```

---

## Step 3: Setup Unity Scene

#### A. Installer NativeWebSocket Package

1. Åbn dit Unity projekt
2. Window → Package Manager
3. Klik **+** → "Add package from git URL"
4. Indtast: `https://github.com/endel/NativeWebSocket.git#upm`
5. Klik **Add**

#### B. Tilføj Scripts

1. Kopier `WahooDataReceiver.cs` til `Assets/Scripts/`
2. Kopier `BikeMovementController.cs` til `Assets/Scripts/`

#### C. Setup Scene

**Wahoo Data Manager:**
1. GameObject → Create Empty
2. Omdøb til "WahooData"
3. Add Component → `WahooDataReceiver`
4. Sæt Server URL til: `ws://localhost:8765`
5. Enable "Auto Connect"

**VR Bike:**
1. Importer din cykelmodel
2. Add Component → Rigidbody
3. Add Component → `BikeMovementController`
4. I Inspector:
   - Wahoo Data → træk "WahooData" GameObject hertil
   - Bike Model → træk din cykelmodel hertil

---

## Step 4: Test Det!

1. Sørg for Python bridge eller mock kører
2. Tryk **Play** i Unity
3. Tjek Console:
   ```
   [WahooData] ✓ Connected to bridge!
   [WahooData] HR: 72bpm
   ```

---

## Step 5: Kør med Rigtig Hardware

```bash
python3 UnityIntegration/python/bike_bridge.py --live
```

(Kræver TICKR FIT på + Arduino tilsluttet og kørende)

---

## Debug Tips

**Check Console i Unity:**
```
[WahooData] ✓ Connected to bridge!      ← Godt!
[WahooData] Connection failed            ← Kør Python bridge først
```

**Check Python terminal:**
```
✓ Connected to TICKR FIT                ← Godt!
Scanning... no device found             ← Sæt TICKR på (elektroderne skal røre huden)
```

---

## Data Du Får

- **Heart Rate** (BPM) — fra Wahoo TICKR FIT via BLE
- **Speed / Cadence / Steering / Brakes** — fra Arduino via UDP (direkte til Unity)

---

**Held og lykke med dit VR projekt! 🎮**
