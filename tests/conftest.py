"""Pytest conftest to ensure repository root is on sys.path so tests can import local packages.
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    # Prepend repo root so local packages like `UnityIntegration` are importable during discovery
    sys.path.insert(0, ROOT)
