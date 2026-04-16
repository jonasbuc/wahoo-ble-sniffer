#!/usr/bin/env python3
"""
mock_wahoo_bridge.py — backwards-compatibility shim for bike_bridge.py
=======================================================================
The ``WahooBridgeServer`` in ``bike_bridge.py`` supersedes this file.
Running without ``--live`` is already mock mode.

This module provides a ``MockWahooBridge`` wrapper that forces ``mock=True``
so that existing tests and scripts that import from here continue to work
unchanged.

Usage (command-line)
--------------------
::

    python mock_wahoo_bridge.py [--port 8765] [--no-binary] [--spawn-interval 5]

is equivalent to::

    python bike_bridge.py [--port 8765] [--no-binary] [--spawn-interval 5]
"""
import sys
from pathlib import Path
from typing import TYPE_CHECKING

# Ensure bike_bridge is importable regardless of working directory.
# We insert the directory containing this file so that `import bike_bridge`
# resolves to the sibling file — Pylance also resolves it this way when
# python.analysis.extraPaths includes this directory.
_here = Path(__file__).parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

if TYPE_CHECKING:
    # Pylance static path — resolved relative to this file
    from bridge.bike_bridge import WahooBridgeServer, main  # type: ignore[import]
else:
    from bike_bridge import WahooBridgeServer, main  # noqa: F401


class MockWahooBridge(WahooBridgeServer):
    """``WahooBridgeServer`` with ``mock=True`` forced — no BLE hardware required.

    Drop-in replacement for the old ``MockWahooBridge`` class.
    All constructor kwargs are forwarded to ``WahooBridgeServer``.
    """

    def __init__(self, **kwargs):
        kwargs.setdefault("mock", True)
        super().__init__(**kwargs)


__all__ = ["MockWahooBridge", "main"]

if __name__ == "__main__":
    print("\nMock Wahoo Bridge (-> bike_bridge mock mode)\n")
    main()
