# PR: analysis/quick-start

## What This PR Adds

### Analysis scripts

| File | Purpose |
|------|---------|
| `analysis/quick_analysis.ipynb` | Jupyter notebook — loads Parquet exports and produces overview plots (HR over time, power vs cadence, sampling gaps, session summary) |
| `analysis/run_quick_plots.py` | Programmatic script — writes PNG figures to `analysis/figs/` and a `session_summary.csv` |
| `analysis/run_more_plots.py` | Additional plots — per-session HR overlays, rolling HR, power boxplots, regression |
| `analysis/generate_mock_data.py` | Mock-data generator — produces realistic sessions with jitter, dropouts and spikes; outputs Parquet files into `collector_out/parquet/` |
| `analysis/recompute_summary.py` | Utility — recomputes `session_summary.csv` from current Parquet exports |

### Tests

| File | Purpose |
|------|---------|
| `tests/test_mock_data.py` | Asserts presence of generated Parquet and validates numeric/sampling properties |

### Generated artefacts (for review)

- Mock Parquet files under `collector_out/parquet/`
- Plot PNGs under `analysis/figs/`

---

## Key Findings from Mock Data

- HR and power ranges look realistic for indoor trainer data.
- Sampling rates are consistent with the generator (HR ~1 Hz, bike ~2 Hz, headpose up to 10 Hz for short windows).

---

## Next Steps (Suggested)

- Add more scenario parameters to the generator (long dropouts, multi-rate sensors, corrupt frames).
- Add a CI job to run the quick tests and fail on obvious regressions.
- Convert the executed notebook to HTML and attach to the PR for easier visual review.

---

## Notes

- `session_summary.csv` is recomputed by running `analysis/recompute_summary.py` and matches the Parquet exports.
- The mock generator writes typed timestamps (pandas Timestamps) to Parquet for easier downstream processing.
