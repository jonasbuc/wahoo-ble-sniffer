PR: analysis/quick-start

What this PR adds
- analysis/quick_analysis.ipynb: a small notebook that loads Parquet exports and produces overview plots (HR over time, power vs cadence, sampling gaps, session summary).
- analysis/run_quick_plots.py: programmatic script that writes figure files to analysis/figs/ and a session_summary.csv.
- analysis/generate_mock_data.py: a mock-data generator that produces realistic sessions with jitter, dropouts and spikes; outputs Parquet files into collector_out/parquet/.
- analysis/run_more_plots.py: additional analysis plots (per-session HR overlays, rolling HR, power boxplots, regression).
- analysis/recompute_summary.py: utility to recompute session_summary.csv from current Parquet exports.
- tests/test_mock_data.py: quick tests that assert presence of generated Parquet and simple numeric/sampling checks.
- Generated artifacts: mock Parquet files and PNGs under analysis/figs/ (for review).

Key findings from mock data
- HR ranges and power ranges look realistic for indoor trainer data.
- Sampling rates are consistent with generator (HR ~1Hz, bike ~2Hz, headpose up to 10Hz for short windows).

Next steps (suggested)
- Add more scenario parameters to the generator (long dropouts, multi-rate sensors, corrupt frames).
- Add CI job to run the quick tests and optionally fail on obvious regressions.
- Convert the executed notebook to HTML and attach to the PR for easier visual review.

Notes
- session_summary.csv is recomputed by running analysis/recompute_summary.py and matches the Parquet exports.
- The mock generator now writes typed timestamps (pandas timestamps) to Parquet for easier downstream processing.
