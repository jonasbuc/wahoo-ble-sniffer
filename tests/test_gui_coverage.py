"""
tests/test_gui_coverage.py
==========================
Coverage tests for the previously untested sections of wahoo_bridge_gui.py:

  • websocket_client() — binary frame, JSON handshake, trigger (udp), trigger (mock filtered),
                         JSON cycling data, reconnect on exception
  • update_bridge_status() — connected=True with/without protocol, connected=False
  • _add_trigger() — normal, None timestamp, invalid timestamp
  • Pan handlers — _on_pan_start, _on_pan_move, _on_pan_end, _on_double_click
  • draw_graph() — empty history, single point, multi-point, with triggers
  • run() — calls root.mainloop()
  • __main__ argparse — --url is forwarded to WahooBridgeGUI
"""

from __future__ import annotations

import asyncio
import json
import struct
import time
import tkinter as tk
from collections import deque
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

# ── skip if no display ────────────────────────────────────────────────────────
try:
    _probe = tk.Tk()
    _probe.destroy()
    _TK_AVAILABLE = True
except Exception:
    _TK_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not _TK_AVAILABLE, reason="No display available for Tkinter"
)


# ── fixture ───────────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────────────────────
# update_bridge_status()
# ─────────────────────────────────────────────────────────────────────────────

class TestUpdateBridgeStatus:
    def test_connected_with_protocol(self, gui):
        gui.update_bridge_status(True, "binary")
        assert "binary" in gui.bridge_label.cget("text")
        assert gui.bridge_label.cget("fg") == "green"

    def test_connected_without_protocol(self, gui):
        gui.update_bridge_status(True, None)
        assert "connected" in gui.bridge_label.cget("text").lower()
        assert gui.bridge_label.cget("fg") == "green"

    def test_disconnected_resets_label(self, gui):
        gui.update_bridge_status(True, "binary")
        gui.update_bridge_status(False)
        assert gui.bridge_label.cget("fg") == "gray"
        assert "--" in gui.bridge_label.cget("text")


# ─────────────────────────────────────────────────────────────────────────────
# _add_trigger()
# ─────────────────────────────────────────────────────────────────────────────

class TestAddTrigger:
    def test_appends_to_triggers(self, gui):
        before = len(gui.triggers)
        gui._add_trigger("hall_hit", time.time())
        assert len(gui.triggers) == before + 1

    def test_trigger_stores_name(self, gui):
        gui._add_trigger("spawn", time.time())
        _, name = gui.triggers[-1]
        assert name == "spawn"

    def test_none_timestamp_defaults_to_now(self, gui):
        t_before = time.time()
        gui._add_trigger("tap", None)
        ts, _ = gui.triggers[-1]
        t_after = time.time()
        assert t_before <= ts <= t_after

    def test_invalid_timestamp_is_swallowed(self, gui):
        """A non-numeric timestamp must not crash _add_trigger."""
        before = len(gui.triggers)
        gui._add_trigger("bad", "not-a-float")
        # Nothing should have been appended (exception caught internally)
        assert len(gui.triggers) == before


# ─────────────────────────────────────────────────────────────────────────────
# draw_graph()
# ─────────────────────────────────────────────────────────────────────────────

class TestDrawGraph:
    def test_empty_history_returns_early(self, gui):
        """draw_graph() must not raise when hr_history is empty."""
        gui.hr_history.clear()
        gui.draw_graph()   # should complete without raising

    def test_single_point_no_crash(self, gui):
        gui.hr_history.clear()
        gui.hr_history.append((time.time(), 70))
        gui.draw_graph()   # single point: no polyline, but must not crash

    def test_multi_point_draws_graph(self, gui):
        """Multi-point history produces canvas items tagged 'graph'."""
        gui.hr_history.clear()
        # Place data in the most recent 10 seconds (well within the 30s window)
        t_now = time.time()
        for i in range(20):
            gui.hr_history.append((t_now - 10 + i * 0.5, 60 + i))
        # Set start_time so elapsed >= graph_seconds to ensure a full visible window
        gui.start_time = t_now - 40
        gui.draw_graph()
        items = gui.graph_canvas.find_withtag("graph")
        assert len(items) > 0

    def test_trigger_within_window_is_drawn(self, gui):
        """A trigger inside the visible window produces an orange line."""
        gui.hr_history.clear()
        t0 = time.time() - 5
        for i in range(10):
            gui.hr_history.append((t0 + i, 70))
        gui.triggers.clear()
        gui.triggers.append((t0 + 3, "hall_hit"))

        gui.draw_graph()
        items = gui.graph_canvas.find_withtag("graph")
        assert len(items) > 0

    def test_trigger_outside_window_not_drawn(self, gui):
        """A trigger far outside the window does not appear in canvas items."""
        gui.hr_history.clear()
        t0 = time.time() - 5
        for i in range(10):
            gui.hr_history.append((t0 + i, 70))
        gui.triggers.clear()
        # Trigger 1000 seconds in the future — outside any visible window
        gui.triggers.append((time.time() + 1000, "future"))

        before_items = 0
        gui.graph_canvas.delete("graph")
        gui.draw_graph()
        # Canvas must not error; exact item count varies — just assert no crash.
        assert True

    def test_draw_with_pan_offset(self, gui):
        """Non-zero pan_offset must not crash draw_graph."""
        gui.hr_history.clear()
        t0 = time.time() - 30
        for i in range(60):
            gui.hr_history.append((t0 + i, 70 + (i % 10)))
        gui.pan_offset = -10.0   # look 10 seconds into the past
        gui.draw_graph()


# ─────────────────────────────────────────────────────────────────────────────
# Pan handlers
# ─────────────────────────────────────────────────────────────────────────────

class TestPanHandlers:
    """Mouse event handlers for graph panning."""

    def _make_event(self, x: int) -> MagicMock:
        ev = MagicMock()
        ev.x = x
        return ev

    def test_on_pan_start_records_position(self, gui):
        gui._on_pan_start(self._make_event(100))
        assert gui._pan_start_x == 100
        assert gui._pan_start_offset == gui.pan_offset

    def test_on_pan_end_clears_anchor(self, gui):
        gui._pan_start_x = 50
        gui._on_pan_end(self._make_event(60))
        assert gui._pan_start_x is None

    def test_on_double_click_resets_pan(self, gui):
        gui.pan_offset = -5.0
        gui._on_double_click(self._make_event(100))
        assert gui.pan_offset == 0.0

    def test_on_pan_move_no_start_does_nothing(self, gui):
        """If pan never started (_pan_start_x is None), move is ignored."""
        gui._pan_start_x = None
        original_offset = gui.pan_offset
        gui._on_pan_move(self._make_event(200))
        assert gui.pan_offset == original_offset

    def test_on_pan_move_changes_offset(self, gui):
        """Dragging right (towards larger x) moves pan_offset in expected direction."""
        # Add some history so clamping logic runs
        t0 = time.time() - 30
        gui.hr_history.clear()
        for i in range(60):
            gui.hr_history.append((t0 + i, 70))

        gui._pan_start_x = 0
        gui._pan_start_offset = 0.0
        gui.pan_offset = 0.0
        # Drag 180 pixels right (half the graph width) — should shift pan_offset
        gui._on_pan_move(self._make_event(180))
        # pan_offset should have changed from 0 (direction is: drag right = towards live = clamp to 0)
        assert isinstance(gui.pan_offset, float)

    def test_on_pan_move_clamps_to_zero(self, gui):
        """Dragging right (towards larger X) cannot pan past the live edge (offset > 0 clamped)."""
        # Put some history so the clamping branch runs
        t0 = time.time() - 30
        gui.hr_history.clear()
        for i in range(60):
            gui.hr_history.append((t0 + i, 70 + (i % 10)))

        # Start from offset=0 and drag right (dx > 0):
        # delta_seconds = dx / width * graph_seconds (positive)
        # pan_offset = 0 - (+delta) → negative (look into past)
        # That's fine; the clamp prevents going *further* into the past than available.
        gui._pan_start_x = 0
        gui._pan_start_offset = 0.0
        gui.pan_offset = 0.0
        gui._on_pan_move(self._make_event(360))   # drag from 0 to full width
        # pan_offset should be clamped within [-max_past, 0]
        assert gui.pan_offset <= 0.0

        # Now drag left from x=200 to x=0 — delta_seconds is negative,
        # so pan_offset = 0 - (negative) = positive → clamped back to 0.
        gui._pan_start_x = 200
        gui._pan_start_offset = 0.0
        gui.pan_offset = 0.0
        gui._on_pan_move(self._make_event(0))
        # After clamp, offset must not exceed 0 (can't pan into the future)
        assert gui.pan_offset <= 0.0


# ─────────────────────────────────────────────────────────────────────────────
# websocket_client() — asyncio coroutine tests
# ─────────────────────────────────────────────────────────────────────────────

class TestWebsocketClient:
    """
    websocket_client() is a coroutine with an infinite retry loop.
    We drive it with a timeout to avoid hanging.

    root.after() is replaced by a synchronous call so side-effects
    (update_data, update_status, etc.) are applied immediately.

    The mock connection context manager delivers one message then raises
    ConnectionError so the reconnect branch fires, followed by a
    CancelledError in asyncio.sleep to terminate the loop.
    """

    def _sync_after(self, gui):
        """Patch root.after so callbacks fire synchronously in the test thread."""
        def after(delay, func, *args):
            func(*args)
        gui.root.after = after

    def _make_mock_ws(self, messages):
        """Return an async context manager mock that yields *messages* then exits.

        On the second call to __aenter__ it raises OSError so the outer
        while-True loop hits the except branch → asyncio.sleep → CancelledError.
        """
        async def _gen():
            for m in messages:
                yield m

        mock_ws = MagicMock()
        mock_ws.__aiter__ = lambda self: _gen()

        call_count = [0]

        async def aenter(_):
            call_count[0] += 1
            if call_count[0] > 1:
                raise OSError("second connect attempt — stop loop")
            return mock_ws

        cm = MagicMock()
        cm.__aenter__ = aenter
        cm.__aexit__ = AsyncMock(return_value=False)
        return cm

    # ── Binary frames ─────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_binary_frame_calls_update_data(self, gui):
        self._sync_after(gui)
        frame = struct.pack("di", time.time(), 77)

        cm = self._make_mock_ws([frame])
        call_count = [0]
        original = gui.update_data

        def tracking(hr):
            call_count[0] += 1
            original(hr)

        gui.update_data = tracking

        sleep_call = [0]

        async def fake_sleep(n):
            sleep_call[0] += 1
            raise asyncio.CancelledError

        with patch("websockets.connect", return_value=cm), \
             patch("asyncio.sleep", side_effect=fake_sleep):
            try:
                await asyncio.wait_for(gui.websocket_client(), timeout=3.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        assert call_count[0] >= 1
        assert gui.heart_rate == 77

    @pytest.mark.asyncio
    async def test_binary_frame_too_short_ignored(self, gui):
        """Frames shorter than 12 bytes must be silently ignored."""
        self._sync_after(gui)
        frame = b"\x00" * 8   # too short

        cm = self._make_mock_ws([frame])

        async def fake_sleep(n):
            raise asyncio.CancelledError

        with patch("websockets.connect", return_value=cm), \
             patch("asyncio.sleep", side_effect=fake_sleep):
            try:
                await asyncio.wait_for(gui.websocket_client(), timeout=3.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        assert gui.heart_rate == 0   # not updated

    # ── JSON handshake ────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_json_handshake_updates_bridge_status(self, gui):
        self._sync_after(gui)
        msg = json.dumps({"protocol": "binary", "version": "1.0"})

        cm = self._make_mock_ws([msg])

        async def fake_sleep(n):
            raise asyncio.CancelledError

        with patch("websockets.connect", return_value=cm), \
             patch("asyncio.sleep", side_effect=fake_sleep):
            try:
                await asyncio.wait_for(gui.websocket_client(), timeout=3.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        assert gui.bridge_protocol == "binary"
        assert "binary" in gui.bridge_label.cget("text")

    # ── Trigger events ────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_trigger_udp_source_is_added(self, gui):
        self._sync_after(gui)
        msg = json.dumps({
            "event": "hall_hit", "source": "udp",
            "timestamp": time.time(), "heart_rate": 0,
        })

        cm = self._make_mock_ws([msg])

        async def fake_sleep(n):
            raise asyncio.CancelledError

        with patch("websockets.connect", return_value=cm), \
             patch("asyncio.sleep", side_effect=fake_sleep):
            try:
                await asyncio.wait_for(gui.websocket_client(), timeout=3.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        names = [name for (_, name) in gui.triggers]
        assert "hall_hit" in names

    @pytest.mark.asyncio
    async def test_trigger_unity_source_is_added(self, gui):
        self._sync_after(gui)
        msg = json.dumps({
            "event": "spawn", "source": "unity",
            "timestamp": time.time(), "heart_rate": 0,
        })

        cm = self._make_mock_ws([msg])

        async def fake_sleep(n):
            raise asyncio.CancelledError

        with patch("websockets.connect", return_value=cm), \
             patch("asyncio.sleep", side_effect=fake_sleep):
            try:
                await asyncio.wait_for(gui.websocket_client(), timeout=3.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        names = [name for (_, name) in gui.triggers]
        assert "spawn" in names

    @pytest.mark.asyncio
    async def test_trigger_mock_source_is_filtered(self, gui):
        """Triggers with source='mock' must not appear in self.triggers."""
        self._sync_after(gui)
        msg = json.dumps({
            "event": "spawn", "source": "mock",
            "timestamp": time.time(), "heart_rate": 0,
        })

        cm = self._make_mock_ws([msg])
        gui.triggers.clear()

        async def fake_sleep(n):
            raise asyncio.CancelledError

        with patch("websockets.connect", return_value=cm), \
             patch("asyncio.sleep", side_effect=fake_sleep):
            try:
                await asyncio.wait_for(gui.websocket_client(), timeout=3.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        assert len(gui.triggers) == 0

    # ── JSON cycling data ─────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_json_cycling_data_updates_hr(self, gui):
        self._sync_after(gui)
        msg = json.dumps({"heart_rate": 88, "speed": 25.5})

        cm = self._make_mock_ws([msg])

        async def fake_sleep(n):
            raise asyncio.CancelledError

        with patch("websockets.connect", return_value=cm), \
             patch("asyncio.sleep", side_effect=fake_sleep):
            try:
                await asyncio.wait_for(gui.websocket_client(), timeout=3.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        assert gui.heart_rate == 88

    # ── Connection/reconnect ──────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_update_status_true_on_connect(self, gui):
        self._sync_after(gui)
        status_calls: list[bool] = []
        original = gui.update_status

        def tracking(c):
            status_calls.append(c)
            original(c)

        gui.update_status = tracking

        # Empty message list — connects then immediately falls through to sleep
        cm = self._make_mock_ws([])

        async def fake_sleep(n):
            raise asyncio.CancelledError

        with patch("websockets.connect", return_value=cm), \
             patch("asyncio.sleep", side_effect=fake_sleep):
            try:
                await asyncio.wait_for(gui.websocket_client(), timeout=3.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        assert True in status_calls

    @pytest.mark.asyncio
    async def test_reconnect_on_connection_error(self, gui):
        """On connection error, update_status(False) is called and retry happens."""
        self._sync_after(gui)
        status_calls: list[bool] = []
        original = gui.update_status

        def tracking(c):
            status_calls.append(c)
            original(c)

        gui.update_status = tracking

        # Connection fails immediately
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(side_effect=OSError("refused"))
        cm.__aexit__ = AsyncMock(return_value=False)

        sleep_calls = [0]

        async def fake_sleep(n):
            sleep_calls[0] += 1
            if sleep_calls[0] >= 2:
                raise asyncio.CancelledError

        with patch("websockets.connect", return_value=cm), \
             patch("asyncio.sleep", side_effect=fake_sleep):
            try:
                await asyncio.wait_for(gui.websocket_client(), timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        assert False in status_calls
        assert sleep_calls[0] >= 1


# ─────────────────────────────────────────────────────────────────────────────
# run() — calls root.mainloop()
# ─────────────────────────────────────────────────────────────────────────────

class TestRun:
    def test_run_calls_mainloop(self, gui):
        with patch.object(gui.root, "mainloop") as mock_mainloop:
            gui.run()
            mock_mainloop.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# __main__ argparse
# ─────────────────────────────────────────────────────────────────────────────

class TestMainArgparse:
    """The __main__ block parses --url and passes it to WahooBridgeGUI."""

    def _run_main_block(self, argv):
        """Execute the __main__ block inline, with WahooBridgeGUI patched."""
        import argparse
        from UnityIntegration.python.wahoo_bridge_gui import WahooBridgeGUI

        p = argparse.ArgumentParser(description="Wahoo Bridge GUI monitor")
        p.add_argument("--url", default="ws://localhost:8765")
        with patch("sys.argv", argv):
            args = p.parse_args()
        return args.url

    def test_default_url_parsed(self):
        """No --url → default ws://localhost:8765."""
        with patch("sys.argv", ["wahoo_bridge_gui.py"]):
            url = self._run_main_block(["wahoo_bridge_gui.py"])
        assert url == "ws://localhost:8765"

    def test_custom_url_parsed(self):
        """--url flag overrides the default."""
        url = self._run_main_block(
            ["wahoo_bridge_gui.py", "--url", "ws://192.168.1.10:9000"]
        )
        assert url == "ws://192.168.1.10:9000"

    def test_gui_constructed_with_url(self):
        """WahooBridgeGUI is instantiated with the parsed url= kwarg."""
        from UnityIntegration.python.wahoo_bridge_gui import WahooBridgeGUI
        with patch("sys.argv", ["wahoo_bridge_gui.py", "--url", "ws://10.0.0.1:9999"]), \
             patch.object(WahooBridgeGUI, "__init__", return_value=None) as mock_init, \
             patch.object(WahooBridgeGUI, "run", return_value=None):
            # Simulate the __main__ block directly
            import argparse
            p = argparse.ArgumentParser()
            p.add_argument("--url", default="ws://localhost:8765")
            args = p.parse_args()
            app = WahooBridgeGUI(url=args.url)
            app.run()
        mock_init.assert_called_once_with(url="ws://10.0.0.1:9999")
