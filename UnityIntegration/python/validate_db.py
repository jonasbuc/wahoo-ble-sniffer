"""Compatibility wrapper — forwards to `db/validate_db.py`.

This file remains at the old path for backwards compatibility and simply
loads and exposes the implementation from `db/validate_db.py`.
"""
from importlib import util
from pathlib import Path
import sys

_p = Path(__file__).parent / 'db' / 'validate_db.py'
spec = util.spec_from_file_location('validate_db', str(_p))
mod = util.module_from_spec(spec)
sys.modules['validate_db'] = mod
spec.loader.exec_module(mod)

# re-export helper functions
validate_headpose = mod.validate_headpose
validate_bike = mod.validate_bike
validate_hr = mod.validate_hr
validate_events = mod.validate_events
main = mod.main

if __name__ == '__main__':
    raise SystemExit(main())
