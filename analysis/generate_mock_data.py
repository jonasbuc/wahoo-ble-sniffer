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
import argparse

OUT = Path("collector_out") / "parquet"
OUT.mkdir(parents=True, exist_ok=True)

parser = argparse.ArgumentParser(description='Generate mock parquet exports')
parser.add_argument('--seed', type=int, default=42)
parser.add_argument('--dropout_rate', type=float, default=0.02, help='Fraction of time to drop as dropouts')
parser.add_argument('--jitter_ms', type=float, default=50.0, help='Max jitter in ms applied to timestamps')
parser.add_argument('--spike_rate', type=float, default=0.001, help='Fraction of samples to turn into spikes')
args = parser.parse_args()

np.random.seed(args.seed)

# Create 3 sessions with varying durations
sessions = []
hr_rows = []
bike_rows = []
head_rows = []
event_rows = []
