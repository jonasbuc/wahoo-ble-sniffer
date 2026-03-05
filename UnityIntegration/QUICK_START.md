# 🚴‍♂️ Nem Start Guide - Wahoo til Unity

## 📁 Quick Start Filer

Vi har lavet nemme starter-filer så du ikke behøver at åbne Terminal/CMD!

### **For Real BLE Speed / Trainer (Garmin etc):**

**macOS:**
```
Dobbeltklik på: START_WAHOO_BRIDGE.command
```

**Windows:**
```
Dobbeltklik på: START_WAHOO_BRIDGE.bat
```

### **For Test Uden Hardware:**

**macOS:**
```
Dobbeltklik på: START_MOCK_BRIDGE.command
```

**Windows:**
```
Dobbeltklik på: START_MOCK_BRIDGE.bat
```

---

## 🎯 Sådan Bruger Du Det

### Step 1: Start Python Bridge

1. **Tænd din speed/trainer sensor** (og træd på pedalerne for at vække den!)
2. **Dobbeltklik** på `START_WAHOO_BRIDGE.command` (macOS) eller `.bat` (Windows)
3. Vent til du ser: `✓ WebSocket server: ws://localhost:8765`

### Step 2: Start Unity

1. Åbn dit Unity projekt
2. Tryk **Play**
3. Se Unity Console - du skulle se cycling data! 🎉


## 💡 Tips

### Første Gang Setup (Kun Én Gang):

**macOS:**
 Højreklik på `.command` fil → Åbn
- Klik "Åbn" i sikkerhedsadvarslen
- Næste gang kan du bare dobbeltklikke!

**Windows:**
 Hvis Python mangler, download fra [python.org](https://www.python.org/downloads/)
- Installer med "Add Python to PATH" aktiveret

### Troubleshooting:

**"Sensor not found!"**
- ✅ Tænd din sensor
- ✅ Træd på pedalerne (vækker den)
- ✅ macOS: Unpair fra Bluetooth Settings hvis tidligere paired

**"Module not found"**
- Scriptet installerer automatisk dependencies
- Hvis det fejler: Åbn Terminal/CMD og kør:
  ```
  pip install bleak websockets
  ```

**"Port already in use"**
- Stop den gamle bridge først (Ctrl+C i vinduet)
- Eller genstart computer


## 🎮 Unity Setup (Én Gang)

1. Kopier `WahooDataReceiver_Optimized.cs` til `Assets/Scripts/`
2. Create Empty GameObject → "WahooManager"
3. Add Component → WahooDataReceiver
4. Inspector: 
   - Server URL: `ws://localhost:8765`
   - Use Binary Protocol: ✅ (for lav latency)
   - Auto Connect: ✅


## 📊 Hvad Du Får

Bridge'en sender **real-time** data til Unity:

- ⚡ **Power** (Watts)
- 🔄 **Cadence** (RPM)
- 🚴 **Speed** (km/h)
- ❤️ **Heart Rate** (BPM - hvis TICKR tilsluttet)

**Latency:** ~5-15ms med optimeret binary protocol! 💨


## 🔄 Normal Workflow

### Udvikling (ingen hardware):
```
1. Dobbeltklik START_MOCK_BRIDGE
2. Åbn Unity → Play
3. Udvikl dit spil!
```

### Test Med Rigtig Cykel:
```
1. Dobbeltklik START_WAHOO_BRIDGE  
2. Træd på pedalerne
3. Unity → Play
4. Cykel i VR! 🎉
```


## ⚙️ Avanceret

Hvis du vil se Python koden køre:

**macOS Terminal:**
```bash
cd "Blu Sniffer/UnityIntegration"
../venv/bin/python python/wahoo_unity_bridge.py
```

**Windows CMD:**
```cmd
cd "Blu Sniffer\UnityIntegration"
python python/wahoo_unity_bridge.py
```


## 🆘 Hjælp

Se `GRATIS_LØSNING.md` for fuld guide!

**Problem?** Check at:
1. ✅ Python er installeret
2. ✅ Sensor er tændt
3. ✅ Du træder på pedalerne
4. ✅ Unity's Server URL er `ws://localhost:8765`


**Nyd din VR cycling simulator! 🚴‍♂️🎮**
