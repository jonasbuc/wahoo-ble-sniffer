import tempfile
from pathlib import Path
import importlib.util
import sys


def load_export_module():
    path = Path('UnityIntegration/python/db/sqlite/export_readable_views.py')
    spec = importlib.util.spec_from_file_location('export_readable_views', str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules['export_readable_views'] = mod
    spec.loader.exec_module(mod)
    return mod


def test_export_creates_csv_files():
    erv = load_export_module()
    db = Path("collector_out/vrs.sqlite")
    assert db.exists(), "DB must exist for this test"
    with tempfile.TemporaryDirectory() as td:
        out = Path(td)
        written = erv.export_all_views(out, db_path=db)
        csv_paths = [p for p in written if str(p).endswith('.csv')]
        assert any('headpose_readable.csv' in str(p) for p in csv_paths)
        for p in csv_paths:
            assert p.exists()
            assert p.stat().st_size > 0


def test_parquet_optional_if_pyarrow_available():
    try:
        import pyarrow  # type: ignore  # noqa: F401
    except Exception:
        import pytest

        pytest.skip("pyarrow not installed — parquet export not tested")
    erv = load_export_module()
    db = Path("collector_out/vrs.sqlite")
    with tempfile.TemporaryDirectory() as td:
        out = Path(td)
        written = erv.export_all_views(out, db_path=db)
        parquet_paths = [p for p in written if str(p).endswith('.parquet')]
        assert parquet_paths, "pyarrow present but no parquet files created"
