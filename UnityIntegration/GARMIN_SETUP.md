# 🚴 Garmin Speed Sensor 2 Setup Guide

## Hvad er dette?

Garmin Speed Sensor 2 kan nu bruges **sammen med eller i stedet for** KICKR SNAP til at måle hastighed i din Unity VR cykel simulator.

---

## 🎯 Fordele ved Garmin Speed Sensor 2

✅ **Billigere** - ~200 kr vs 7000+ kr for KICKR  
✅ **Lettere** - Lille sensor på hjulet  
✅ **Portable** - Kan bruges på enhver cykel  
✅ **Samme data** - Speed + optional cadence  
✅ **Samme latency** - Binary protocol support  

---

## 📦 Hvad du skal bruge

### Hardware:
- ✅ Garmin Speed Sensor 2 (eller Speed/Cadence bundle)
- ✅ Mac/PC med Bluetooth
- ✅ Cykel med hjul 😊

### Software:
- ✅ Python 3.11+ (INSTALL.command installer det)
- ✅ Unity 2021.3+
- ✅ Denne bridge (allerede klar!)

---

## 🚀 Quick Start

### 1. Monter Garmin Sensor

**Speed Sensor 2:**
1. Fastgør til hubben på baghjulet
2. Sensor skal sidde i midten af hjulet
3. LED skal lyse når hjulet drejes

**Cadence Sensor (optional):**
1. Fastgør til venstre crankarmen
2. Sensor skal passe til magneten
3. LED lyser ved pedaling

### 2. Installer Bridge

Første gang:
```bash
./INSTALL.command
```

### 3. Test Sensor

Tjek at sensoren virker:
```bash
# macOS
./START_GARMIN_BRIDGE.command

# Windows
START_GARMIN_BRIDGE.bat
```

**Vigtig:** Spin hjulet for at vække sensoren! LED blinker rød/grøn.

### 4. Unity Setup

Samme som før:
1. Add `BikeMovementController` til din cykel
2. Drag `WahooDataReceiver` GameObject til Wahoo Receiver field
3. Press Play ▶️

---

## 🔧 Sensor Konfiguration

### Hjul Størrelse

Garmin bruger **hjul omkreds** til at beregne hastighed.

Standard i bridge: **2.105 meter** (700x25c road bike)

Hvis din cykel har andre hjul:

**I Python (`wahoo_unity_bridge.py`):**
```python
self.wheel_circumference_m = 2.105  # Juster denne!
```

**Find din hjul omkreds:**

| Hjul Type | Omkreds (meter) |
|-----------|-----------------|
| 700x23c (racing) | 2.096 |
| 700x25c (road) | 2.105 ⭐ |
| 700x28c (comfort) | 2.136 |
| 29" MTB | 2.326 |
| 26" MTB | 2.070 |

Eller mål selv:
1. Marker dækket med kridt
2. Rul præcis 1 omdrejning
3. Mål afstand i meter

---

## 🎮 Brug Scenarios

### Scenario 1: Kun Garmin (ingen KICKR)

**Fordele:**
- ✅ Billigt setup
- ✅ Kan bruges på enhver cykel

**Begrænsninger:**
- ❌ Ingen power data (Watts)
- ✅ Men du får speed + optional cadence!

**Unity:**
```csharp
// Speed virker perfekt
float speed = wahooReceiver.Speed; // km/h fra Garmin

// Power vil være 0 (ingen KICKR)
float power = wahooReceiver.Power; // = 0

// Cadence hvis du har Cadence sensor
float cadence = wahooReceiver.Cadence; // RPM
```

### Scenario 2: Garmin + KICKR

**Fordele:**
- ✅ Dobbelt speed source (redundans)
- ✅ Power fra KICKR
- ✅ Cadence fra begge

**Hvordan det virker:**
- KICKR sender: Power + Speed + Cadence
- Garmin sender: Speed (+ optional Cadence)
- Bridge **kombinerer** data automatisk
- Unity får det bedste fra begge!

### Scenario 3: Garmin + KICKR + HR

**Ultimate setup:**
```
Garmin Speed Sensor 2 → Speed
KICKR SNAP → Power + Cadence
TICKR Armband → Heart Rate
```

All data streams samtidig! 🔥

---

## 🐛 Troubleshooting

### "Sensor not found"

**Fix:**
1. **Vækker sensoren aktiv?**
   - Spin hjulet kraftigt (10+ omdrejninger)
   - LED skal blinke rød/grøn
   - Sensor går i sleep efter 2 min uden bevægelse

2. **Bluetooth paired?**
   - Garmin sensorer skal IKKE paires i macOS Bluetooth settings
   - Bridge scanner automatisk
   - Slet evt. pairing fra System Settings

3. **Batteri dødt?**
   - CR2032 batteri holder ~1 år
   - Skift hvis sensor ikke lyser

### "Speed er forkert"

**Fix 1: Hjul omkreds forkert**
```python
# I wahoo_unity_bridge.py
self.wheel_circumference_m = 2.105  # Juster til dine hjul!
```

**Fix 2: Sensor placering**
- Skal sidde på hub (midten af hjulet)
- Ikke på eger eller fælg
- LED op eller ud (ikke ind mod cykel)

### "Unity får ingen data"

**Check:**
1. ✅ Python bridge kører? (`START_GARMIN_BRIDGE.command`)
2. ✅ Console viser "Speed: X km/h"?
3. ✅ Unity WebSocket connected? (grøn status)
4. ✅ BikeMovementController attached til cykel?

---

## 🔬 Technical Details

### BLE Service Used

Garmin sensorer bruger standard **Cycling Speed and Cadence (CSC) Service**:

```
Service UUID: 0x1816
Characteristic: 0x2A5B (CSC Measurement)

Data format:
- Flags (1 byte)
  - Bit 0: Wheel data present
  - Bit 1: Crank data present
- Cumulative Wheel Revolutions (4 bytes)
- Last Wheel Event Time (2 bytes, 1/1024 second)
- Cumulative Crank Revolutions (2 bytes)
- Last Crank Event Time (2 bytes, 1/1024 second)
```

### Speed Calculation

```python
# Wheel revolutions since last update
rev_diff = current_revs - last_revs

# Time in seconds
time_diff = (current_time - last_time) / 1024.0

# Speed in m/s
speed_ms = (rev_diff * wheel_circumference) / time_diff

# Convert to km/h
speed_kmh = speed_ms * 3.6
```

### Latency

Same as KICKR: **~5-15ms** via binary protocol!

---

## 💰 Cost Comparison

| Setup | Cost (DKK) | Features |
|-------|-----------|----------|
| **Garmin Only** | ~200 | Speed only |
| **Garmin + Cadence** | ~400 | Speed + Cadence |
| **KICKR Only** | ~7000 | Power + Speed + Cadence |
| **Garmin + KICKR** | ~7200 | Everything + redundancy |

**Anbefaling:**  
Start med Garmin (~200 kr) og se om det er nok! Opgrader til KICKR senere hvis du vil have power data.

---

## 🎯 Launcher Scripts

### Kun Garmin:
```bash
./START_GARMIN_BRIDGE.command
```
Scans kun efter Garmin (hurtigere start)

### Alt (Garmin + KICKR + HR):
```bash
./START_WAHOO_BRIDGE.command
```
Scans efter ALT - bruger hvad den finder

---

## 📝 Eksempel Session

```bash
$ ./START_GARMIN_BRIDGE.command

═══════════════════════════════════════════════════════════
  BLE to Unity Bridge (Wahoo + Garmin)
═══════════════════════════════════════════════════════════

Scanning for devices...
✓ Found: Speed Sensor 2 (12:34:56:78:9A:BC)

✓ Devices ready!
  • Garmin Speed: Speed Sensor 2

✓ WebSocket server: ws://localhost:8765

Press Ctrl+C to stop
═══════════════════════════════════════════════════════════

14:23:15 [INFO] Connecting to Speed Sensor 2...
14:23:16 [INFO] ✓ Connected to Speed Sensor 2
14:23:16 [INFO] ✓ Zero detection enabled
14:23:17 [INFO] Unity client connected from ('127.0.0.1', 52341)
14:23:18 [INFO] Speed: 15.3 km/h
14:23:19 [INFO] Speed: 18.7 km/h
14:23:20 [INFO] Speed: 22.1 km/h
```

---

## ✅ Checklist

- [ ] Garmin Speed Sensor 2 monteret
- [ ] Batteri frisk (CR2032)
- [ ] INSTALL.command kørt
- [ ] Hjul omkreds korrekt i kode
- [ ] Sensor vågen (spin hjul!)
- [ ] START_GARMIN_BRIDGE.command kører
- [ ] Unity BikeMovementController configured
- [ ] Cyklen bevæger sig i Unity! 🎉

---

## 🚀 Ready to Ride!

Nu kan du bruge din **Garmin Speed Sensor 2** til at styre hastighed i Unity VR!

Perfekt billig løsning til at komme i gang 🚴‍♂️

Hvis du senere vil have power data, tilføj bare en KICKR - bridge understøtter begge automatisk!
