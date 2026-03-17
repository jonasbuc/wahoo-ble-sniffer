# macOS BLE Pairing — TICKR FIT

## Problemet
Hvis din Wahoo TICKR FIT er **parret med macOS**, kan Bleak have svært ved at forbinde:
- Parrede enheder bruger tilfældige UUIDs i stedet for MAC-adresser
- De reklamerer ikke altid med deres navn
- Bleak kan ikke forbinde pålideligt mens de er parret

## Løsning: Unpair fra macOS (ANBEFALET)

1. **Åbn Systemindstillinger → Bluetooth**
2. **Find "TICKR FIT CCC1"** (eller hvad din hedder)
   - Klik ⓘ (info) ikonet
   - Klik **"Forget This Device"** / **"Glem denne enhed"**
3. **Aktivér TICKR FIT:**
   - Sæt den på — den aktiveres når elektroder rører huden
4. **Kør forbindelsestesten:**
   ```bash
   python UnityIntegration/python/ble_test_connect.py
   ```

Enheden bør nu dukke op med sit rigtige navn!

## Alternativ: Brug CoreBluetooth (macOS Native)

Hvis unpairing ikke hjælper, kan du bruge CoreBluetooth (Apples native BLE framework):

- Installer `pyobjc-framework-CoreBluetooth`
- Omskriv BLE-laget til macOS native API'er

Dette omgår parring-systemet fuldstændigt, men kræver mere opsætning.

## Fejlfinding

| Problem | Fix |
|---------|-----|
| TICKR ikke fundet | Sæt den på — elektroder skal røre huden |
| Dukker op som UUID i stedet for navn | Unpair fra Systemindstillinger |
| Forbinder men mister forbindelsen hurtigt | Flyt computer tættere på, tjek batteri |
| Dukker ikke op overhovedet | Reset Bluetooth: hold Shift+Option, klik BT-ikon → Debug → Reset |

## Anbefaling

**Start med at unpaire** — det tager 2 minutter og løser problemet i langt de fleste tilfælde.
