"""Compatibility wrapper — forwards to `db/pretty_dump_db.py`.

This file remains at the old path for backwards compatibility and simply
loads and exposes the implementation from `db/pretty_dump_db.py`.
"""
from importlib import util
from pathlib import Path
import sys

_p = Path(__file__).parent / 'db' / 'pretty_dump_db.py'
spec = util.spec_from_file_location('pretty_dump_db', str(_p))
if spec is None or spec.loader is None:
    raise RuntimeError(f'Unable to load module at {_p}')
mod = util.module_from_spec(spec)  # type: ignore[arg-type]
sys.modules['pretty_dump_db'] = mod
spec.loader.exec_module(mod)  # type: ignore[attr-defined]

main = mod.main

if __name__ == '__main__':
    main()
