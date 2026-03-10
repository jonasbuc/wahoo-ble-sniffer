#!/usr/bin/env python3
"""Additional, more realistic plots: per-session overlays, rolling HR, power boxplots, regression power~cadence."""
from pathlib import Path
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns

sns.set(style='whitegrid')
BASE = Path('.')
DATADIR = BASE / 'collector_out' / 'parquet'
OUTDIR = BASE / 'analysis' / 'figs'
OUTDIR.mkdir(parents=True, exist_ok=True)

# read
def read(name):
    p = DATADIR / f"{name}.parquet"
    if not p.exists():
        return pd.DataFrame()
    return pd.read_parquet(p)

sessions = read('sessions_readable')
hr = read('hr_readable')
bike = read('bike_readable')

# normalize ts
for df in [sessions, hr, bike]:
    if not df.empty and 'recv_ts_iso' in df.columns:
        df['ts'] = pd.to_datetime(df['recv_ts_iso'], errors='coerce')
    elif not df.empty and 'ts' in df.columns:
        df['ts'] = pd.to_datetime(df['ts'], errors='coerce')

# Per-session HR overlay with rolling mean
if not hr.empty:
    hr['bpm'] = pd.to_numeric(hr.get('bpm', hr.get('hr_bpm', None)), errors='coerce')
    groups = hr.groupby('session_id') if 'session_id' in hr.columns else [('all', hr)]
    plt.figure(figsize=(10,4))
    for sid, g in groups:
        g = g.sort_values('ts').dropna(subset=['ts', 'bpm'])
        if g.empty:
            continue
        # resample to 1s for overlay (select numeric column to avoid string columns)
        g = g.set_index('ts')[['bpm']].resample('1s').mean().interpolate()
        plt.plot(g.index, g['bpm'], alpha=0.6, label=str(sid))
    plt.legend()
    plt.title('Per-session HR overlay (1s resampled)')
    plt.xlabel('time')
    plt.ylabel('BPM')
    plt.tight_layout()
    plt.savefig(OUTDIR / 'hr_per_session_overlay.png')
    plt.close()
    print('Wrote', OUTDIR / 'hr_per_session_overlay.png')

    # rolling average for a selected session (first)
    first = next(iter(groups))[1] if hasattr(groups, '__iter__') else None
    if first is not None and not first.empty:
        g = first.sort_values('ts').dropna(subset=['ts','bpm']).set_index('ts')
        roll = g['bpm'].rolling('30s').mean()
        plt.figure(figsize=(10,3))
        plt.plot(g.index, g['bpm'], alpha=0.4, label='raw')
        plt.plot(roll.index, roll.values, color='red', label='30s rolling')
        plt.title('HR rolling (30s)')
        plt.legend()
        plt.tight_layout()
        plt.savefig(OUTDIR / 'hr_rolling_30s.png')
        plt.close()
        print('Wrote', OUTDIR / 'hr_rolling_30s.png')

# Power boxplots per session
if not bike.empty:
    bike['power_w'] = pd.to_numeric(bike.get('power_w', bike.get('power', None)), errors='coerce')
    bike['cadence_rpm'] = pd.to_numeric(bike.get('cadence_rpm', bike.get('cadence', None)), errors='coerce')
    if 'session_id' in bike.columns:
        plt.figure(figsize=(8,4))
        sns.boxplot(x='session_id', y='power_w', data=bike.dropna(subset=['power_w']))
        plt.title('Power boxplot by session')
        plt.tight_layout()
        plt.savefig(OUTDIR / 'power_boxplot_by_session.png')
        plt.close()
        print('Wrote', OUTDIR / 'power_boxplot_by_session.png')

    # regression power ~ cadence (global)
    good = bike.dropna(subset=['power_w','cadence_rpm'])
    if len(good) >= 10:
        X = good['cadence_rpm'].to_numpy()
        y = good['power_w'].to_numpy()
        # simple linear fit y = a*x + b
        slope, intercept = np.polyfit(X, y, 1)
        # scatter + fitted line
        plt.figure(figsize=(6,5))
        sns.scatterplot(x='cadence_rpm', y='power_w', data=good, alpha=0.5)
        xs = np.linspace(good['cadence_rpm'].min(), good['cadence_rpm'].max(), 100)
        ys = intercept + slope * xs
        plt.plot(xs, ys, color='red', label=f'y={slope:.2f}x+{intercept:.1f}')
        plt.legend()
        plt.title('Power vs Cadence with linear fit')
        plt.tight_layout()
        plt.savefig(OUTDIR / 'power_vs_cadence_regression.png')
        plt.close()
        print('Wrote', OUTDIR / 'power_vs_cadence_regression.png')

print('Additional plots done')
