"""
Shared fixtures for live_analytics tests.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the live_analytics package is importable when running tests
# from the live_analytics/tests directory.
_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
