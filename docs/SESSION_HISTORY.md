# Session History & Runtime Session Management (Unity)

This guide describes how to enable and use the session history panel and
runtime session start/stop in Unity.

---

## Overview

- `VrsSessionLogger` writes `Logs/sessions_history.ndjson` — one JSON object per
  line: `{"display_id": 1, "session_id": "...", "subject": "...", "started_unix_ms": ..., "ended_unix_ms": ..., "dir": "..."}`.
- `SessionManagerUI` loads this file on Start and renders one row per entry in a
  scrollable history panel.
- Each row has **Resume**, **Stop**, and **Open** buttons.
- **Resume** creates a **new** `session_id` for the same `display_id` (Option A —
  recommended for data integrity).

---

## Important Files

| File | Purpose |
|------|---------|
| `Assets/VrsLogging/VrsSessionLogger.cs` | Logger + history file persistence |
| `Assets/VrsLogging/SessionManagerUI.cs` | UI loader and button callbacks |
| `Assets/VrsLogging/SessionHistoryRow.cs` | Row prefab component |
| `Logs/sessions_history.ndjson` | Created automatically when first session starts |

---

## Unity Editor Setup

### 1. Create the history container

- In your UI Canvas: **Create Empty** → rename to `HistoryContainer`
- Add **Vertical Layout Group** component
- Add **Content Size Fitter** → set *Vertical Fit* to **Preferred Size**

### 2. Create the row prefab

1. Create a new GameObject `HistoryRowTemplate`
2. Add child objects and bind them in `SessionHistoryRow`:
   - `Text` (DisplayId) → `displayIdText`
   - `Text` (Subject)   → `subjectText`
   - `Text` (Times)     → `timesText`
   - `Button` (Resume)  → `resumeButton`
   - `Button` (Stop)    → `stopButton`
   - `Button` (Open)    → `openButton`
3. Add the `SessionHistoryRow` component and set all references
4. Save as a prefab; disable or delete the template instance in the Hierarchy

### 3. Wire `SessionManagerUI`

On the GameObject that has the `SessionManagerUI` script, set:

| Inspector field | Target |
|-----------------|--------|
| `logger` | Drag the `VrsSessionLogger` component |
| `subjectInput` | InputField for session subject |
| `newSessionButton` | Button to start a new session |
| `stopSessionButton` | Button to stop the current session |
| `currentSessionLabel` | Text label showing active session |
| `historyContainer` | `HistoryContainer` from step 1 |
| `rowPrefab` | Row prefab from step 2 |

### 4. Test in Play mode

1. Press **Play**
2. Enter a subject and click **New Session** — check `Logs/` for new files and `sessions_history.ndjson`
3. Click **Stop** — `ended_unix_ms` should be filled in `sessions_history.ndjson`
4. Click **Resume** in the history panel — a new session with the same `display_id` starts

---

## NDJSON History Format

Each line in `sessions_history.ndjson` is one JSON object:

```json
{"display_id": 1, "session_id": "a3f1...", "subject": "Jonas", "started_unix_ms": 1710000000000, "ended_unix_ms": 1710003600000, "dir": "Logs/session_a3f1..."}
```

| Field | Type | Description |
|-------|------|-------------|
| `display_id` | int | Human-readable incrementing counter (1, 2, 3, …) |
| `session_id` | string | UUID (hex, no dashes) — unique per recording |
| `subject` | string | Free-text label entered in the UI |
| `started_unix_ms` | int | Session start time (ms since epoch) |
| `ended_unix_ms` | int | Session end time; `null` if still running |
| `dir` | string | Relative path to the session's `.vrsf` folder |

---

## Design Notes

- **Resume = Option A**: a new `session_id` is created for the same `display_id`.
  This keeps every recording atomic and avoids appending to existing VRSF files,
  which simplifies CRC validation and collector logic.
- The display ID counter is persisted in `Logs/display_id_counter.txt` so it
  survives application restarts.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `NullReferenceException` on Start | Ensure `rowPrefab` and `historyContainer` are assigned in `SessionManagerUI` |
| **Open** button does nothing on macOS | Use `Application.OpenURL("file://" + dir)` in the callback |
| `sessions_history.ndjson` missing | Start at least one session — the file is created on first write |
