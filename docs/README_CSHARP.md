# C# BLE i Unity — Ikke Brugt

> **Note:** Den direkte C# BLE Unity-løsning (`WahooBLEManager.cs`) er fjernet fra projektet.
>
> Puls (HR) kommer fra **Wahoo TICKR FIT** via Python-broen (`bike_bridge.py`) over WebSocket.
> Cykeldata (hastighed, styring, bremser) læses direkte i Unity via **`ArduinoSerialReader.cs`** over seriel port — broen er ikke involveret.
>
> Se **[QUICKSTART.md](QUICKSTART.md)** for den aktuelle opsætning.

---

## Aktuel Arkitektur

```
TICKR FIT ──BLE──► bike_bridge.py ──WS──► WahooWsClient.cs      (puls)
Arduino   ──Serial────────────────────────► ArduinoSerialReader.cs (hastighed/styring)
                                                   ↓
                                             BikeController.cs (bevægelse + styring)
```

Ingen Unity BLE plugin nødvendig.
