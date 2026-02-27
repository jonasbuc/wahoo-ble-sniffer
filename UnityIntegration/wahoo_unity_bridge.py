#!/usr/bin/env python3
"""
Deprecated bridge wrapper.

The canonical bridge implementation lives at:
  UnityIntegration/python/wahoo_unity_bridge.py

This small wrapper exists to preserve scripts that start the bridge from
the repository root. It will execute the canonical implementation if it
exists, otherwise it prints instructions.
"""

from __future__ import annotations

import os
import runpy
import sys


def main() -> None:
    base = os.path.dirname(__file__)
    target = os.path.join(base, "python", "wahoo_unity_bridge.py")
    if os.path.exists(target):
        runpy.run_path(target, run_name="__main__")
    else:
        print("The bridge implementation has moved to: UnityIntegration/python/wahoo_unity_bridge.py")
        print("Please run that script directly or update your start scripts.")
        sys.exit(1)


if __name__ == "__main__":
    main()
#!/usr/bin/env python3
"""Helper stub - run the bridge from the python/ folder.

This file was moved to `python/wahoo_unity_bridge.py`.
Run that file instead:

  ./python/wahoo_unity_bridge.py

"""

import sys
print("This file has moved to UnityIntegration/python/wahoo_unity_bridge.py")
print("Run: python3 python/wahoo_unity_bridge.py")
sys.exit(0)
