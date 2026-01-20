# 🎯 GETTING STARTED - Super Easy!

## 🚀 ONE-CLICK INSTALLATION

### First Time Setup:

**macOS:**
1. Download the project
2. Double-click `UnityIntegration/INSTALL.command`
3. Done! ✅

**Windows:**
1. Download the project
2. Double-click `UnityIntegration\INSTALL.bat`
3. Done! ✅

The installer will:
- ✅ Check Python
- ✅ Create virtual environment
- ✅ Install all dependencies
- ✅ Verify everything works

---

## 📊 THREE WAYS TO USE IT

### Option 1: GUI Monitor (RECOMMENDED!)

**Easy visual monitoring with status LED**

**macOS:** Double-click `START_GUI.command`  
**Windows:** Double-click `START_GUI.bat`

**Features:**
- 🟢 Green LED when connected
- 🔴 Red LED when disconnected
- 📊 Live data display:
  - Power (Watts)
  - Cadence (RPM)
  - Speed (km/h)
  - Heart Rate (BPM)
- Auto-reconnects

![GUI Preview](https://via.placeholder.com/400x350?text=Wahoo+Bridge+Monitor)

---

### Option 2: Real KICKR (Terminal)

**For actual cycling**

**macOS:** Double-click `START_WAHOO_BRIDGE.command`  
**Windows:** Double-click `START_WAHOO_BRIDGE.bat`

**Remember:**
- Turn on KICKR
- Pedal to wake it up!
- Wait for "Connected" message

---

### Option 3: Mock Data (Terminal)

**For development without hardware**

**macOS:** Double-click `START_MOCK_BRIDGE.command`  
**Windows:** Double-click `START_MOCK_BRIDGE.bat`

**Perfect for:**
- Developing your Unity game
- Testing without pedaling constantly
- Demo/presentation mode

---

## 🎮 UNITY SETUP (One Time)

1. Copy `WahooDataReceiver_Optimized.cs` to your Unity project (`Assets/Scripts/`)
2. Create Empty GameObject → Name it "WahooManager"
3. Add Component → `WahooDataReceiver`
4. Inspector Settings:
   - Server URL: `ws://localhost:8765`
   - Use Binary Protocol: ✅ (for low latency!)
   - Auto Connect: ✅
   - Enable Smoothing: ✅

5. Press Play!

---

## 💡 TYPICAL WORKFLOW

### Development Phase:
```
1. Double-click START_MOCK_BRIDGE
2. Double-click START_GUI (optional - to see data)
3. Open Unity → Press Play
4. Develop your game!
```

### Testing Phase:
```
1. Turn on KICKR and pedal
2. Double-click START_WAHOO_BRIDGE
3. Double-click START_GUI (to monitor)
4. Open Unity → Press Play
5. Cycle and test!
```

---

## 📁 FILE OVERVIEW

### Installation:
- `INSTALL.command` / `INSTALL.bat` - Auto-installer

### Launchers:
- `START_GUI.command` / `START_GUI.bat` - **GUI Monitor** (NEW!)
- `START_WAHOO_BRIDGE.command` / `.bat` - Real KICKR
- `START_MOCK_BRIDGE.command` / `.bat` - Test data

### Python Scripts:
- `wahoo_bridge_gui.py` - **GUI application** (NEW!)
- `wahoo_unity_bridge.py` - Main bridge (optimized)
- `mock_wahoo_bridge.py` - Test data generator

### Unity:
- `WahooDataReceiver_Optimized.cs` - Unity script (binary protocol)
- `VRBikeController.cs` - Example bike controller

### Documentation:
- `QUICK_START.md` - Quick reference
- `GRATIS_LØSNING.md` - Full guide (Danish)

---

## ⚡ PERFORMANCE

**Low-latency optimized:**
- Binary WebSocket protocol (24 bytes vs ~60 bytes JSON)
- TCP_NODELAY enabled
- Reduced logging overhead
- **Total latency: ~5-15ms** (was ~15-30ms)

Perfect for VR cycling! 🚴‍♂️💨

---

## 🆘 TROUBLESHOOTING

### "Python not found"
- Download from: https://python.org/downloads/
- Windows: Check "Add Python to PATH"

### "KICKR not found"
- Turn on KICKR
- Pedal to wake it up
- macOS: Unpair from Bluetooth Settings if previously paired

### "Connection refused" in Unity
- Make sure bridge is running
- Check Unity Server URL: `ws://localhost:8765`

### GUI shows "Not Connected"
- Start the bridge first (WAHOO or MOCK)
- GUI will auto-connect when bridge is ready

---

## 🎯 WHAT YOU GET

### Real-time Data:
- ⚡ Power (Watts)
- 🔄 Cadence (RPM)  
- 🚴 Speed (km/h)
- ❤️ Heart Rate (BPM) - if TICKR connected

### Three Modes:
1. **GUI Monitor** - Visual status ✨ NEW!
2. **Real Hardware** - Actual cycling
3. **Mock Data** - Development mode

### Zero Cost:
- ✅ 100% Free
- ✅ No Unity plugins required
- ✅ No paid assets
- ✅ Works with desktop VR

---

## 📚 NEXT STEPS

1. ✅ Run `INSTALL` script
2. ✅ Test with `START_MOCK_BRIDGE` + `START_GUI`
3. ✅ Import Unity script
4. ✅ Build your VR cycling game!

**Enjoy! 🚴‍♂️🎮**
