#!/usr/bin/env python3
"""Generate a broad set of plots from Parquet exports and save them to analysis/figs.

Creates:
 - HR time series and distribution
 - Power vs cadence scatter + marginal hist
 - Power/cadence distributions
 - Sampling gaps histograms per stream
 - Session duration bar chart
 - Missing-data heatmap (resampled at 1s)
 - CSV per-session summary: start/end/duration and basic stats

Run with: .venv/bin/python analysis/run_quick_plots.py
"""
import os
import glob
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from pathlib import Path

sns.set(style="whitegrid")
BASE = Path(".")
DATADIR = BASE / "collector_out" / "parquet"
OUTDIR = BASE / "analysis" / "figs"
OUTDIR.mkdir(parents=True, exist_ok=True)

# Helper to read parquet safely
def read_parquet(name):
    path = DATADIR / f"{name}.parquet"
    if not path.exists():
        print(f"Missing parquet: {path}")
        return pd.DataFrame()
    try:
        return pd.read_parquet(path)
    except Exception as e:
        print("read_parquet failed for", path, e)
        return pd.DataFrame()

# Load datasets
bike = read_parquet("bike_readable")
hr = read_parquet("hr_readable")
headpose = read_parquet("headpose_readable")
sessions = read_parquet("sessions_readable")
events = read_parquet("events_readable")

# Ensure timestamp column `ts` exists and is datetime for each
def ensure_ts(df):
    if df is None or df.empty:
        return df
    if 'ts' in df.columns and pd.api.types.is_datetime64_any_dtype(df['ts']):
        return df
    if 'recv_ts_iso' in df.columns:
        df['ts'] = pd.to_datetime(df['recv_ts_iso'], errors='coerce')
    elif 'recv_ts_ms' in df.columns:
        df['ts'] = pd.to_datetime(df['recv_ts_ms'], unit='ms', errors='coerce')
    elif 'recv_ts_ns' in df.columns:
        df['ts'] = pd.to_datetime(df['recv_ts_ns'], unit='ns', errors='coerce')
    else:
        # try to infer any column that looks like a timestamp
        for c in df.columns:
            if 'time' in c or 'ts' in c:
                try:
                    df['ts'] = pd.to_datetime(df[c], errors='coerce')
                    break
                except Exception:
                    continue
    return df

bike = ensure_ts(bike)
hr = ensure_ts(hr)
headpose = ensure_ts(headpose)
sessions = ensure_ts(sessions)
events = ensure_ts(events)

# Utility to save figures with tight layout
def savefig(name, fig=None):
    out = OUTDIR / name
    if fig is None:
        plt.tight_layout()
        plt.savefig(out)
        plt.close()
    else:
        fig.tight_layout()
        fig.savefig(out)
        plt.close(fig)
    print("Wrote", out)

# 1) HR time series + distribution
if not hr.empty:
    df = hr.sort_values('ts')
    if 'bpm' in df.columns:
        hr_col = 'bpm'
    elif 'hr_bpm' in df.columns:
        hr_col = 'hr_bpm'
    else:
        # try common names
        candidates = [c for c in df.columns if 'hr' in c.lower() or 'bpm' in c.lower()]
        hr_col = candidates[0] if candidates else None

    if hr_col:
        plt.figure(figsize=(12,3))
        plt.plot(df['ts'], df[hr_col], marker='.', ms=3, lw=0.5)
        plt.title('Heart rate over time')
        plt.xlabel('time')
        plt.ylabel('BPM')
        savefig('hr_timeseries.png')

        plt.figure(figsize=(6,4))
        sns.histplot(df[hr_col].dropna(), bins=40, kde=True)
        plt.title('HR distribution')
        savefig('hr_distribution.png')

# 2) Power vs Cadence + marginal
if not bike.empty:
    b = bike.copy()
    # common column names
    p_col = None
    c_col = None
    for c in ['power_w', 'power', 'watts']:
        if c in b.columns:
            p_col = c
            break
    for c in ['cadence_rpm', 'cadence', 'cadence_rpm']:  # keep cadence_rpm as primary
        if c in b.columns:
            c_col = c
            break
    # coerce numeric
    if p_col:
        b[p_col] = pd.to_numeric(b[p_col], errors='coerce')
    if c_col:
        b[c_col] = pd.to_numeric(b[c_col], errors='coerce')
    if p_col and c_col and not b[[p_col, c_col]].dropna().empty:
        plt.figure(figsize=(6,5))
        sns.scatterplot(data=b, x=c_col, y=p_col, alpha=0.6)
        plt.title('Power vs Cadence')
        plt.xlabel('Cadence (rpm)')
        plt.ylabel('Power (W)')
        savefig('power_vs_cadence.png')

        # marginal distributions
        g = sns.jointplot(data=b.dropna(subset=[p_col, c_col]), x=c_col, y=p_col, kind='hex', height=6)
        g.fig.suptitle('Power vs Cadence (hex)')
        g.fig.tight_layout()
        g.fig.subplots_adjust(top=0.95)
        g.fig.savefig(OUTDIR / 'power_vs_cadence_marginal.png')
        plt.close(g.fig)
        print('Wrote', OUTDIR / 'power_vs_cadence_marginal.png')

    # histograms
    if p_col:
        plt.figure(figsize=(5,3))
        sns.histplot(b[p_col].dropna(), bins=40)
        plt.title('Power distribution')
        savefig('power_distribution.png')
    if c_col:
        plt.figure(figsize=(5,3))
        sns.histplot(b[c_col].dropna(), bins=40)
        plt.title('Cadence distribution')
        savefig('cadence_distribution.png')

# 3) Sampling gaps per stream
for name, df in [('headpose', headpose), ('bike', bike), ('hr', hr)]:
    if df is None or df.empty or 'ts' not in df.columns:
        continue
    ts = df['ts'].dropna().sort_values()
    if len(ts) < 2:
        continue
    diffs = ts.diff().dt.total_seconds().dropna()
    plt.figure(figsize=(6,3))
    sns.histplot(diffs, bins=60)
    plt.title(f'Sampling gaps (s) - {name}')
    plt.xlabel('seconds')
    savefig(f'gaps_{name}.png')

# 4) Session duration bar chart and per-session counts
if not sessions.empty:
    s = sessions.copy()
    # try to extract start/end
    if 'session_start' in s.columns and 'session_end' in s.columns:
        s['start'] = pd.to_datetime(s['session_start'], errors='coerce')
        s['end'] = pd.to_datetime(s['session_end'], errors='coerce')
    elif 'start_ts' in s.columns and 'end_ts' in s.columns:
        s['start'] = pd.to_datetime(s['start_ts'], errors='coerce')
        s['end'] = pd.to_datetime(s['end_ts'], errors='coerce')
    elif 'ts' in s.columns and 'session_id' in s.columns:
        # fallback: compute from other tables per session id later
        s['start'] = s.get('start')
        s['end'] = s.get('end')

    # If start/end present
    if 'start' in s.columns and s['start'].notna().any():
        s['duration_s'] = (s['end'] - s['start']).dt.total_seconds()
        s_plot = s.dropna(subset=['duration_s'])
        if not s_plot.empty:
            plt.figure(figsize=(8,3))
            sns.barplot(x='session_id', y='duration_s', data=s_plot)
            plt.xticks(rotation=45)
            plt.ylabel('Duration (s)')
            plt.title('Session durations')
            savefig('session_durations.png')

# 5) Missing-data heatmap: resample streams to 1s and mark presence
streams = {'hr': hr, 'bike': bike, 'headpose': headpose}
resampled = {}
for name, df in streams.items():
    if df is None or df.empty or 'ts' not in df.columns:
        continue
    d = df.set_index('ts')
    # a simple presence series resampled to 1s
    pres = (~d.index.duplicated()).astype(int)
    pres = pres.to_series(name='present') if hasattr(pres, 'to_series') else pd.Series(1, index=d.index, name='present')
    pres = pres.resample('1s').max().fillna(0)
    resampled[name] = pres

# align indices
if resampled:
    all_idx = pd.Index(sorted(set().union(*[s.index for s in resampled.values()])))
    mat = pd.DataFrame(index=all_idx)
    for k, s in resampled.items():
        mat[k] = s.reindex(all_idx, fill_value=0)
    # downsample large matrices for plotting
    if len(mat) > 2000:
        mat = mat.resample('10s').max()
    plt.figure(figsize=(8, max(2, 0.2 * len(mat.columns))))
    sns.heatmap(mat.T, cbar=True)
    plt.title('Presence heatmap (rows=streams, cols=time bins)')
    savefig('missing_data_heatmap.png')

# 6) Event counts by type (if event_type column exists)
if not events.empty:
    e = events.copy()
    if 'event_type' in e.columns:
        counts = e['event_type'].value_counts()
        plt.figure(figsize=(6,3))
        sns.barplot(x=counts.index, y=counts.values)
        plt.xticks(rotation=45)
        plt.title('Event counts')
        savefig('event_counts.png')

# 7) Per-session summary (try to compute basic stats from HR and bike streams)
summary_rows = []
# find session ids either from sessions table or from combined data
session_ids = None
if 'session_id' in sessions.columns:
    session_ids = sessions['session_id'].dropna().unique()
elif 'session_id' in hr.columns:
    session_ids = hr['session_id'].dropna().unique()

if session_ids is None or len(session_ids)==0:
    # fallback: single implicit session from min/max ts
    session_ids = [None]

for sid in session_ids:
    row = {'session_id': sid}
    if sid is None:
        hr_sel = hr
        bike_sel = bike
    else:
        hr_sel = hr[hr.get('session_id') == sid] if not hr.empty and 'session_id' in hr.columns else hr
        bike_sel = bike[bike.get('session_id') == sid] if not bike.empty and 'session_id' in bike.columns else bike
    # times
    min_ts = pd.concat([df['ts'] for df in [hr_sel, bike_sel] if not (df is None or df.empty)]).min() if any([(not df.empty) for df in [hr_sel, bike_sel]]) else pd.NaT
    max_ts = pd.concat([df['ts'] for df in [hr_sel, bike_sel] if not (df is None or df.empty)]).max() if any([(not df.empty) for df in [hr_sel, bike_sel]]) else pd.NaT
    row['start'] = min_ts
    row['end'] = max_ts
    row['duration_s'] = (max_ts - min_ts).total_seconds() if pd.notna(min_ts) and pd.notna(max_ts) else None
    # hr stats
    if not hr_sel.empty:
        hr_vals = hr_sel[[c for c in hr_sel.columns if 'hr' in c.lower() or 'bpm' in c.lower()]].select_dtypes(include=[np.number])
        if not hr_vals.empty:
            row['hr_mean'] = hr_vals.mean().mean()
            row['hr_median'] = hr_vals.median().median()
            row['hr_samples'] = len(hr_sel)
    # bike stats
    if not bike_sel.empty:
        pcol = [c for c in bike_sel.columns if 'power' in c.lower() or 'watts' in c.lower()]
        ccol = [c for c in bike_sel.columns if 'cadence' in c.lower()]
        if pcol:
            row['power_mean'] = pd.to_numeric(bike_sel[pcol[0]], errors='coerce').mean()
        if ccol:
            row['cadence_mean'] = pd.to_numeric(bike_sel[ccol[0]], errors='coerce').mean()
        row['bike_samples'] = len(bike_sel)
    summary_rows.append(row)

summary = pd.DataFrame(summary_rows)
summary_path = BASE / 'analysis' / 'session_summary.csv'
summary.to_csv(summary_path, index=False)
print('Wrote', summary_path)

print('Done generating plots. Figures in', OUTDIR)
