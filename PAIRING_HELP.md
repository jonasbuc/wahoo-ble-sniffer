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

If unpairing doesn't work, we need to use CoreBluetooth (Apple's BLE framework) instead of Bleak. This requires:
- Installing `pyobjc-framework-CoreBluetooth`
- Rewriting the BLE layer to use macOS native APIs

Let me know if you want me to implement Option 2.

### Option 3: Try the MAC addresses we found

Even though they might use UUIDs, we can try:

```bash
python wahoo_ble_logger.py --tickr-address F0:13:C3:FD:EA:CB --kickr-address C7:52:A1:6F:EB:57
```

This probably won't work with current pairing, but worth a try.

## Next Steps

**Please choose:**
1. Unpair the devices (Option 1) - Takes 2 minutes, most likely to work
2. I'll implement CoreBluetooth support (Option 2) - Takes 15 minutes
3. Try something else

Which would you prefer?
