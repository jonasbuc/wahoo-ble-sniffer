"""
Regression tests for bugs found during the stabilization pass.

Covers:
  - websockets v16 import (ConnectionClosed at correct path)
  - ws_ingest uses ServerConnection not legacy WebSocketServerProtocol
  - asyncio task callback on ingest server
  - Streamlit widget key conflict avoidance
"""

from __future__ import annotations


class TestWebsocketsV16Imports:
    """Verify websockets 16.x imports work — no legacy API usage."""

    def test_connection_closed_importable(self):
        from websockets import ConnectionClosed
        assert ConnectionClosed is not None

    def test_server_connection_importable(self):
        from websockets import ServerConnection
        assert ServerConnection is not None

    def test_legacy_exceptions_connection_closed_not_accessible(self):
        """websockets.exceptions.ConnectionClosed is not directly accessible in v16+
        via attribute access on the top-level module."""
        import websockets
        # The correct way is: from websockets import ConnectionClosed
        # Using websockets.exceptions.ConnectionClosed may fail at runtime
        # depending on import state. Our code must not rely on it.
        from pathlib import Path
        src = Path(__file__).resolve().parent.parent / "app" / "ws_ingest.py"
        code = src.read_text()
        assert "websockets.exceptions" not in code

    def test_ws_ingest_imports_modern_api(self):
        """Ensure ws_ingest uses modern websockets imports."""
        from pathlib import Path
        src = Path(__file__).resolve().parent.parent / "app" / "ws_ingest.py"
        code = src.read_text()
        assert "from websockets import ConnectionClosed" in code
        assert "from websockets import" in code and "ServerConnection" in code
        assert "websockets.exceptions" not in code, (
            "ws_ingest.py must not reference websockets.exceptions (removed in v16)"
        )


class TestIngestTaskCallback:
    """Verify the ingest server task has a done callback for crash detection."""

    def test_main_has_done_callback(self):
        from pathlib import Path
        src = Path(__file__).resolve().parent.parent / "app" / "main.py"
        code = src.read_text()
        assert "add_done_callback" in code, (
            "The ingest server task must have a done callback to catch crashes"
        )


class TestWidgetKeyConflict:
    """Verify the dashboard doesn't set session_state keys that
    conflict with widget keys."""

    def test_no_direct_selected_session_key(self):
        """The selectbox widget key and the programmatic key must differ."""
        from pathlib import Path
        src = Path(__file__).resolve().parent.parent / "dashboard" / "streamlit_app.py"
        code = src.read_text()
        # The selectbox should use a widget-specific key like _session_selectbox
        assert '_session_selectbox' in code
        # There should be no direct assignment to the widget key
        assert 'st.session_state.selected_session' not in code
        assert 'key="selected_session"' not in code
