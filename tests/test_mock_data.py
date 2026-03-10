import subprocess
from pathlib import Path
import pandas as pd

PAR = Path('collector_out') / 'parquet'


def test_parquets_exist():
    files = ['sessions_readable.parquet','hr_readable.parquet','bike_readable.parquet']
    for f in files:
        p = PAR / f
        assert p.exists(), f"Missing {p}"


def test_basic_stats():
    hr = pd.read_parquet(PAR / 'hr_readable.parquet')
    bike = pd.read_parquet(PAR / 'bike_readable.parquet')
    # basic row counts
    assert len(hr) > 0
    assert len(bike) > 0
    # numeric ranges
    assert hr['bpm'].min() >= 20
    assert hr['bpm'].max() <= 220
    assert bike['power_w'].min() >= 0
    assert bike['power_w'].max() < 500


import pytest


def test_sampling_gaps():
    hr = pd.read_parquet(PAR / 'hr_readable.parquet')
    # parse recv_ts or recv_ts_iso
    if 'recv_ts' in hr.columns:
        ts = pd.to_datetime(hr['recv_ts'], errors='coerce')
    elif 'recv_ts_iso' in hr.columns:
        ts = pd.to_datetime(hr['recv_ts_iso'], errors='coerce')
    else:
        pytest.skip('no timestamp column')
    diffs = ts.dropna().sort_values().diff().dt.total_seconds().dropna()
    assert diffs.median() <= 2.0
