"""Compatibility wrapper — forwards to `db/pretty_dump_db.py`.

This file remains at the old path for backwards compatibility and simply
loads and exposes the implementation from `db/pretty_dump_db.py`.
"""
from importlib import util
from pathlib import Path
import sys

_p = Path(__file__).parent / 'db' / 'pretty_dump_db.py'
spec = util.spec_from_file_location('pretty_dump_db', str(_p))
mod = util.module_from_spec(spec)
sys.modules['pretty_dump_db'] = mod
spec.loader.exec_module(mod)

main = mod.main

if __name__ == '__main__':
    main()
