"""
Pytest configuration for the repository-level test suite.

Ensures the repository root is on ``sys.path`` so tests can import local
packages (``bridge``, ``live_analytics``, etc.) without requiring an editable
install.  The root is prepended — not appended — so local packages shadow any
stale installed versions of the same name.
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
