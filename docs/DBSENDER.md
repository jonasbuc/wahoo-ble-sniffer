# DBSender — Unity pulse logger

`unity/DBSender.cs` records heart-rate data from the Wahoo BLE bridge during
a cycling session and writes it to a plain text file that can be imported into
the questionnaire SQLite database after the session ends.

---

## How participant_id is created

The researcher opens the questionnaire web UI (**http://localhost:8090**) and
registers the participant **before** the headset goes on:

1. Enter a short identifier — any string or number you like, e.g. `42`, `P07`,
   `Jonas`.  This becomes the `participant_id`.
2. Optionally enter a display name.
3. Press **Register**.

The questionnaire service stores this as a `TEXT PRIMARY KEY` in the
`participants` table.  There is **no auto-increment** — the researcher controls
the ID.  Pick something consistent (e.g. sequential integers: `1`, `2`, `3`…).

---

## What DBSender writes

Output file: `<Unity dataPath>/CARLogs/pulse.txt`

```
42
1778587705813|72
1778587705814|74
1778587705814|71
1778587706118|73
```

| Position | Content | Example |
|---|---|---|
| **Line 1** | `participant_id` (resolved from the analytics API) | `42` |
| **Every other line** | `unix_ms\|bpm` — millisecond UTC timestamp and heart rate | `1778587705813\|72` |

The file is wiped clean at the start of each Unity session, so one file = one
participant = one ride.

### How participant_id gets into the file

DBSender polls `GET http://127.0.0.1:8080/api/sessions/{session_id}` every few
seconds.  The analytics server resolves the session → participant link
automatically (FIFO order) once the questionnaire registration has been
submitted.  When the API returns a non-null `participant_id`, DBSender rewrites
line 1 of the file in place.

If the session ends before the link resolves, line 1 stays as `PENDING`.  You
can still import the file manually using the `session_id` to look up the
participant.

---

## How to import into the database

The target table is `pulse_data` in the questionnaire SQLite database
(`live_analytics/data/questionnaire.db`):

```sql
CREATE TABLE pulse_data (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT    NOT NULL,
    participant_id  TEXT,
    unix_ms         INTEGER NOT NULL,
    pulse           INTEGER NOT NULL,
    created_at      TEXT    NOT NULL
);
```

### Option A — Python import script (recommended)

```python
import sqlite3, sys
from pathlib import Path
from datetime import datetime, timezone

def import_pulse_log(pulse_txt: str, session_id: str, db_path: str):
    lines = Path(pulse_txt).read_text().splitlines()
    if not lines:
        print("Empty file — nothing to import.")
        return

    participant_id = lines[0].strip()
    if participant_id == "PENDING":
        print("WARNING: participant_id was never resolved. Importing with NULL.")
        participant_id = None

    records = []
    for line in lines[1:]:
        line = line.strip()
        if not line:
            continue
        unix_ms, bpm = line.split("|")
        created_at = datetime.now(timezone.utc).isoformat()
        records.append((session_id, participant_id, int(unix_ms), int(bpm), created_at))

    conn = sqlite3.connect(db_path)
    conn.executemany(
        "INSERT INTO pulse_data (session_id, participant_id, unix_ms, pulse, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        records,
    )
    conn.commit()
    conn.close()
    print(f"Imported {len(records)} pulse records for participant={participant_id!r}")

# Usage:
# python import_pulse.py CARLogs/pulse.txt <session_id> live_analytics/data/questionnaire.db
if __name__ == "__main__":
    import_pulse_log(sys.argv[1], sys.argv[2], sys.argv[3])
```

Run it after the session:

```bash
python import_pulse.py \
  "path/to/CARLogs/pulse.txt" \
  "1718000000000" \
  "live_analytics/data/questionnaire.db"
```

The `session_id` is the 13-digit Unix-ms string printed in the Unity console
at session start (it is also the value `TelemetryPublisher` logs).

### Option B — Query the imported data

```sql
-- All pulse records for participant 42
SELECT unix_ms, pulse
FROM pulse_data
WHERE participant_id = '42'
ORDER BY unix_ms;

-- Average HR per participant
SELECT participant_id, ROUND(AVG(pulse), 1) AS avg_bpm
FROM pulse_data
GROUP BY participant_id;

-- Join with questionnaire answers
SELECT p.display_name, pd.unix_ms, pd.pulse
FROM pulse_data pd
JOIN participants p ON p.participant_id = pd.participant_id
WHERE p.participant_id = '42'
ORDER BY pd.unix_ms;
```

---

## Inspector setup (Unity)

On the `DBSender` GameObject in the scene:

| Field | Assign |
|---|---|
| **Wahoo Ws Client** | The `WahooWsClient` component in the scene |
| **Telemetry Publisher** | The `TelemetryPublisher` component in the scene |
| **Analytics Api Url** | `http://127.0.0.1:8080` (default, change if port differs) |

---

## Timing summary

```
Unity starts
  └─ DBSender.Start()
       ├─ Creates CARLogs/pulse.txt (empty)
       ├─ Waits 1 frame → reads SessionId from TelemetryPublisher
       ├─ Writes "PENDING" as line 1
       └─ Starts PollParticipantId coroutine
            ├─ polls every 5 s for 30 s
            ├─ then every 10 s for 30 s
            └─ then every 30 s × 4
                 └─ on success → rewrites line 1 with participant_id

Researcher registers participant in questionnaire UI
  └─ Analytics server links participant → session (FIFO, oldest-unlinked)
       └─ Next poll by DBSender hits the API → participant_id resolved
```
