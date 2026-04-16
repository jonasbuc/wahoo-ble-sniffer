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
python3 bridge/bike_bridge.py
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

1. Kopier `BikeController.cs` til `Assets/Scripts/`
2. Kopier `WahooWsClient.cs` til `Assets/Scripts/`
3. Kopier `ArduinoSerialReader.cs` + `GroundSensor.cs` til `Assets/Scripts/`

#### C. Setup Scene

**Cykel GameObject:**
1. Vælg dit cykel-GameObject
2. Add Component → `CharacterController`
3. Add Component → `BikeController`
4. I Inspector:
   - Ground Sensor → træk GroundSensor-objektet hertil
   - Bike Steer Steel → træk styr-objektet hertil
   - Quest Controller Transform → træk Quest-controller hertil
   - Camera Rig → træk kamera-riggen hertil
   - Arduino Serial Reader → træk ArduinoSerialReader-objektet hertil
   - Speed Multiplier: `1.0`
   - Turn Speed Modifier: prøv `60`

**Puls (valgfri) — WahooWsClient:**
1. GameObject → Create Empty → omdøb "WahooWsClient"
2. Add Component → `WahooWsClient`
3. Server URL: `ws://localhost:8765`

---

## Step 4: Test Det!

1. Sørg for Python bridge eller mock kører
2. Tryk **Play** i Unity
3. Tjek Console:
   ```
   [WahooWsClient] Connected
   ```

---

## Step 5: Kør med Rigtig Hardware

```bash
python3 bridge/bike_bridge.py --live
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
