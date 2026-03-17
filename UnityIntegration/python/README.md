# UnityIntegration/python — Bridge scripts & DB utilities

Python scripts for the Unity ↔ BLE bridge pipeline.

## Scripts

| File | Purpose |
|------|---------|
| `bike_bridge.py` | BLE → WebSocket bridge — mock mode (default) or live BLE (`--live`); replaces `mock_wahoo_bridge.py` |
| `wahoo_bridge_gui.py` | Tkinter status monitor with live heart-rate graph; connects to the bridge as a WebSocket client |
| `collector_tail.py` | Tails Unity's VRSF binary log files in real time and imports validated records into SQLite + Parquet |

## DB utilities (`db/`)

| File | Purpose |
|------|---------|
| `db/pretty_dump_db.py` | Prints each table with human-readable timestamps (`recv_ts_ms`, ISO8601). Read-only. |
| `db/create_readable_views.py` | Creates `*_readable` SQLite VIEWs that expose `recv_ts_ms`, `recv_ts_iso`, and parsed JSON fields |
| `db/export_readable_views.py` | Exports readable views to CSV and Parquet files |
| `db/validate_db.py` | Sanity-checks value ranges and quaternion norms; prints counts per table |
| `db/SQL_CHEATSHEET.md` | Useful SQL queries for inspecting the collector DB |

## Quick start (macOS / zsh)

1. Activate the venv from the repo root:

```bash
source .venv/bin/activate
```

2. Create human-readable views (safe; does not change raw tables):

```bash
python UnityIntegration/python/db/create_readable_views.py
```

3. Pretty-print the DB with ISO timestamps:

```bash
python UnityIntegration/python/db/pretty_dump_db.py
```

4. Query a readable view directly with sqlite3:

```bash
sqlite3 -header -column collector_out/vrs.sqlite \
  "SELECT * FROM headpose_readable LIMIT 10;"
```

## Notes

- The collector stores timestamps as `recv_ts_ns` (nanoseconds since Unix epoch).
  All helpers convert to `recv_ts_ms` (milliseconds) and `recv_ts_iso` (ISO 8601) for display.
  Keep the raw nanoseconds in the DB — other code and tests expect that resolution.

- For CSV/Parquet exports use `db/export_readable_views.py`, or load a view directly in pandas:

```python
import sqlite3, pandas as pd
con = sqlite3.connect("collector_out/vrs.sqlite")
df = pd.read_sql_query("SELECT * FROM headpose_readable;", con)
df.to_csv("headpose_readable.csv", index=False)
con.close()
```
