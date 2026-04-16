import pytest

from bridge import collector_tail as ct


@pytest.mark.skipif(not ct.HAVE_PYARROW, reason="pyarrow not available")
def test_flush_parquet_parts_writes_files(tmp_path):
    # prepare some fake rows for a session/stream as pyarrow expects a list of dicts
    sid = 123
    stream_id = 1
    key = (sid, stream_id)
    # rows should be dict-like matching the headpose schema written by flush_parquet_parts
    rows = [
        {"session_id": sid, "recv_ts_ns": 1000, "seq": 1, "unity_t": 0.01,
            "px": 0.1, "py": 0.2, "pz": 0.3, "qx": 0, "qy": 0, "qz": 0, "qw": 1.0},
        {"session_id": sid, "recv_ts_ns": 1000, "seq": 2, "unity_t": 0.02,
            "px": 0.2, "py": 0.2, "pz": 0.3, "qx": 0, "qy": 0, "qz": 0, "qw": 1.0},
    ]
    # inject into PARQUET_BUFFERS
    ct.PARQUET_BUFFERS[key] = rows.copy()
    outdir = tmp_path / 'parts'
    ct.flush_parquet_parts(str(outdir), part_rows=1000)
    # check for any files created under outdir
    assert outdir.exists()
    files = list(outdir.rglob('*.parquet'))
    assert files, "No parquet files were written"
