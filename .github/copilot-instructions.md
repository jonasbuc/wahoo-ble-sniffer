# Copilot Instructions – Wahoo BLE Sniffer / VR Cycling Simulator

## Repository overview

This repository contains a **Unity VR cycling simulator** with BLE (Bluetooth Low Energy) integration for Wahoo smart trainers, heart-rate monitors, and related peripherals. It also includes a **live analytics pipeline** for real-time telemetry processing and dashboarding.

## Key principles

1. **Separate concerns** – live analytics code lives in `live_analytics/` and `unity/LiveAnalytics/`. BLE bridge code lives in `bridge/`. Do not mix the two unless strictly required.
2. **Local-first architecture** – everything must run on a single Windows machine without cloud dependencies. SQLite is the primary store; JSONL files provide raw durability.
3. **Preserve readability** – prefer small, well-documented modules over monoliths. Use docstrings, type hints, and clear naming.
4. **Simple deployment** – one-click scripts in `starters/` are the canonical way to start services. No Docker or container orchestration is required for local use.
5. **Fail safely** – malformed telemetry packets must never crash the server or the Unity client. Log warnings and continue.
6. **Testing** – add pytest tests for any new Python module. Keep tests fast and isolated (use in-memory SQLite where possible).

## Tech stack

| Layer | Technology |
|---|---|
| Unity client | C# / Unity 2021+ |
| Ingest & API | Python 3.11, FastAPI, uvicorn, websockets |
| Storage | SQLite (WAL mode), JSONL raw files |
| Dashboard | Streamlit |
| BLE bridge | Python, bleak (existing code) |

## Default ports

| Service | Port |
|---|---|
| FastAPI HTTP | 8080 |
| Bridge WebSocket | 8765 |
| Analytics ingest WebSocket | 8766 |
| Streamlit dashboard | 8501 |
| Questionnaire API | 8090 |
| System Check GUI | 8095 |

## Folder conventions

- `live_analytics/app/` – FastAPI analytics server
- `live_analytics/dashboard/` – Streamlit dashboard
- `live_analytics/questionnaire/` – Pre/post-session questionnaire server
- `live_analytics/system_check/` – System health-check GUI
- `live_analytics/tests/` – pytest tests for analytics modules
- `live_analytics/scripts/` – PowerShell launch scripts
- `live_analytics/data/` – runtime data (SQLite DB, session JSONL)
- `unity/LiveAnalytics/` – Unity telemetry publisher
- `bridge/` – BLE bridge, mock server, GUI, collector

## What NOT to change

- `unity/VrsLogging/` – existing VRSF binary logging
- `bridge/` – existing BLE bridge and collector (modify with care)
- `unity/BikeMovementController.cs` – gameplay controller
- Scene files and prefabs unrelated to analytics
