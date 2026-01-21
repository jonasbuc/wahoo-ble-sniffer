# 🚴 Unity VR Bike Setup Guide

## Problem: "Jeg får data, men objektet rykker sig ikke"

**Løsning:** Du skal bruge dataen til at flytte objektet!

`WahooDataReceiver` modtager kun data - den flytter ikke noget selv.
Du skal bruge et **movement controller script** til at læse dataen og flytte cyklen.

---

## 🎯 Quick Setup (5 minutter)

### 1. Import Scripts til Unity

Kopier disse 2 filer til din Unity projekt `Assets/Scripts/` mappe:
- ✅ `WahooDataReceiver_Optimized.cs`
- ✅ `BikeMovementController.cs` ← NY!

### 2. Setup Scene

#### A. Opret Data Receiver GameObject

1. I Unity Hierarchy: Højreklik → Create Empty
2. Navngiv: `WahooDataReceiver`
3. Add Component → `WahooDataReceiver` script
4. Indstillinger:
   - Server URL: `ws://localhost:8765`
   - Auto Connect: ✅
   - Use Binary Protocol: ✅
   - Instant Zero Detection: ✅

#### B. Tilføj Movement til din Cykel

1. Vælg dit **cykel/spiller GameObject** i Hierarchy
2. Add Component → `BikeMovementController` script
3. Indstillinger:
   - Wahoo Receiver: Træk `WahooDataReceiver` GameObject hertil
   - Speed Multiplier: `0.5` (juster senere!)
   - Show Debug Info: ✅

### 3. Start Bridge & Test

1. **Start Python bridge:**
   ```bash
   # Test med mock data (uden hardware)
   ./START_MOCK_BRIDGE.command
   ```

2. **Start Unity:**
   - Press Play ▶️
   - Du skulle se i Console: `✓ Connected to Wahoo bridge!`
   - Cyklen burde begynde at bevæge sig!

3. **Se debug info:**
   - Speed/Cadence/Power vises øverst til venstre på skærmen
   - Console viser: `[BikeMovement] Moving at X m/s`

---

## ⚙️ Indstillinger

### Speed Multiplier

Denne værdi kontrollerer hvor hurtigt cyklen bevæger sig i Unity:

- **0.5** = Realistisk (20 km/h i virkeligheden = langsom bevægelse i Unity)
- **1.0** = Standard (1:1 forhold)
- **2.0** = Dobbelt hastighed (20 km/h føles som 40 km/h)

**Tip:** Start med 0.5 og juster indtil det føles rigtigt!

### Movement Methods

`BikeMovementController` understøtter 3 metoder:

#### 1. Transform Movement (Standard)
```
Use Rigidbody: ☐
```
- Simpel transform.position bevægelse
- Virker altid
- God til start

#### 2. CharacterController
```
(Tilføj CharacterController component til cyklen)
```
- Bedre kollision detection
- Automatisk gravity
- God til first-person

#### 3. Rigidbody Physics
```
Use Rigidbody: ✅
(Tilføj Rigidbody component til cyklen)
```
- Fuld fysik simulation
- Momentum og inerti
- God til realistisk følelse

---

## 🎨 Hjul Rotation (Visuelt)

For at rotere hjulene baseret på hastighed:

1. Find dine hjul GameObjects i Hierarchy
2. I BikeMovementController:
   - Front Wheel: Træk front hjul hertil
   - Rear Wheel: Træk bag hjul hertil
   - Wheel Rotation Multiplier: `100` (juster hvis de drejer for hurtigt/langsomt)

---

## 🐛 Troubleshooting

### "Cyklen bevæger sig ikke"

**Check 1: Er WahooDataReceiver connected?**
```
Console burde vise:
✓ Connected to Wahoo bridge!
```
Hvis ikke:
- Er Python bridge startet? (START_MOCK_BRIDGE.command)
- Kører den på port 8765?

**Check 2: Kommer data ind?**
```
Console burde vise:
[BikeMovement] Speed: 15.3 km/h | Cadence: 75 rpm | Power: 120 W
```
Hvis ikke:
- Er BikeMovementController.wahooReceiver sat korrekt?
- Er "Show Debug Info" enabled?

**Check 3: Er speedMultiplier for lav?**
```
Hvis Speed er 20 km/h men multiplier er 0.1:
→ moveSpeed = (20/3.6) * 0.1 = 0.55 m/s (meget langsom!)

Prøv højere værdi som 1.0 eller 2.0
```

### "Cyklen fortsætter selv når jeg stopper"

✅ **FIKSET i version 2.0!**

Zero detection er nu enabled:
- Python sender zeros efter 1.2s uden pedal activity
- Unity snapper instantly til zero (ingen smoothing ved stop)

Hvis det stadig sker:
- Check at `Instant Zero Detection` er ✅ i WahooDataReceiver
- Kør seneste version af bridge

### "Hastigheden er mærkelig"

Unity bruger **meters per second**, KICKR sender **km/h**.

Konvertering:
```csharp
float speedKmh = 20f;  // Fra KICKR
float speedMs = speedKmh / 3.6f;  // = 5.55 m/s
float moveSpeed = speedMs * speedMultiplier;
```

Juster `speedMultiplier` indtil det føles rigtigt!

---

## 🎮 Avanceret: Brug Cadence til Animationer

```csharp
using UnityEngine;

public class PedalAnimator : MonoBehaviour
{
    public BikeMovementController bikeController;
    public Transform leftPedal;
    public Transform rightPedal;
    
    void Update()
    {
        float cadence = bikeController.GetCurrentCadence(); // RPM
        
        // Konverter RPM til rotation speed
        float rotationSpeed = cadence * 6f; // 360° / 60s
        
        // Roter pedalerne
        leftPedal.Rotate(rotationSpeed * Time.deltaTime, 0, 0);
        rightPedal.Rotate(rotationSpeed * Time.deltaTime + 180f, 0, 0);
    }
}
```

---

## 🎮 Avanceret: Brug Power til Sværhedsgrad

```csharp
using UnityEngine;

public class ResistanceSimulator : MonoBehaviour
{
    public BikeMovementController bikeController;
    
    void Update()
    {
        float power = bikeController.GetCurrentPower(); // Watts
        
        // Højere power = hårdere at cykle
        if (power < 100)
        {
            // Let terræn
            bikeController.speedMultiplier = 1.0f;
        }
        else if (power < 200)
        {
            // Medium terræn
            bikeController.speedMultiplier = 0.7f;
        }
        else
        {
            // Bakke - høj power men lavere hastighed
            bikeController.speedMultiplier = 0.4f;
        }
    }
}
```

---

## 📊 Data Reference

Fra `WahooDataReceiver.CyclingData`:

| Property | Type | Unit | Range | Description |
|----------|------|------|-------|-------------|
| `speed` | float | km/h | 0-100 | Hjul hastighed |
| `cadence` | float | RPM | 0-180 | Pedal omdrejninger |
| `power` | int | Watts | 0-1000+ | Kraft på pedalerne |
| `heart_rate` | int | BPM | 0-220 | Puls (hvis HR armband tilsluttet) |
| `timestamp` | double | seconds | - | Unix timestamp |

---

## ✅ Test Checklist

- [ ] Python bridge kører (START_MOCK_BRIDGE.command)
- [ ] Unity Console viser "Connected to Wahoo bridge!"
- [ ] Debug info viser Speed/Cadence/Power øverst på skærmen
- [ ] Grøn "● Connected" status vises
- [ ] Cyklen bevæger sig når Speed > 0
- [ ] Cyklen stopper instant når Speed = 0
- [ ] Hjul roterer (hvis configured)

---

## 🚀 Næste Skridt

1. **Test med mock data først** - sørg for alt virker
2. **Test med rigtig KICKR** - start på cyklen og brug START_WAHOO_BRIDGE.command
3. **Juster speedMultiplier** til det føles naturligt
4. **Tilføj hjul rotation** for visuel feedback
5. **Byg din VR verden** omkring cyklen!

---

## 💡 Tips

- Start altid med **mock data** for hurtig iteration
- Brug **Debug Info** til at se hvad der sker
- Juster **Speed Multiplier** indtil det føles rigtigt
- Overvej **CharacterController** for bedre kollision
- Brug **Cadence** til pedal animationer
- Brug **Power** til sværhedsgrad/modstand simulation

---

Held og lykke med din VR cykel simulator! 🚴‍♂️🎮
