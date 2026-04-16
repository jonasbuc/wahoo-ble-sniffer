# VRSF Logging and Collector

VRSF (VR Session Format) is the custom binary file format used to record sensor
and headpose data from a Unity session.  Unity writes the files in real time;
a Python collector tails them concurrently and imports validated records into
SQLite (and optionally Parquet).

---

## How It Works

```
Unity (VrsSessionLogger)
  │
  ├─ VrsFileWriterFixed  → Logs/session_<id>/stream_1_headpose.vrsf
  ├─ VrsFileWriterFixed  → Logs/session_<id>/stream_2_bike.vrsf
  ├─ VrsFileWriterFixed  → Logs/session_<id>/stream_3_hr.vrsf
  └─ VrsFileWriterEvents → Logs/session_<id>/stream_4_events.vrsf
                                         │
                         (file shared; concurrent read OK)
                                         ↓
collector_tail.py  ←── tails all four files
  │
  ├─ SQLite  → collector_out/vrs.sqlite   (WAL mode, live queries)
  └─ Parquet → collector_out/parquet/     (partitioned by session + stream)
```

---

## VRSF Binary Format

Every `.vrsf` file is a sequence of **chunks**.  Each chunk = 40-byte header + payload.

### Chunk Header (40 bytes, all little-endian)

| Offset | Size | Type   | Field        | Notes |
|--------|------|--------|--------------|-------|
| 0      | 4    | uint32 | magic        | `0x46535256` = ASCII `VRSF` |
| 4      | 2    | uint16 | version      | Currently `1` |
| 6      | 2    | uint16 | streamId     | 1=headpose  2=bike  3=hr  4=events |
| 8      | 2    | uint16 | flags        | Reserved, `0` |
| 10     | 2    | uint16 | reserved     | `0` |
| 12     | 8    | uint64 | sessionId    | Session GUID low 64 bits |
| 20     | 4    | uint32 | chunkSeq     | Monotonically incrementing counter |
| 24     | 4    | uint32 | recordCount  | Number of records in payload |
| 28     | 4    | uint32 | payloadBytes | Byte length of payload |
| 32     | 4    | uint32 | headerCrc32  | CRC32 of header with both CRC fields zeroed |
| 36     | 4    | uint32 | payloadCrc32 | CRC32 of payload bytes |

> **CRC order:** `payloadCrc32` is computed first (payload is ready), then written at
> offset 36.  `headerCrc32` is computed over the 40-byte header with **both** CRC
> fields set to zero, then written at offset 32.

### Stream Record Layouts

#### Stream 1 — Head Pose (36 bytes / record)

| Offset | Type    | Field      | Unit |
|--------|---------|------------|------|
| 0      | uint64  | recv_ts_ns | ns since Unix epoch |
| 8      | float32 | unity_t    | Unity `Time.time` (s) |
| 12     | float32 | px         | position X (m) |
| 16     | float32 | py         | position Y (m) |
| 20     | float32 | pz         | position Z (m) |
| 24     | float32 | qx         | rotation quaternion X |
| 28     | float32 | qy         | rotation quaternion Y |
| 32     | float32 | qz         | rotation quaternion Z |
| 36     | float32 | qw         | rotation quaternion W |

#### Stream 2 — Bike Sensor (20 bytes / record)

| Offset | Type    | Field      | Unit |
|--------|---------|------------|------|
| 0      | uint64  | recv_ts_ns | ns since Unix epoch |
| 8      | float32 | power_w    | Watts |
| 12     | float32 | cadence    | RPM |
| 16     | float32 | speed_kph  | km/h |

#### Stream 3 — Heart Rate (12 bytes / record)

| Offset | Type    | Field      | Unit |
|--------|---------|------------|------|
| 0      | uint64  | recv_ts_ns | ns since Unix epoch |
| 8      | float32 | hr_bpm     | beats per minute |

#### Stream 4 — Events (variable length)

Each record is a UTF-8 JSON string (no null terminator).  `payloadBytes` in the
chunk header gives the total byte length; `recordCount` gives the count of JSON
strings concatenated (records are not newline-separated inside the chunk).

Example event JSON:
```json
{"name": "session_start", "t": 1710000000.0, "subject": "Jonas"}
```

---

## Quick Start

### 1. Add `VrsSessionLogger` to your Unity scene

- Create a GameObject → Add Component → `VrsSessionLogger`
- Set `logsRoot` to `Logs` (or an absolute path)
- Session files appear under `Logs/session_<id>/` when a session starts

### 2. Run the collector

```bash
# From the repository root, with venv active
source .venv/bin/activate
python bridge/collector_tail.py \
    --logs Logs \
    --out collector_out/vrs.sqlite
```

The collector runs indefinitely, tailing new sessions as they appear.  Stop with `Ctrl+C`.

### 3. Inspect data live

```bash
sqlite3 -header -column collector_out/vrs.sqlite \
    "SELECT session_id, count(*) FROM headpose GROUP BY session_id;"
```

Or open `collector_out/vrs.sqlite` in [DB Browser for SQLite](https://sqlitebrowser.org/).

### 4. Create human-readable views

```bash
python bridge/db/create_readable_views.py
```

This adds `*_readable` views with ISO 8601 timestamps and parsed JSON columns.
See `SQL_CHEATSHEET.md` for useful queries.

---

## SQLite Schema

```sql
-- Raw tables written by the collector
CREATE TABLE sessions (session_id TEXT PRIMARY KEY, started_unix_ms INTEGER, ...);
CREATE TABLE headpose (session_id TEXT, recv_ts_ns INTEGER, unity_t REAL,
                       px REAL, py REAL, pz REAL, qx REAL, qy REAL, qz REAL, qw REAL);
CREATE TABLE bike     (session_id TEXT, recv_ts_ns INTEGER, power_w REAL,
                       cadence REAL, speed_kph REAL);
CREATE TABLE hr       (session_id TEXT, recv_ts_ns INTEGER, hr_bpm REAL);
CREATE TABLE events   (session_id TEXT, recv_ts_ns INTEGER, json TEXT);
```

All raw timestamps are `recv_ts_ns` (nanoseconds since epoch).
Use the `*_readable` views for `recv_ts_ms` and `recv_ts_iso`.

---

## Notes

- Unity writers open `.vrsf` files with file-sharing enabled so the Python collector
  can tail concurrently without blocking Unity.
- The collector is robust to partial writes: it waits until a full header + payload
  is available before parsing, and re-syncs on CRC failures.
- CRC32 uses the IEEE 802.3 reflected polynomial (`0xEDB88320`), identical to
  Python's `zlib.crc32(data) & 0xFFFFFFFF`.

---

## Related Files

| File | Purpose |
|------|---------|
| `Assets/VrsLogging/VrsSessionLogger.cs` | Orchestrates all writers per session |
| `Assets/VrsLogging/VrsFormats.cs` | Binary record layouts + chunk-header writer |
| `Assets/VrsLogging/VrsCrc32.cs` | CRC32 implementation |
| `Assets/VrsLogging/VrsFileWriterFixed.cs` | Fixed-size stream writer (streams 1–3) |
| `Assets/VrsLogging/VrsFileWriterEvents.cs` | Variable events writer (stream 4) |
| `python/collector_tail.py` | Python collector (tail VRSF → SQLite + Parquet) |
| `python/db/SQL_CHEATSHEET.md` | Useful SQL queries for the collector DB |
