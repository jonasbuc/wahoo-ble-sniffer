# 🚴 Unity VR Bike Setup Guide

## Arkitektur

```
TICKR armband  ──BLE──► bike_bridge.py  ──WS──► WahooWsClient.cs  (puls)
Arduino        ──Serial──►                        ArduinoSerialReader.cs (hastighed)
                                                        ↓
                                                  BikeController.cs  (bevægelse)
```

`BikeController` læser hastighed direkte fra `ArduinoSerialReader` og styring fra Quest-controlleren.
`WahooWsClient` bruges separat til at modtage puls fra broen — de to scripts er uafhængige.

---

## 🎯 Quick Setup (5 minutter)

### 1. Import Scripts til Unity

Kopier disse filer til din Unity projekt `Assets/Scripts/` mappe:
- ✅ `BikeController.cs` — bevægelse + styring
- ✅ `WahooWsClient.cs` — puls fra Python-bro (valgfri)
- ✅ `ArduinoSerialReader.cs` — seriel hastighed fra Arduino
- ✅ `GroundSensor.cs` — grounds-check

### 2. Setup Scene

#### A. Cykel GameObject

1. Vælg dit **cykel/spiller GameObject** i Hierarchy
2. Add Component → `CharacterController`
3. Add Component → `BikeController`
4. Indstillinger i Inspector:
   - **Character Controller**: trækkes auto-ind hvis på samme GO
   - **Ground Sensor**: træk dit GroundSensor-objekt hertil
   - **Bike Steer Steel**: træk styr-objektet hertil
   - **Quest Controller Transform**: træk Quest-controller Transform hertil
   - **Camera Rig**: træk kamera-riggen hertil
   - **Arduino Serial Reader**: træk dit ArduinoSerialReader-objekt hertil
   - **Speed Multiplier**: `1.0` (juster efter smag)
   - **Turn Speed Modifier**: prøv `60`

#### B. Arduino Serial Reader

1. Opret tomt GameObject: `ArduinoSerialReader`
2. Add Component → `ArduinoSerialReader`
3. Sæt den korrekte COM-port / device path

#### C. Puls (valgfri) — WahooWsClient

1. Opret tomt GameObject: `WahooWsClient`
2. Add Component → `WahooWsClient`
3. Server URL: `ws://localhost:8765`
4. Subscribe til `OnHeartRate` event i dit eget UI-script

### 3. Start Bridge & Test

1. **Start Python bridge (mock uden hardware):**
   ```bash
   ./starters/START_MOCK_BRIDGE.command
   ```

2. **Start Unity → Press Play ▶️**
   - Console: `[WahooWsClient] Connected` (puls)
   - Cyklen skal bevæge sig når Arduino sender hastighed > 0

---

## ⚙️ Indstillinger

| Felt | Type | Beskrivelse |
|------|------|-------------|
| `speedMultiplier` | float | Skalér hastighed. `1.0` = 1:1 |
| `turnSpeedModifier` | float | Styrefølsomhed. Prøv `60` |
| `gravity` | float | Tyngdekraft ved luft (standard `-9.81`) |

### Bevægelseslogik

```
speed > 0.3 m/s  →  CharacterController.Move(forward * speed * multiplier)
                    + rotation fra Quest-controller cross-produkt
speed ≤ 0.3      →  ingen rotation, men stadig gravity
```

---

## 🐛 Troubleshooting

### "Cyklen bevæger sig ikke"
- Er `ArduinoSerialReader` tilsluttet og sender data?
- Er `ArduinoSerialReader`-feltet sat i `BikeController` Inspector?
- Er `CharacterController` på samme GameObject?

### "Styring virker ikke"
- Er `questControllerTransform` sat?
- Prøv at øge `turnSpeedModifier`
- `speed` skal være > 0.3 for rotation

### "Puls vises ikke"
- Er Python bridge startet? (`START_MOCK_BRIDGE.command`)
- Er `WahooWsClient` i scenen og URL korrekt (`ws://localhost:8765`)?

---

## ✅ Test Checklist

- [ ] Arduino sender hastighed (Console / Debug)
- [ ] `BikeController` er på cykel-objektet med alle refs sat
- [ ] Cyklen bevæger sig fremad med fart
- [ ] Styring responderer på Quest-controller
- [ ] (Valgfri) `WahooWsClient` viser puls fra bridge

---

## 🚀 Næste Skridt

1. **Test med mock bridge** — `START_MOCK_BRIDGE.command`
2. **Test med rigtig TICKR** — `START_WAHOO_BRIDGE.command`
3. **Juster `speedMultiplier`** til naturlig følelse
4. **Juster `turnSpeedModifier`** til behagelig styring
5. **Byg din VR verden** omkring cyklen!

---

Held og lykke med din VR cykel! 🚴‍♂️🎮
