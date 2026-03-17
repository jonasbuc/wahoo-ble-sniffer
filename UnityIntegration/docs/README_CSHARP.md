# C# BLE i Unity — Ikke Brugt

> **Note:** Den direkte C# BLE Unity-løsning (`WahooBLEManager.cs`) er fjernet fra projektet.
>
> Cykeldata (hastighed, kadence, styring, bremser) kommer nu fra **Arduino** over UDP.
> Puls (HR) kommer fra **Wahoo TICKR FIT** via Python-broen (`wahoo_unity_bridge.py`) over WebSocket.
>
> Se **[QUICKSTART.md](QUICKSTART.md)** for den aktuelle opsætning.

---

## Aktuel Arkitektur

```
TICKR FIT ──BLE──► wahoo_unity_bridge.py ──WS──► WahooDataReceiver.cs
Arduino   ──UDP──► wahoo_unity_bridge.py           BikeMovementController.cs
```

Ingen Unity BLE plugin nødvendig.
