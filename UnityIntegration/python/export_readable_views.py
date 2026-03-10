"""Compatibility wrapper — forwards to `db/export_readable_views.py`.

This file remains at the old path for backwards compatibility and simply
loads and exposes the implementation from `db/export_readable_views.py`.
"""
from importlib import util
from pathlib import Path
import sys

_p = Path(__file__).parent / 'db' / 'export_readable_views.py'
spec = util.spec_from_file_location('export_readable_views', str(_p))
mod = util.module_from_spec(spec)
sys.modules['export_readable_views'] = mod
spec.loader.exec_module(mod)

export_all_views = mod.export_all_views
main = mod.main

if __name__ == '__main__':
    main()
