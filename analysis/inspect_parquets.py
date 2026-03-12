#!/usr/bin/env python3
"""Inspect parquet files in collector_out/parquet and print basic summaries.
"""
from pathlib import Path
import pandas as pd

PAR_DIR = Path('collector_out') / 'parquet'
files = sorted(list(PAR_DIR.glob('*.parquet')))
if not files:
    print('No parquet files found in', PAR_DIR)
    raise SystemExit(0)

for p in files:
    print('\n===', p.name, '===')
    try:
        df = pd.read_parquet(p)
    except Exception as e:
        print('  read error:', e)
        continue
    print('  rows:', len(df))
    print('  columns:', list(df.columns))
    print('  dtypes:')
    print(df.dtypes)
    print('  head:')
    print(df.head(3).to_string(index=False))

    # numeric stats
    for col in ['bpm', 'hr_bpm', 'power_w', 'power', 'cadence_rpm', 'cadence']:
        if col in df.columns:
            try:
                s = pd.to_numeric(df[col], errors='coerce')
                print(f'  {col} stats: n={s.count()} min={s.min():.2f} med={s.median():.2f} max={s.max():.2f}')
            except Exception as e:
                print('   stats error for', col, e)

    # sampling gap if ts parseable
    ts = None
    if 'ts' in df.columns:
        ts = pd.to_datetime(df['ts'], errors='coerce')
    elif 'recv_ts_iso' in df.columns:
        ts = pd.to_datetime(df['recv_ts_iso'], errors='coerce')
    elif 'recv_ts_ms' in df.columns:
        ts = pd.to_datetime(df['recv_ts_ms'], unit='ms', errors='coerce')
    elif 'recv_ts_ns' in df.columns:
        ts = pd.to_datetime(df['recv_ts_ns'], unit='ns', errors='coerce')

    if ts is not None and ts.dropna().shape[0] >= 2:
        diffs = ts.dropna().sort_values().diff().dt.total_seconds().dropna()
        print('  sampling gaps: n_gaps=', len(diffs), 'median(s)=', float(diffs.median()))
    else:
        print('  no parseable timestamps for gap analysis')

print('\nInspection complete')
