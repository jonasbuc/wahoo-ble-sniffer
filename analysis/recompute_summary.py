#!/usr/bin/env python3
"""Recompute session_summary.csv from Parquet exports (sessions, hr, bike).
"""
from pathlib import Path
import pandas as pd
import numpy as np

PAR = Path('collector_out') / 'parquet'
OUT = Path('analysis') / 'session_summary.csv'

sessions_p = PAR / 'sessions_readable.parquet'
hr_p = PAR / 'hr_readable.parquet'
bike_p = PAR / 'bike_readable.parquet'

if not sessions_p.exists():
    print('No sessions_parquet found')
    raise SystemExit(1)

s = pd.read_parquet(sessions_p)
# ensure timestamps
for col in ['session_start','session_end']:
    if col in s.columns:
        s[col] = pd.to_datetime(s[col], errors='coerce')

rows = []
for _, row in s.iterrows():
    sid = row.get('session_id')
    start = row.get('session_start')
    end = row.get('session_end')
    duration = (end - start).total_seconds() if pd.notna(start) and pd.notna(end) else None
    # compute hr and bike aggregates from Parquet
    hr_mean = hr_count = None
    bike_count = bike_power_mean = bike_cadence_mean = None
    try:
        hr = pd.read_parquet(hr_p)
        hr['recv_ts'] = pd.to_datetime(hr['recv_ts'], errors='coerce') if 'recv_ts' in hr.columns else pd.to_datetime(hr['recv_ts_iso'], errors='coerce')
        hr_sel = hr[hr.get('session_id') == sid] if 'session_id' in hr.columns else hr
        if not hr_sel.empty:
            hr_mean = hr_sel[[c for c in hr_sel.columns if 'hr' in c.lower() or 'bpm' in c.lower()]].select_dtypes(include=[np.number]).mean().mean()
            hr_count = len(hr_sel)
    except Exception:
        pass
    try:
        bike = pd.read_parquet(bike_p)
        if 'recv_ts' in bike.columns:
            bike['recv_ts'] = pd.to_datetime(bike['recv_ts'], errors='coerce')
        elif 'recv_ts_iso' in bike.columns:
            bike['recv_ts'] = pd.to_datetime(bike['recv_ts_iso'], errors='coerce')
        bike_sel = bike[bike.get('session_id') == sid] if 'session_id' in bike.columns else bike
        if not bike_sel.empty:
            bike_count = len(bike_sel)
            pcols = [c for c in bike_sel.columns if 'power' in c.lower()]
            ccols = [c for c in bike_sel.columns if 'cadence' in c.lower()]
            if pcols:
                bike_power_mean = pd.to_numeric(bike_sel[pcols[0]], errors='coerce').mean()
            if ccols:
                bike_cadence_mean = pd.to_numeric(bike_sel[ccols[0]], errors='coerce').mean()
    except Exception:
        pass
    rows.append({
        'session_id': sid,
        'start': start,
        'end': end,
        'duration_s': duration,
        'hr_mean': hr_mean,
        'hr_samples': hr_count,
        'bike_samples': bike_count,
        'power_mean': bike_power_mean,
        'cadence_mean': bike_cadence_mean,
    })

df = pd.DataFrame(rows)
df.to_csv(OUT, index=False)
print('Wrote', OUT)
