"""Compatibility wrapper — forwards to `db/create_readable_views.py`.

This file remains at the old path for backwards compatibility and simply
loads and exposes the implementation from `db/create_readable_views.py`.
"""
from importlib import util
from pathlib import Path
import sys

# load implementation from the canonical `db/` location. Be explicit about
# the possibility that spec may be None so type checkers (mypy) are happy.
_p = Path(__file__).parent / 'db' / 'create_readable_views.py'
spec = util.spec_from_file_location('create_readable_views', str(_p))
if spec is None or spec.loader is None:
    raise RuntimeError(f'Unable to load module at {_p}')
mod = util.module_from_spec(spec)  # type: ignore[arg-type]
sys.modules['create_readable_views'] = mod
spec.loader.exec_module(mod)  # type: ignore[attr-defined]

# re-export main
main = mod.main

if __name__ == '__main__':
    main()
