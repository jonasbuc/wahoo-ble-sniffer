# IMPORTANT: macOS BLE Pairing Issue

## The Problem
Your Wahoo devices are **already paired** with macOS:
- TICKR FIT CCC1: `F0:13:C3:FD:EA:CB`
- KICKR SNAP C041: `C7:52:A1:6F:EB:57`

When BLE devices are paired with macOS, they:
1. Use random UUIDs instead of MAC addresses
2. Don't advertise their device names
3. Can't be easily connected to via Bleak while paired

## The Solution

### Option 1: Unpair from macOS (RECOMMENDED)

1. **Open System Settings → Bluetooth**
2. **Find "KICKR SNAP C041"**
   - Click the ⓘ (info) icon next to it
   - Click **"Forget This Device"**
3. **Find "TICKR FIT CCC1"**
   - Click the ⓘ (info) icon next to it
   - Click **"Forget This Device"**
4. **Activate your devices:**
   - Wear the TICKR (it needs to detect your heartbeat)
   - Start pedaling on the KICKR
5. **Run the logger:**
   ```bash
   python wahoo_ble_logger.py --show-all-devices
   ```

The devices should now appear with their actual names!

### Option 2: Use CoreBluetooth (macOS Native)

If unpairing doesn't work, use CoreBluetooth (Apple's native BLE framework) instead of Bleak:

- Install `pyobjc-framework-CoreBluetooth`
- Rewrite the BLE layer to use macOS native APIs

This approach bypasses the pairing system entirely but requires more setup.

### Option 3: Try the MAC addresses directly

Even though macOS may use random UUIDs, it is worth trying:

```bash
python wahoo_ble_logger.py --tickr-address F0:13:C3:FD:EA:CB --kickr-address C7:52:A1:6F:EB:57
```

This may not work while the devices are still paired, but costs nothing to attempt.

## Recommendation

**Start with Option 1 (unpair)** — it takes about 2 minutes and resolves the
issue in the vast majority of cases.  Only consider Option 2 (CoreBluetooth) if
Option 1 doesn't help after a fresh scan.
