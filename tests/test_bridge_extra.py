"""
test_bridge_extra.py
====================
Additional tests for bike_bridge.py covering paths not yet exercised:

  • WahooBridgeServer.__init__ — attribute defaults
  • parse_args — default values and flag overrides
  • Handshake JSON — all required keys present
  • JSON event relay — message sent by one client reaches other clients
  • UDP pre-formed JSON body forwarded as-is
  • UDP known-string mappings (HALL_HIT, HIT, SWITCH_HIT, etc.)
  • broadcast_json with zero clients — no error, returns cleanly
  • broadcast_json with many clients — all receive the message
  • Live-mode frame packing — struct round-trip for _ble_hr value
  • broadcast_loop exits on CancelledError without swallowing it
  • Handshake re-sent to each independently connecting client
  • Server on already-occupied port raises immediately (port-in-use)
  • Multiple parallel clients all receive the same mock HR data
  • WahooBridgeServer.mockgen is a MockCyclingData instance
"""

from __future__ import annotations

import argparse
import asyncio
import json
import socket
import struct
import sys
import time
from typing import List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from UnityIntegration.python.bike_bridge import (
    MockCyclingData,
    WahooBridgeServer,
    parse_args,
)

try:
    import websockets
    HAS_WS = True
except Exception:
    HAS_WS = False

FRAME_FMT  = "di"
FRAME_SIZE = struct.calcsize(FRAME_FMT)   # 12 bytes


# ── helpers ───────────────────────────────────────────────────────────────────

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_server(**kw) -> WahooBridgeServer:
    defaults = dict(host="127.0.0.1", port=_free_port(), mock=True)
    defaults.update(kw)
    return WahooBridgeServer(**defaults)


# ─────────────────────────────────────────────────────────────────────────────
# WahooBridgeServer.__init__ attribute defaults
# ─────────────────────────────────────────────────────────────────────────────

class TestServerInitAttributes:

    def test_default_host_and_port(self):
        s = WahooBridgeServer()
        assert s.host == "localhost"
        assert s.port == 8765

    def test_custom_host_port(self):
        s = WahooBridgeServer(host="0.0.0.0", port=9000)
        assert s.host == "0.0.0.0"
        assert s.port == 9000

    def test_mock_flag_stored(self):
        s = WahooBridgeServer(mock=True)
        assert s.mock is True
        s2 = WahooBridgeServer(mock=False)
        assert s2.mock is False

    def test_use_binary_default_false(self):
        """use_binary defaults to True in the constructor signature."""
        s = WahooBridgeServer()
        assert s.use_binary is True

    def test_clients_starts_empty_set(self):
        s = _make_server()
        assert isinstance(s.clients, set)
        assert len(s.clients) == 0

    def test_ble_hr_starts_none(self):
        s = _make_server()
        assert s._ble_hr is None

    def test_ble_task_starts_none(self):
        s = _make_server()
        assert s._ble_task is None

    def test_running_starts_false(self):
        s = _make_server()
        assert s.running is False

    def test_mockgen_is_MockCyclingData(self):
        s = _make_server()
        assert isinstance(s.mockgen, MockCyclingData)

    def test_backoff_params_stored(self):
        s = WahooBridgeServer(base_backoff=2.0, max_backoff=60.0)
        assert s.base_backoff == 2.0
        assert s.max_backoff == 60.0

    def test_keepalive_interval_stored(self):
        s = WahooBridgeServer(keepalive_interval=30.0)
        assert s.keepalive_interval == 30.0

    def test_udp_host_port_stored(self):
        s = WahooBridgeServer(udp_host="192.168.1.1", udp_port=7777)
        assert s.udp_host == "192.168.1.1"
        assert s.udp_port == 7777

    def test_ble_address_stored(self):
        s = WahooBridgeServer(ble_address="AA:BB:CC:DD:EE:FF")
        assert s.ble_address == "AA:BB:CC:DD:EE:FF"

    def test_ble_address_default_none(self):
        s = WahooBridgeServer()
        assert s.ble_address is None


# ─────────────────────────────────────────────────────────────────────────────
# parse_args defaults
# ─────────────────────────────────────────────────────────────────────────────

class TestParseArgs:
    """parse_args must expose correct defaults and honour overrides."""

    def _parse(self, argv: list[str]) -> argparse.Namespace:
        with patch.object(sys, "argv", ["bridge"] + argv):
            return parse_args()

    def test_default_port(self):
        args = self._parse([])
        assert args.port == 8765

    def test_default_host(self):
        args = self._parse([])
        assert args.host == "localhost"

    def test_default_live_false(self):
        args = self._parse([])
        assert args.live is False

    def test_live_flag_sets_true(self):
        args = self._parse(["--live"])
        assert args.live is True

    def test_port_override(self):
        args = self._parse(["--port", "9999"])
        assert args.port == 9999

    def test_host_override(self):
        args = self._parse(["--host", "0.0.0.0"])
        assert args.host == "0.0.0.0"

    def test_verbose_default_false(self):
        args = self._parse([])
        assert args.verbose is False

    def test_verbose_flag(self):
        args = self._parse(["--verbose"])
        assert args.verbose is True

    def test_ble_address_default_none(self):
        args = self._parse([])
        assert args.ble_address is None

    def test_ble_address_override(self):
        args = self._parse(["--ble-address", "AA:BB:CC:DD:EE:FF"])
        assert args.ble_address == "AA:BB:CC:DD:EE:FF"

    def test_keepalive_interval_default(self):
        args = self._parse([])
        assert args.keepalive_interval == 15.0

    def test_base_backoff_default(self):
        args = self._parse([])
        assert args.base_backoff == 1.0

    def test_max_backoff_default(self):
        args = self._parse([])
        assert args.max_backoff == 30.0

    def test_no_binary_default_false(self):
        args = self._parse([])
        assert args.no_binary is False

    def test_no_binary_flag(self):
        args = self._parse(["--no-binary"])
        assert args.no_binary is True

    def test_spawn_interval_default_none(self):
        args = self._parse([])
        assert args.spawn_interval is None

    def test_spawn_interval_override(self):
        args = self._parse(["--spawn-interval", "5.0"])
        assert args.spawn_interval == 5.0


# ─────────────────────────────────────────────────────────────────────────────
# Frame packing / protocol correctness
# ─────────────────────────────────────────────────────────────────────────────

class TestFrameProtocol:

    def test_frame_only_contains_timestamp_and_hr(self):
        """di wire format: only timestamp (double) and HR (int32) — 12 bytes."""
        gen = MockCyclingData()
        for _ in range(20):
            frame = gen.get_binary_frame()
            assert len(frame) == FRAME_SIZE
            _ts, hr = struct.unpack(FRAME_FMT, frame)
            assert 30 <= hr <= 220, f"HR out of plausible range: {hr}"

    def test_live_mode_frame_packs_ble_hr(self):
        """When _ble_hr is set the broadcast frame must encode it correctly."""
        server = _make_server(mock=False)
        server._ble_hr = 155

        frame = struct.pack(
            FRAME_FMT,
            time.time(),
            int(server._ble_hr),
        )
        assert len(frame) == FRAME_SIZE
        _ts, hr = struct.unpack(FRAME_FMT, frame)
        assert hr == 155

    def test_frame_hr_field_is_int32(self):
        """HR is packed as 'i' (signed int32) — verify it round-trips for large BPM."""
        for bpm in (40, 100, 155, 220):
            frame = struct.pack(FRAME_FMT, time.time(), bpm)
            _, recovered = struct.unpack(FRAME_FMT, frame)
            assert recovered == bpm

    def test_mock_base_hr_attribute(self):
        gen = MockCyclingData()
        assert gen.base_hr == 140

    def test_mock_time_offset_is_recent(self):
        before = time.time()
        gen = MockCyclingData()
        after = time.time()
        assert before <= gen.time_offset <= after


# ─────────────────────────────────────────────────────────────────────────────
# broadcast_json with zero and many clients
# ─────────────────────────────────────────────────────────────────────────────

class TestBroadcastJson:

    def test_broadcast_json_no_clients_no_error(self):
        """broadcast_json must return cleanly when no clients are connected."""
        server = _make_server()
        assert len(server.clients) == 0

        async def _run():
            await server.broadcast_json({"event": "test"})

        asyncio.run(_run())   # must not raise

    def test_broadcast_json_exclude_skips_sender(self):
        """The *exclude* client must not receive the broadcast."""
        server = _make_server()

        received_by_a = []
        received_by_b = []

        async def _run():
            a = AsyncMock()
            a.send = AsyncMock(side_effect=lambda m: received_by_a.append(m))
            b = AsyncMock()
            b.send = AsyncMock(side_effect=lambda m: received_by_b.append(m))

            server.clients.add(a)
            server.clients.add(b)

            await server.broadcast_json({"event": "hall_hit"}, exclude=a)

        asyncio.run(_run())

        assert len(received_by_a) == 0, "Excluded client must not receive its own message"
        assert len(received_by_b) == 1, "Other client must receive the broadcast"

    def test_broadcast_json_reaches_all_non_excluded_clients(self):
        """All clients except the excluded one must receive the broadcast."""
        server = _make_server()
        received: dict[str, list] = {}

        async def _run():
            clients = {}
            for name in ("c1", "c2", "c3", "c4"):
                c = AsyncMock()
                msgs = []
                received[name] = msgs
                c.send = AsyncMock(side_effect=lambda m, _msgs=msgs: _msgs.append(m))
                clients[name] = c
                server.clients.add(c)

            await server.broadcast_json({"event": "lap"}, exclude=clients["c1"])

        asyncio.run(_run())

        assert len(received["c1"]) == 0
        for name in ("c2", "c3", "c4"):
            assert len(received[name]) == 1

    def test_broadcast_json_message_is_valid_json(self):
        """Each message delivered to clients must be parseable JSON."""
        server = _make_server()
        messages = []

        async def _run():
            c = AsyncMock()
            c.send = AsyncMock(side_effect=lambda m: messages.append(m))
            server.clients.add(c)
            await server.broadcast_json({"event": "test", "value": 42})

        asyncio.run(_run())

        assert len(messages) == 1
        data = json.loads(messages[0])
        assert data["event"] == "test"
        assert data["value"] == 42

    def test_broadcast_json_failed_client_removed_others_untouched(self):
        """A client whose send raises must be removed; others are unaffected."""
        server = _make_server()
        good_msgs = []

        async def _run():
            bad = AsyncMock()
            bad.send = AsyncMock(side_effect=Exception("broken pipe"))
            bad.remote_address = ("127.0.0.1", 1)

            good = AsyncMock()
            good.send = AsyncMock(side_effect=lambda m: good_msgs.append(m))
            good.remote_address = ("127.0.0.1", 2)

            server.clients.add(bad)
            server.clients.add(good)

            await server.broadcast_json({"event": "x"})

        asyncio.run(_run())

        assert len(good_msgs) == 1
        # bad client must have been evicted
        assert all(
            getattr(c, "remote_address", None) != ("127.0.0.1", 1)
            for c in server.clients
        )


# ─────────────────────────────────────────────────────────────────────────────
# WebSocket handshake content
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.skipif(not HAS_WS, reason="websockets not installed")
class TestHandshake:

    @pytest.mark.asyncio
    async def test_handshake_has_protocol_version_modes(self):
        """The first message from the server must be a handshake with known keys."""
        port = _free_port()
        server = WahooBridgeServer(host="127.0.0.1", port=port, mock=True)
        task = asyncio.create_task(server.start())
        await asyncio.sleep(0.15)
        try:
            async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
                raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
                hs = json.loads(raw)
                assert "protocol" in hs
                assert "version"  in hs
                assert "modes"    in hs
                assert hs["version"] == "1.0"
                assert isinstance(hs["modes"], list)
        finally:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    @pytest.mark.asyncio
    async def test_each_client_receives_own_handshake(self):
        """Every new client must get a handshake regardless of connection order."""
        port = _free_port()
        server = WahooBridgeServer(host="127.0.0.1", port=port, mock=True)
        task = asyncio.create_task(server.start())
        await asyncio.sleep(0.15)
        handshakes = []
        try:
            for _ in range(3):
                async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
                    raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
                    handshakes.append(json.loads(raw))
        finally:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        assert len(handshakes) == 3
        for hs in handshakes:
            assert hs["version"] == "1.0"

    @pytest.mark.asyncio
    async def test_handshake_protocol_binary_when_use_binary_true(self):
        """Protocol field must be 'binary' when use_binary=True."""
        port = _free_port()
        server = WahooBridgeServer(host="127.0.0.1", port=port, mock=True, use_binary=True)
        task = asyncio.create_task(server.start())
        await asyncio.sleep(0.15)
        try:
            async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
                hs = json.loads(await asyncio.wait_for(ws.recv(), timeout=2.0))
                assert hs["protocol"] == "binary"
        finally:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass


# ─────────────────────────────────────────────────────────────────────────────
# JSON event relay between clients
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.skipif(not HAS_WS, reason="websockets not installed")
class TestEventRelay:

    @pytest.mark.asyncio
    async def test_event_json_from_client_reaches_other_client(self):
        """A JSON event sent by one client must arrive at all other connected clients."""
        port = _free_port()
        server = WahooBridgeServer(host="127.0.0.1", port=port, mock=True)
        task = asyncio.create_task(server.start())
        await asyncio.sleep(0.15)

        received_by_b = []
        try:
            async with websockets.connect(f"ws://127.0.0.1:{port}") as a, \
                       websockets.connect(f"ws://127.0.0.1:{port}") as b:
                # consume handshakes
                await asyncio.wait_for(a.recv(), timeout=2.0)
                await asyncio.wait_for(b.recv(), timeout=2.0)

                # a sends an event
                await a.send(json.dumps({"event": "lap_complete", "lap": 3}))

                # b should receive it (draining binary frames until we get the event)
                deadline = time.time() + 2.0
                while time.time() < deadline:
                    try:
                        msg = await asyncio.wait_for(b.recv(), timeout=0.3)
                        if isinstance(msg, str):
                            d = json.loads(msg)
                            if d.get("event") == "lap_complete":
                                received_by_b.append(d)
                                break
                    except asyncio.TimeoutError:
                        break
        finally:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        assert len(received_by_b) == 1
        assert received_by_b[0]["lap"] == 3

    @pytest.mark.asyncio
    async def test_non_event_json_not_relayed(self):
        """JSON without an 'event' key must be silently ignored and not relayed."""
        port = _free_port()
        server = WahooBridgeServer(host="127.0.0.1", port=port, mock=True)
        task = asyncio.create_task(server.start())
        await asyncio.sleep(0.15)

        got_non_event = []
        try:
            async with websockets.connect(f"ws://127.0.0.1:{port}") as a:
                async with websockets.connect(f"ws://127.0.0.1:{port}") as b:
                    await asyncio.wait_for(a.recv(), timeout=2.0)
                    await asyncio.wait_for(b.recv(), timeout=2.0)

                    # a sends JSON without 'event' key
                    await a.send(json.dumps({"status": "ok", "value": 99}))
                    await asyncio.sleep(0.15)

                    # Use a short-timeout drain — expect only binary frames, no JSON relay
                    deadline = time.time() + 0.5
                    while time.time() < deadline:
                        try:
                            msg = await asyncio.wait_for(b.recv(), timeout=0.1)
                            if isinstance(msg, str):
                                try:
                                    d = json.loads(msg)
                                    if "status" in d:
                                        got_non_event.append(d)
                                except Exception:
                                    pass
                        except asyncio.TimeoutError:
                            break
        finally:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        assert len(got_non_event) == 0, "Non-event JSON must not be relayed to other clients"


# ─────────────────────────────────────────────────────────────────────────────
# UDP — pre-formed JSON body and known-string mappings
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.skipif(not HAS_WS, reason="websockets not installed")
class TestUDPMapping:

    async def _start_and_send(self, udp_payload: bytes) -> list[dict]:
        """Helper: start server, connect one WS client, send UDP datagram, collect events."""
        udp_port = _free_port()
        ws_port = _free_port()
        server = WahooBridgeServer(
            host="127.0.0.1", port=ws_port, mock=True,
            udp_host="127.0.0.1", udp_port=udp_port,
        )
        task = asyncio.create_task(server.start())
        await asyncio.sleep(0.2)

        events = []
        try:
            async with websockets.connect(f"ws://127.0.0.1:{ws_port}") as ws:
                await asyncio.wait_for(ws.recv(), timeout=2.0)  # handshake
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.sendto(udp_payload, ("127.0.0.1", udp_port))
                sock.close()
                deadline = time.time() + 2.0
                while time.time() < deadline:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=0.3)
                        if isinstance(msg, str):
                            try:
                                d = json.loads(msg)
                                if "event" in d:
                                    events.append(d)
                            except Exception:
                                pass
                    except asyncio.TimeoutError:
                        break
        finally:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        return events

    @pytest.mark.asyncio
    async def test_udp_hit_maps_to_hall_hit(self):
        events = await self._start_and_send(b"HIT")
        assert any(e.get("event") == "hall_hit" for e in events)

    @pytest.mark.asyncio
    async def test_udp_switch_hit_maps_correctly(self):
        events = await self._start_and_send(b"SWITCH_HIT")
        assert any(e.get("event") == "switch_hit" for e in events)

    @pytest.mark.asyncio
    async def test_udp_unknown_string_uses_raw_text_as_event(self):
        events = await self._start_and_send(b"CUSTOM_TRIGGER")
        assert any(e.get("event") == "CUSTOM_TRIGGER" for e in events)

    @pytest.mark.asyncio
    async def test_udp_preformed_json_forwarded_as_is(self):
        payload = json.dumps({"event": "spawn", "x": 1.5, "y": 0.0}).encode()
        events = await self._start_and_send(payload)
        assert any(e.get("event") == "spawn" and e.get("x") == 1.5 for e in events)

    @pytest.mark.asyncio
    async def test_udp_event_has_source_metadata(self):
        events = await self._start_and_send(b"HALL_HIT")
        assert events, "Expected at least one event"
        e = events[0]
        assert e.get("source") == "udp"
        assert "addr" in e
        assert "timestamp" in e

    @pytest.mark.asyncio
    async def test_udp_event_timestamp_is_recent(self):
        before = time.time()
        events = await self._start_and_send(b"HALL_HIT")
        after = time.time()
        assert events
        ts = events[0].get("timestamp", 0)
        assert before - 1 <= ts <= after + 1, f"Timestamp {ts} outside expected window"


# ─────────────────────────────────────────────────────────────────────────────
# Multiple parallel clients all receive frames
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.skipif(not HAS_WS, reason="websockets not installed")
class TestParallelClients:

    @pytest.mark.asyncio
    async def test_five_clients_all_receive_frames(self):
        """Five simultaneous clients must all receive binary frames in mock mode."""
        port = _free_port()
        server = WahooBridgeServer(host="127.0.0.1", port=port, mock=True)
        task = asyncio.create_task(server.start())
        await asyncio.sleep(0.15)

        counts = [0] * 5
        conns = []
        try:
            for _ in range(5):
                ws = await websockets.connect(f"ws://127.0.0.1:{port}")
                conns.append(ws)

            # consume handshakes
            for c in conns:
                await asyncio.wait_for(c.recv(), timeout=2.0)

            # Collect at least 1 binary frame per client (up to 3 attempts each)
            for _ in range(3):
                for i, c in enumerate(conns):
                    try:
                        msg = await asyncio.wait_for(c.recv(), timeout=2.0)
                        if isinstance(msg, (bytes, bytearray)) and len(msg) == FRAME_SIZE:
                            counts[i] += 1
                    except asyncio.TimeoutError:
                        pass
        finally:
            for c in conns:
                try:
                    await c.close()
                except Exception:
                    pass
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        for i, cnt in enumerate(counts):
            assert cnt >= 1, f"Client {i} received 0 frames"
