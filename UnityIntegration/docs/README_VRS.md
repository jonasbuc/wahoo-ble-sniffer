VRSF Logging and Collector

Overview
- Unity produces append-only chunked binary files (VRSF) per stream in Logs/session_<id>/
- A Python collector tails those files live and imports validated records into SQLite (WAL) and optionally writes Parquet parts.

Quick start
1) Run Unity with a scene that contains `VrsSessionLogger` (attach to a GameObject). Logs will appear under `Logs/session_<id>/`.
2) From the repo root run:
   python3 UnityIntegration/python/collector_tail.py --logs Logs --out collector_out/vrs.sqlite
3) Open `collector_out/vrs.sqlite` with DB Browser for SQLite to verify live inserts.

Notes
- The Unity writers open files with sharing so the collector can tail concurrently.
- The VRSF format is little-endian, chunked, with CRC32 checks for header and payload.
- Collector is robust to partial writes: it waits for full header+payload before parsing.
