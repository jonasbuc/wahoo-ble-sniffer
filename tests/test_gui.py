"""
tests/test_gui.py
=================
Unit tests for WahooBridgeGUI (wahoo_bridge_gui.py).

Tkinter requires a display to create a Tk() root window.  On CI or headless
machines we use the ``Xvfb`` virtual display if available, or skip via the
``DISPLAY`` environment variable check.  On macOS the Tk framework always
works, so these tests run locally without any setup.

Strategy: create a real ``WahooBridgeGUI`` instance but patch
``threading.Thread`` so the WebSocket background thread never starts.
After each test we destroy the Tk root to avoid resource leaks.
"""
import time
import tkinter as tk
from collections import deque
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Skip entire module if Tk cannot open a display
# ---------------------------------------------------------------------------
try:
    _probe = tk.Tk()
    _probe.destroy()
    _TK_AVAILABLE = True
except Exception:
    _TK_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not _TK_AVAILABLE, reason="No display available for Tkinter"
)


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def gui():
    """Create a WahooBridgeGUI with websocket thread disabled."""
    from UnityIntegration.python.wahoo_bridge_gui import WahooBridgeGUI

    with patch("threading.Thread"):          # prevent background thread starting
        app = WahooBridgeGUI()
    yield app
    try:
        app.root.destroy()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# update_data()
# ---------------------------------------------------------------------------

class TestUpdateData:
    def test_sets_heart_rate(self, gui):
        gui.update_data(72)
        assert gui.heart_rate == 72

    def test_appends_to_hr_history(self, gui):
        before = len(gui.hr_history)
        gui.update_data(80)
        assert len(gui.hr_history) == before + 1

    def test_hr_history_value(self, gui):
        gui.update_data(95)
        _ts, bpm = gui.hr_history[-1]
        assert bpm == 95

    def test_float_hr_is_cast_to_int_in_history(self, gui):
        gui.update_data(72.9)
        _ts, bpm = gui.hr_history[-1]
        assert isinstance(bpm, int)
        assert bpm == 72

    def test_invalid_hr_falls_back_to_zero(self, gui):
        gui.update_data("not-a-number")
        _ts, bpm = gui.hr_history[-1]
        assert bpm == 0

    def test_hr_label_updated(self, gui):
        gui.update_data(63)
        assert gui.hr_value.cget("text") == "63"

    def test_multiple_updates_accumulate(self, gui):
        for bpm in [60, 70, 80]:
            gui.update_data(bpm)
        assert gui.heart_rate == 80
        assert len(gui.hr_history) >= 3


# ---------------------------------------------------------------------------
# _add_trigger()
# ---------------------------------------------------------------------------

class TestAddTrigger:
    def test_appends_trigger(self, gui):
        before = len(gui.triggers)
        gui._add_trigger("spawn")
        assert len(gui.triggers) == before + 1

    def test_trigger_name_stored(self, gui):
        gui._add_trigger("hall_hit")
        _ts, name = gui.triggers[-1]
        assert name == "hall_hit"

    def test_custom_timestamp_stored(self, gui):
        t = 1_700_000_000.0
        gui._add_trigger("test_event", timestamp=t)
        ts, _name = gui.triggers[-1]
        assert ts == pytest.approx(t)

    def test_auto_timestamp_is_recent(self, gui):
        before = time.time()
        gui._add_trigger("auto")
        after = time.time()
        ts, _ = gui.triggers[-1]
        assert before <= ts <= after

    def test_bad_timestamp_is_ignored(self, gui):
        before = len(gui.triggers)
        # Passing a non-numeric timestamp that cannot be cast to float
        gui._add_trigger("bad", timestamp="not-a-float")
        # Exception is swallowed silently — no entry added
        assert len(gui.triggers) == before

    def test_maxlen_not_exceeded(self, gui):
        for i in range(600):  # maxlen=500
            gui._add_trigger(f"evt_{i}")
        assert len(gui.triggers) <= 500


# ---------------------------------------------------------------------------
# Pan / zoom clamp logic (_on_pan_move via direct state manipulation)
# ---------------------------------------------------------------------------

class TestPanClamp:
    def _fill_history(self, gui, n_seconds=60):
        """Populate hr_history with n_seconds of fake data at 1 Hz."""
        now = gui.start_time
        for i in range(n_seconds):
            gui.hr_history.append((now + i, 70))

    def test_pan_cannot_go_positive(self, gui):
        """pan_offset > 0 (into the future) must be clamped to 0."""
        self._fill_history(gui, 60)
        gui._pan_start_x = 0
        gui._pan_start_offset = 0.0
        # Simulate dragging left (negative dx → positive delta → offset > 0)
        event = MagicMock()
        event.x = -200  # dragging left
        gui.graph_width = 400
        gui._on_pan_move(event)
        assert gui.pan_offset <= 0.0

    def test_pan_cannot_go_past_oldest_data(self, gui):
        """pan_offset must not scroll further back than the oldest history entry."""
        self._fill_history(gui, 60)
        gui._pan_start_x = 0
        gui._pan_start_offset = 0.0
        # Simulate an enormous rightward drag that would scroll way into the past
        event = MagicMock()
        event.x = 9999
        gui.graph_width = 400
        gui._on_pan_move(event)
        # Should be clamped — at most -(history_duration - graph_seconds)
        latest_rel = gui.hr_history[-1][0] - gui.start_time
        max_allowed = max(0.0, latest_rel - gui.graph_seconds)
        assert gui.pan_offset >= -max_allowed

    def test_double_click_resets_pan(self, gui):
        """Double-click must snap pan_offset back to 0 (live view)."""
        gui.pan_offset = -15.0
        event = MagicMock()
        gui._on_double_click(event)
        assert gui.pan_offset == 0.0

    def test_pan_end_clears_anchor(self, gui):
        """Releasing the mouse button must clear the drag anchor."""
        gui._pan_start_x = 100
        event = MagicMock()
        gui._on_pan_end(event)
        assert gui._pan_start_x is None
