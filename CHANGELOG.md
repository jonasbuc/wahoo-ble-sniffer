# Changelog

All notable changes to this project are documented here. Dates are in YYYY-MM-DD format.

---

## [Unreleased]

---

## [2026-05-29] — Arduino/UDP removal & documentation cleanup

### Removed
- `bike_bridge.py`: all Arduino/UDP code — `_UDPProtocol`, `spawn_loop`, `udp_host`/`udp_port` parameters, `--spawn-interval` CLI argument
- All references to `--udp-host`, `--udp-port`, and UDP forwarding from docs and README

### Changed
- Bridge is now a focused **Wahoo TICKR FIT → WebSocket HR bridge** (pulse-only)
- Arduino sensor data (speed, steering, brakes) is read **directly in Unity** via `ArduinoSerialReader.cs` over serial port — the bridge has no role there
- All documentation updated to reflect the correct architecture

---

## [2026-05-28] — Documentation improvements

### Changed
- `README.md`: cleaned all `← NY` dev annotations, fixed mixed Danish/English in file tree and service interaction sections
- `live_analytics/README.md`: fully translated from Danish to English
- All Danish table descriptions and inline comments translated to English

---

## [2026-05-20] — Participant auto-linking & pulse logging

### Added
- **FIFO participant-to-session auto-linking** in `ws_ingest.py` — new sessions are automatically linked to the oldest unlinked questionnaire participant
- **`PulseSessionLogger`** (`pulse_session_logger.py`): dedicated per-session pulse log files under `logs/pulse/`
- **`DBSender.cs`**: Unity C# script that writes `CARLogs/pulse.txt` — line 1 is `participant_id`, remaining lines are `unix_ms|bpm` at 1 Hz; polls until participant resolves
- `PUT /api/sessions/{id}/participant` endpoint — manual participant linking from analytics side
- `GET /api/participants/oldest-unlinked` — FIFO ordering for auto-linker
- `GET /api/participants/by-session/{session_id}` — reverse look-up
- `POST /api/sessions/trigger-relink` — re-run resolution for active unlinked sessions
- Pulse session API: `POST /api/pulse-session/start|end`, `GET /api/pulse-session/current`
- Per-participant log directories under `live_analytics/data/participants/<id>/` (pulse.jsonl, session.jsonl, info.json)
- Dual-write in `web_api_client.send_pulse()`: questionnaire DB + external research API

### Added (tests)
- 81 integration tests covering the full participant/session/dashboard flow
- 14 C# unit tests in `tests/mock_dbsender/` for DBSender file format, header rewrite, and JSON extraction

---

## [2026-05-10] — Initial analytics pipeline

### Added
- FastAPI analytics server (`live_analytics/app/`) with WebSocket ingest (`:8766`) and HTTP API (`:8080`)
- Streamlit dashboard (`live_analytics/dashboard/`)
- Questionnaire service (`live_analytics/questionnaire/`) with SPA web UI
- System Check GUI (`live_analytics/system_check/`) with ADB + VRSF log checks
- SQLite storage in WAL mode with per-session JSONL raw files
- Real-time scoring: `stress_score`, `risk_score`, `brake_reaction_ms`, `head_scan_count_5s`
- One-click launcher (`starters/launcher.py`) and install scripts
- Wahoo TICKR FIT BLE bridge (`bridge/bike_bridge.py`) with optional Tkinter GUI monitor
- Mock bridge for hardware-free development (`bridge/mock_wahoo_bridge.py`)
