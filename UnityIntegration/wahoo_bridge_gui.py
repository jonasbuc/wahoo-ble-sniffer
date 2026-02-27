#!/usr/bin/env python3
"""
Deprecated GUI wrapper.

The canonical GUI script has been moved to:
  UnityIntegration/python/wahoo_bridge_gui.py

This small wrapper will try to execute that script if present. It exists
to preserve old start scripts that reference this location.
"""

from __future__ import annotations

import os
import runpy
import sys


def main() -> None:
    base = os.path.dirname(__file__)
    target = os.path.join(base, "python", "wahoo_bridge_gui.py")
    if os.path.exists(target):
        # Execute the canonical GUI in the current process
        runpy.run_path(target, run_name="__main__")
    else:
        print("The GUI has moved to: UnityIntegration/python/wahoo_bridge_gui.py")
        print("Please run that script directly.")
        sys.exit(1)


if __name__ == "__main__":
    main()



