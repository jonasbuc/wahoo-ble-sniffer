#!/usr/bin/env python3
"""Generate realistic mock Parquet exports for testing analysis scripts.

Creates the following in collector_out/parquet:
 - sessions_readable.parquet
 - hr_readable.parquet
 - bike_readable.parquet
 - headpose_readable.parquet
 - events_readable.parquet

Run: .venv/bin/python analysis/generate_mock_data.py
"""
from pathlib import Path
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

OUT = Path("collector_out") / "parquet"
OUT.mkdir(parents=True, exist_ok=True)

np.random.seed(42)

# Create 3 sessions with varying durations
sessions = []
hr_rows = []
bike_rows = []
head_rows = []
event_rows = []

now = datetime.utcnow().replace(microsecond=0)
start0 = now - timedelta(days=1, hours=2)

session_defs = [
    {"session_id": "s1", "start": start0, "duration_min": 30, "hr_base": 70, "power_base": 120},
    {"session_id": "s2", "start": start0 + timedelta(hours=3), "duration_min": 60, "hr_base": 85, "power_base": 180},
    {"session_id": "s3", "start": start0 + timedelta(hours=6), "duration_min": 12, "hr_base": 65, "power_base": 100},
]

for s in session_defs:
    sid = s["session_id"]
    start = s["start"]
    duration = int(s["duration_min"]) * 60
    end = start + timedelta(seconds=duration)
    sessions.append({
        "session_id": sid,
        "session_start": start.isoformat() + "Z",
        "session_end": end.isoformat() + "Z",
    })

    # HR @ ~1Hz with variability and a warmup + a spike section
    t0 = start
    times = [t0 + timedelta(seconds=i) for i in range(0, duration, 1)]
    hr_base = s["hr_base"]
    hr_vals = (
        hr_base
        + 5 * np.sin(np.linspace(0, 6 * np.pi, len(times)))
        + np.random.normal(0, 2.5, size=len(times))
        + np.linspace(0, 12, len(times)) * 0.0
    )
    # occasional spike
    for i, ts in enumerate(times):
        hr_rows.append({
            "session_id": sid,
            "recv_ts_iso": ts.isoformat() + "Z",
            "bpm": float(max(40, hr_vals[i]))
        })

    # bike data @ 2Hz
    bike_times = [t0 + timedelta(seconds=i * 0.5) for i in range(0, int(duration * 2))]
    cadence = 80 + 10 * np.sin(np.linspace(0, 4 * np.pi, len(bike_times))) + np.random.normal(0, 2, size=len(bike_times))
    power = s["power_base"] + 30 * np.sin(np.linspace(0, 8 * np.pi, len(bike_times))) + np.random.normal(0, 15, size=len(bike_times))
    power = np.clip(power, 0, None)
    for i, ts in enumerate(bike_times):
        bike_rows.append({
            "session_id": sid,
            "recv_ts_iso": ts.isoformat() + "Z",
            "power_w": float(power[i]),
            "cadence_rpm": float(max(0, cadence[i]))
        })

    # headpose @ 10Hz for low-res (keep it smaller by sampling)
    hp_times = [t0 + timedelta(seconds=i * 0.1) for i in range(0, int(min(duration, 300) * 10))]
    yaw = np.sin(np.linspace(0, 6 * np.pi, len(hp_times))) * 15 + np.random.normal(0, 2, size=len(hp_times))
    pitch = np.random.normal(0, 1, size=len(hp_times))
    for i, ts in enumerate(hp_times):
        head_rows.append({
            "session_id": sid,
            "recv_ts_iso": ts.isoformat() + "Z",
            "yaw_deg": float(yaw[i]),
            "pitch_deg": float(pitch[i])
        })

    # events: random button presses or lap markers
    n_events = max(1, duration // 300)
    for j in range(n_events):
        t = start + timedelta(seconds=int(np.random.uniform(30, duration - 10)))
        event_rows.append({
            "session_id": sid,
            "recv_ts_iso": t.isoformat() + "Z",
            "event_type": np.random.choice(["lap", "pause", "marker"]),
            "detail": "mock"
        })

# Write parquet files
pd.DataFrame(sessions).to_parquet(OUT / 'sessions_readable.parquet', index=False)
pd.DataFrame(hr_rows).to_parquet(OUT / 'hr_readable.parquet', index=False)
pd.DataFrame(bike_rows).to_parquet(OUT / 'bike_readable.parquet', index=False)
pd.DataFrame(head_rows).to_parquet(OUT / 'headpose_readable.parquet', index=False)
pd.DataFrame(event_rows).to_parquet(OUT / 'events_readable.parquet', index=False)

print('Wrote mock parquet files to', OUT)
