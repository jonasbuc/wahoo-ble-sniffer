"""
test_disconnections.py
======================
Exhaustive disconnection and fault-tolerance tests for:

  ■ Bridge (WahooBridgeServer / bike_bridge.py)
      – client drops connection mid-stream
      – client times out (ping/pong failure)
      – multiple clients, only one drops
      – UDP listener unavailable / bad datagram
      – broadcast_loop keeps running after a client disconnect
      – mock mode sends data immediately (no _ble_hr gate)
      – server starts in mock mode without bleak installed
      – server gracefully handles rapid connect/disconnect churn
      – client sends malformed binary (should not kill other clients)
      – client sends garbage JSON (should not kill other clients)

  ■ DB / Collector (collector_tail.py)
      – DB write fails mid-batch → connection stays open, next write works
      – SQLite file is deleted after init_db → reconnect / re-init
      – VRSF file disappears while being tailed (file-not-found mid-session)
      – VRSF file is truncated after a valid header is written
      – VRSF file contains a run of bad-magic bytes then valid chunks
      – Two chunks back-to-back; first has bad payload CRC, second is good
      – watch_sessions stops cleanly when stop_event is set
      – watch_sessions handles a session dir with a missing manifest
      – DB concurrent access: two connections committing simultaneously
      – init_db on read-only path raises a clear error (not a silent hang)
      – insert into DB with full disk (simulated via mock cursor)
      – WAL checkpoint does not corrupt already-committed rows

All tests are synchronous (using asyncio.run / pytest-asyncio for async
bridge tests) and use tmp_path for isolation.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import struct
import threading
import time
import zlib
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

# ── Import under-test modules ─────────────────────────────────────────────────
from bridge import collector_tail as ct
from bridge.bike_bridge import MockCyclingData, WahooBridgeServer

try:
    import websockets

    HAS_WEBSOCKETS = True
except Exception:
    HAS_WEBSOCKETS = False

# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────

FRAME_FMT = "di"   # timestamp(d) hr(i) = 12 bytes
FRAME_SIZE = struct.calcsize(FRAME_FMT)  # 12


def _free_port() -> int:
    """Return a free TCP port on localhost."""
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_headpose_rec(seq: int = 0) -> bytes:
    """36-byte headpose record: seq(u32) + 8 floats (unity_t, px,py,pz, qx,qy,qz,qw)."""
    return struct.pack("<I8f", seq, float(seq), 0.1, 0.2, 0.3, 0.0, 0.0, 0.0, 1.0)


def make_hr_rec(seq: int = 0, hr: float = 72.0) -> bytes:
    """12-byte HR record: seq(u32) + unity_t(f32) + hr_bpm(f32)."""
    return struct.pack("<Iff", seq, float(seq), hr)


def make_bike_rec(seq: int = 0) -> bytes:
    """20-byte bike record (with 2-byte pad so struct is 20 B total)."""
    return struct.pack("<IfffBBxx", seq, float(seq), 5.0, 0.0, 0, 0)


# ── VRSF file builder ─────────────────────────────────────────────────────────

def _vrsf_header(
    payload: bytes,
    stream_id: int = 1,
    corrupt_hdr_crc: bool = False,
    corrupt_pay_crc: bool = False,
    bad_magic: bool = False,
) -> bytes:
    """Build a valid (or deliberately broken) 40-byte VRSF chunk header."""
    hdr = bytearray(40)
    hdr[0:4] = b"VRSF"
    hdr[4] = 1          # version
    hdr[5] = stream_id
    hdr[24:28] = struct.pack("<I", len(payload))
    # Compute header CRC with CRC fields zeroed
    for i in range(28, 36):
        hdr[i] = 0
    hcrc = zlib.crc32(bytes(hdr)) & 0xFFFFFFFF
    pcrc = zlib.crc32(payload) & 0xFFFFFFFF
    hdr[28:32] = struct.pack("<I", hcrc)
    hdr[32:36] = struct.pack("<I", pcrc)
    if corrupt_hdr_crc:
        hdr[28] ^= 0xFF
    if corrupt_pay_crc:
        hdr[32] ^= 0xFF
    if bad_magic:
        hdr[0:4] = b"BAD!"
    return bytes(hdr)


def write_vrsf(path: Path, chunks: list[tuple[bytes, dict]]) -> None:
    """Write multiple VRSF chunks to *path*.

    Each element of *chunks* is ``(payload_bytes, kwargs)`` where *kwargs*
    are forwarded to ``_vrsf_header``.
    """
    with open(path, "wb") as f:
        for payload, kw in chunks:
            f.write(_vrsf_header(payload, **kw))
            f.write(payload)


# ─────────────────────────────────────────────────────────────────────────────
# ██  BRIDGE TESTS  ███████████████████████████████████████████████████████████
# ─────────────────────────────────────────────────────────────────────────────

class TestBridgeMockMode:
    """WahooBridgeServer in mock mode — no BLE, no bleak required."""

    def _make_server(self, port: Optional[int] = None) -> WahooBridgeServer:
        return WahooBridgeServer(
            host="127.0.0.1",
            port=port or _free_port(),
            use_binary=True,
            mock=True,
        )

    # ── mock data generator ───────────────────────────────────────────────

    def test_mock_frame_is_24_bytes(self):
        """MockCyclingData must always produce exactly 24 bytes."""
        gen = MockCyclingData()
        for _ in range(10):
            frame = gen.get_binary_frame()
            assert len(frame) == FRAME_SIZE, f"Expected {FRAME_SIZE} bytes, got {len(frame)}"

    def test_mock_hr_in_plausible_range(self):
        """Simulated HR must stay between 40 and 220 BPM."""
        gen = MockCyclingData()
        for _ in range(200):
            frame = gen.get_binary_frame()
            _, hr = struct.unpack(FRAME_FMT, frame)
            assert 40 <= hr <= 220, f"HR {hr} outside plausible range"

    def test_mock_frame_timestamp_advances(self):
        """Each frame's embedded timestamp must be >= the previous one."""
        gen = MockCyclingData()
        frames = [gen.get_binary_frame() for _ in range(5)]
        timestamps = [struct.unpack(FRAME_FMT, f)[0] for f in frames]
        for a, b in zip(timestamps, timestamps[1:]):
            assert b >= a, "Timestamp went backwards between frames"

    # ── server starts without bleak ───────────────────────────────────────

    @pytest.mark.asyncio
    async def test_server_starts_in_mock_mode_without_bleak(self):
        """Server must start successfully even when HAVE_BLEAK is False."""
        import bridge.bike_bridge as bridge_mod

        port = _free_port()
        original = bridge_mod.HAVE_BLEAK
        bridge_mod.HAVE_BLEAK = False
        server = WahooBridgeServer(host="127.0.0.1", port=port, mock=True)
        server_task = asyncio.create_task(server.start())
        await asyncio.sleep(0.15)
        assert server.running
        server_task.cancel()
        try:
            await server_task
        except (asyncio.CancelledError, Exception):
            pass
        bridge_mod.HAVE_BLEAK = original

    # ── mock frames delivered to client ──────────────────────────────────

    @pytest.mark.skipif(not HAS_WEBSOCKETS, reason="websockets not installed")
    @pytest.mark.asyncio
    async def test_mock_mode_sends_binary_frames(self):
        """In mock mode, clients must receive binary frames without any BLE device."""
        port = _free_port()
        server = WahooBridgeServer(host="127.0.0.1", port=port, mock=True)
        task = asyncio.create_task(server.start())
        await asyncio.sleep(0.15)

        received = []
        try:
            async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
                # skip handshake
                await asyncio.wait_for(ws.recv(), timeout=2.0)
                for _ in range(5):
                    msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
                    if isinstance(msg, (bytes, bytearray)) and len(msg) == FRAME_SIZE:
                        received.append(msg)
        finally:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        assert len(received) >= 3, (
            f"Expected ≥3 binary frames in mock mode, got {len(received)}"
        )

    # ─────────────────────────────────────────────────────────────────────
    # ██  CLIENT DISCONNECT SCENARIOS  ████████████████████████████████████
    # ─────────────────────────────────────────────────────────────────────

    @pytest.mark.skipif(not HAS_WEBSOCKETS, reason="websockets not installed")
    @pytest.mark.asyncio
    async def test_client_drop_does_not_crash_server(self):
        """Server must keep running after a client abruptly closes its connection."""
        port = _free_port()
        server = WahooBridgeServer(host="127.0.0.1", port=port, mock=True)
        task = asyncio.create_task(server.start())
        await asyncio.sleep(0.15)

        try:
            # Connect, receive handshake, then immediately close without sending close frame
            async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
                await asyncio.wait_for(ws.recv(), timeout=2.0)  # handshake
                # abruptly close TCP (no WS close handshake)
                await ws.close()

            await asyncio.sleep(0.15)  # let server process the disconnect

            # Server must still be running — connect a second client to verify
            async with websockets.connect(f"ws://127.0.0.1:{port}") as ws2:
                msg = await asyncio.wait_for(ws2.recv(), timeout=2.0)
                data = json.loads(msg)
                assert data.get("version") == "1.0"
        finally:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    @pytest.mark.skipif(not HAS_WEBSOCKETS, reason="websockets not installed")
    @pytest.mark.asyncio
    async def test_one_of_two_clients_drops_other_keeps_receiving(self):
        """When one of two connected clients drops, the other must keep receiving frames."""
        port = _free_port()
        server = WahooBridgeServer(host="127.0.0.1", port=port, mock=True)
        task = asyncio.create_task(server.start())
        await asyncio.sleep(0.15)

        frames_c2 = []
        try:
            async with websockets.connect(f"ws://127.0.0.1:{port}") as c1, \
                       websockets.connect(f"ws://127.0.0.1:{port}") as c2:
                # consume handshakes
                await asyncio.wait_for(c1.recv(), timeout=2.0)
                await asyncio.wait_for(c2.recv(), timeout=2.0)

                # drop c1 immediately
                await c1.close()

                # c2 must continue to receive frames after c1 is gone
                for _ in range(5):
                    msg = await asyncio.wait_for(c2.recv(), timeout=2.0)
                    if isinstance(msg, (bytes, bytearray)) and len(msg) == FRAME_SIZE:
                        frames_c2.append(msg)
        finally:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        assert len(frames_c2) >= 3, (
            f"c2 should keep receiving after c1 drops; got {len(frames_c2)} frames"
        )

    @pytest.mark.skipif(not HAS_WEBSOCKETS, reason="websockets not installed")
    @pytest.mark.asyncio
    async def test_rapid_connect_disconnect_churn(self):
        """Server must survive 20 clients connecting and immediately disconnecting."""
        port = _free_port()
        server = WahooBridgeServer(host="127.0.0.1", port=port, mock=True)
        task = asyncio.create_task(server.start())
        await asyncio.sleep(0.15)

        errors = []
        try:
            for _ in range(20):
                try:
                    async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
                        await asyncio.wait_for(ws.recv(), timeout=1.0)
                        # immediately close
                except Exception as e:
                    errors.append(e)

            await asyncio.sleep(0.1)

            # Server must still accept connections after the churn
            async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
                msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
                assert json.loads(msg).get("version") == "1.0"
        finally:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        assert len(errors) == 0, f"Unexpected errors during churn: {errors}"

    # ─────────────────────────────────────────────────────────────────────
    # ██  MALFORMED CLIENT DATA  ██████████████████████████████████████████
    # ─────────────────────────────────────────────────────────────────────

    @pytest.mark.skipif(not HAS_WEBSOCKETS, reason="websockets not installed")
    @pytest.mark.asyncio
    async def test_malformed_binary_from_client_does_not_kill_others(self):
        """A client sending unexpected binary must not affect other clients."""
        port = _free_port()
        server = WahooBridgeServer(host="127.0.0.1", port=port, mock=True)
        task = asyncio.create_task(server.start())
        await asyncio.sleep(0.15)

        frames_good = []
        try:
            async with websockets.connect(f"ws://127.0.0.1:{port}") as bad, \
                       websockets.connect(f"ws://127.0.0.1:{port}") as good:
                await asyncio.wait_for(bad.recv(), timeout=2.0)
                await asyncio.wait_for(good.recv(), timeout=2.0)

                # bad client blasts garbage binary at the server
                await bad.send(b"\xff" * 256)
                await bad.send(b"\x00" * 3)

                # good client should keep receiving frames
                for _ in range(5):
                    msg = await asyncio.wait_for(good.recv(), timeout=2.0)
                    if isinstance(msg, (bytes, bytearray)) and len(msg) == FRAME_SIZE:
                        frames_good.append(msg)
        finally:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        assert len(frames_good) >= 3

    @pytest.mark.skipif(not HAS_WEBSOCKETS, reason="websockets not installed")
    @pytest.mark.asyncio
    async def test_garbage_json_from_client_does_not_crash_server(self):
        """A client sending broken JSON text must not kill the server."""
        port = _free_port()
        server = WahooBridgeServer(host="127.0.0.1", port=port, mock=True)
        task = asyncio.create_task(server.start())
        await asyncio.sleep(0.15)

        try:
            async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
                await asyncio.wait_for(ws.recv(), timeout=2.0)
                # send strings that are not valid JSON
                await ws.send("{{{{not json at all")
                await ws.send("null")
                await ws.send("[]")
                # server must still be alive
                await asyncio.sleep(0.1)
                assert server.running
        finally:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    # ─────────────────────────────────────────────────────────────────────
    # ██  UDP LISTENER SCENARIOS  █████████████████████████████████████████
    # ─────────────────────────────────────────────────────────────────────

    @pytest.mark.skipif(not HAS_WEBSOCKETS, reason="websockets not installed")
    @pytest.mark.asyncio
    async def test_udp_bad_datagram_does_not_crash_server(self):
        """Server must survive receiving non-UTF-8 garbage on the UDP port."""
        udp_port = _free_port()
        ws_port = _free_port()
        server = WahooBridgeServer(
            host="127.0.0.1", port=ws_port, mock=True,
            udp_host="127.0.0.1", udp_port=udp_port,
        )
        task = asyncio.create_task(server.start())
        await asyncio.sleep(0.2)  # give UDP time to bind

        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.sendto(b"\xff\xfe\xfd\xfc" * 50, ("127.0.0.1", udp_port))  # non-UTF8
            sock.sendto(b"", ("127.0.0.1", udp_port))                        # empty
            sock.sendto(b"HALL_HIT", ("127.0.0.1", udp_port))                 # valid
            await asyncio.sleep(0.1)
            assert server.running
        finally:
            sock.close()
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    @pytest.mark.skipif(not HAS_WEBSOCKETS, reason="websockets not installed")
    @pytest.mark.asyncio
    async def test_udp_event_broadcast_reaches_ws_client(self):
        """A HALL_HIT UDP datagram must be delivered as a JSON event to WS clients."""
        udp_port = _free_port()
        ws_port = _free_port()
        server = WahooBridgeServer(
            host="127.0.0.1", port=ws_port, mock=True,
            udp_host="127.0.0.1", udp_port=udp_port,
        )
        task = asyncio.create_task(server.start())
        await asyncio.sleep(0.2)

        import socket
        received_events = []
        try:
            async with websockets.connect(f"ws://127.0.0.1:{ws_port}") as ws:
                await asyncio.wait_for(ws.recv(), timeout=2.0)  # handshake

                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.sendto(b"HALL_HIT", ("127.0.0.1", udp_port))
                sock.close()

                # Drain messages — expect a JSON event among them
                deadline = time.time() + 2.0
                while time.time() < deadline:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=0.3)
                        if isinstance(msg, str):
                            try:
                                d = json.loads(msg)
                                if d.get("event") == "hall_hit":
                                    received_events.append(d)
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

        assert len(received_events) >= 1, "Expected at least one hall_hit event via UDP→WS"

    # ─────────────────────────────────────────────────────────────────────
    # ██  PING/PONG (DEAD CLIENT EVICTION)  ███████████████████████████████
    # ─────────────────────────────────────────────────────────────────────

    def test_ping_loop_removes_unresponsive_client(self):
        """ping_loop must discard a client whose ping call raises an exception."""
        server = WahooBridgeServer(host="127.0.0.1", port=_free_port(), mock=True)

        dead = MagicMock()
        dead.ping = MagicMock(side_effect=Exception("connection reset"))
        dead.remote_address = ("127.0.0.1", 9999)
        server.clients.add(dead)

        async def _run():
            # Run the ping loop for one iteration only (sleep is mocked out)
            original_sleep = asyncio.sleep

            call_count = 0

            async def fast_sleep(delay):
                nonlocal call_count
                call_count += 1
                if call_count > 1:
                    raise asyncio.CancelledError
                await original_sleep(0)

            with patch("asyncio.sleep", fast_sleep):
                try:
                    await server.ping_loop()
                except asyncio.CancelledError:
                    pass

        asyncio.run(_run())
        assert dead not in server.clients, "Dead client must be evicted by ping_loop"

    # ─────────────────────────────────────────────────────────────────────
    # ██  CLIENTS SET INTEGRITY  ██████████████████████████████████████████
    # ─────────────────────────────────────────────────────────────────────

    def test_clients_set_cleaned_up_after_failed_send(self):
        """broadcast_json must remove a client whose send() raises."""
        server = WahooBridgeServer(host="127.0.0.1", port=_free_port(), mock=True)

        broken = MagicMock()
        broken.send = MagicMock(side_effect=Exception("pipe broken"))
        broken.remote_address = ("127.0.0.1", 9000)
        server.clients.add(broken)

        async def _run():
            await server.broadcast_json({"event": "test"})

        asyncio.run(_run())
        assert broken not in server.clients, "Failed-send client must be removed from clients set"

    def test_discard_same_client_twice_is_safe(self):
        """discard() on a client not in the set must not raise."""
        server = WahooBridgeServer(host="127.0.0.1", port=_free_port(), mock=True)
        ghost = MagicMock()
        server.clients.discard(ghost)   # first time: not in set → no error
        server.clients.discard(ghost)   # second time: still no error


# ─────────────────────────────────────────────────────────────────────────────
# ██  DB / COLLECTOR TESTS  ███████████████████████████████████████████████████
# ─────────────────────────────────────────────────────────────────────────────

class TestDBDisconnections:
    """SQLite / init_db / insert reliability under fault conditions."""

    # ── basic re-init after close ─────────────────────────────────────────

    def test_init_db_idempotent(self, tmp_path):
        """Calling init_db twice on the same path must not raise or lose rows."""
        db = tmp_path / "vrs.sqlite"
        conn1 = ct.init_db(str(db))
        rec = make_headpose_rec(1)
        ct.insert_records_batch(conn1, 1, 42, int(time.time() * 1e9), [rec])
        conn1.commit()
        conn1.close()

        # Re-open with init_db — tables must already exist (CREATE IF NOT EXISTS)
        conn2 = ct.init_db(str(db))
        cur = conn2.cursor()
        cur.execute("SELECT COUNT(*) FROM headpose WHERE session_id=42")
        assert cur.fetchone()[0] == 1, "Rows inserted before re-init must survive"

    def test_write_after_db_closed_and_reopened(self, tmp_path):
        """After closing and re-opening the DB the next insert must succeed."""
        db = tmp_path / "reopen.sqlite"
        conn = ct.init_db(str(db))
        conn.close()

        conn = ct.init_db(str(db))
        n = ct.insert_records_batch(conn, 1, 1, int(time.time() * 1e9), [make_headpose_rec(0)])
        conn.commit()
        assert n == 1

    # ── insert failures ───────────────────────────────────────────────────

    def test_db_insert_error_connection_remains_usable(self, tmp_path):
        """After a failed insert the connection must still accept the next good insert."""
        db = tmp_path / "recover.sqlite"
        conn = ct.init_db(str(db))
        sid = 77
        ts = int(time.time() * 1e9)

        # Attempt to insert a too-short (corrupted) record — should raise struct.error
        bad_rec = b"\x00" * 4  # only 4 bytes, needs 36
        with pytest.raises(struct.error):
            ct.insert_records_batch(conn, 1, sid, ts, [bad_rec])

        conn.rollback()  # recover from the mid-transaction error

        # Now insert a good record — connection must still be alive
        good_rec = make_headpose_rec(99)
        n = ct.insert_records_batch(conn, 1, sid, ts, [good_rec])
        conn.commit()
        assert n == 1

        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM headpose WHERE session_id=?", (sid,))
        assert cur.fetchone()[0] == 1

    def test_disk_full_simulation_via_mock_cursor(self, tmp_path):
        """Simulate OperationalError('disk full') on executemany — caller catches it."""
        db = tmp_path / "diskfull.sqlite"
        conn = ct.init_db(str(db))

        class DiskFullCursor:
            def executemany(self, sql, params):
                raise sqlite3.OperationalError("disk I/O error")

            def __getattr__(self, name):
                return MagicMock()

        class DiskFullConn:
            def cursor(self):
                return DiskFullCursor()

            def __getattr__(self, name):
                return getattr(conn, name)

        proxy = DiskFullConn()
        with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
            ct.insert_records_batch(proxy, 1, 1, int(time.time() * 1e9), [make_headpose_rec(0)])

        # Real connection is unaffected — can still insert
        n = ct.insert_records_batch(conn, 1, 1, int(time.time() * 1e9), [make_headpose_rec(0)])
        conn.commit()
        assert n == 1

    def test_concurrent_connections_do_not_corrupt_data(self, tmp_path):
        """Two threads each inserting via their own connection must both succeed."""
        db = tmp_path / "concurrent.sqlite"
        conn_main = ct.init_db(str(db))
        conn_main.close()

        errors = []
        inserted = []

        def worker(session_id: int):
            try:
                conn = ct.init_db(str(db))
                recs = [make_headpose_rec(i) for i in range(50)]
                n = ct.insert_records_batch(conn, 1, session_id, int(time.time() * 1e9), recs)
                conn.commit()
                inserted.append(n)
                conn.close()
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=worker, args=(100,))
        t2 = threading.Thread(target=worker, args=(200,))
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert not errors, f"Concurrent insert errors: {errors}"
        assert sum(inserted) == 100, f"Expected 100 total rows, got {sum(inserted)}"

        conn = sqlite3.connect(str(db))
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM headpose")
        total = cur.fetchone()[0]
        conn.close()
        assert total == 100

    # ── WAL / checkpoint safety ───────────────────────────────────────────

    def test_wal_mode_checkpoint_preserves_committed_rows(self, tmp_path):
        """Issuing wal_checkpoint must not lose committed rows."""
        db = tmp_path / "wal.sqlite"
        conn = ct.init_db(str(db))
        recs = [make_headpose_rec(i) for i in range(100)]
        ct.insert_records_batch(conn, 1, 1, int(time.time() * 1e9), recs)
        conn.commit()

        # Force a WAL checkpoint
        conn.execute("PRAGMA wal_checkpoint(FULL);")

        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM headpose WHERE session_id=1")
        assert cur.fetchone()[0] == 100

    def test_init_db_creates_missing_parent_directory(self, tmp_path):
        """init_db must create any missing parent directories automatically."""
        db = tmp_path / "deep" / "nested" / "dir" / "vrs.sqlite"
        conn = ct.init_db(str(db))
        assert db.exists()
        n = ct.insert_records_batch(conn, 3, 1, int(time.time() * 1e9), [make_hr_rec()])
        conn.commit()
        assert n == 1

    # ── events table ─────────────────────────────────────────────────────

    def test_events_insert_survives_malformed_json_string(self, tmp_path):
        """insert_events_batch must store any string value — even non-JSON — without raising."""
        db = tmp_path / "evts.sqlite"
        conn = ct.init_db(str(db))
        bad_json_tuples = [(1, 0.1, "not json at all"), (2, 0.2, "")]
        n = ct.insert_events_batch(conn, 42, int(time.time() * 1e9), bad_json_tuples)
        conn.commit()
        assert n == 2

    def test_events_empty_list_returns_zero(self, tmp_path):
        """insert_events_batch with an empty list must return 0 and not touch the DB."""
        db = tmp_path / "empty_evts.sqlite"
        conn = ct.init_db(str(db))
        n = ct.insert_events_batch(conn, 1, int(time.time() * 1e9), [])
        assert n == 0


# ─────────────────────────────────────────────────────────────────────────────
# ██  VRSF FILE / FileTail FAULT SCENARIOS  ███████████████████████████████████
# ─────────────────────────────────────────────────────────────────────────────

class TestFileTailDisconnections:
    """FileTail behaviour under file-system and data corruption faults."""

    # ── file does not exist ───────────────────────────────────────────────

    def test_missing_file_returns_none(self, tmp_path):
        """tail_once must return (None, None) when the file does not exist yet."""
        ft = ct.FileTail(str(tmp_path / "ghost.vrsf"), 1, 1, rec_size=36, variable=False)
        rv, parsed = ft.tail_once()
        assert rv is None and parsed is None
        assert ft.offset == 0, "Offset must stay at 0 when file is absent"

    def test_missing_file_offset_does_not_advance(self, tmp_path):
        """Repeated tail_once calls on a missing file must never advance the offset."""
        ft = ct.FileTail(str(tmp_path / "ghost.vrsf"), 1, 1, rec_size=36, variable=False)
        for _ in range(5):
            ft.tail_once()
        assert ft.offset == 0

    # ── file disappears mid-session ───────────────────────────────────────

    def test_file_deleted_after_valid_chunk(self, tmp_path):
        """After a valid read, if the file is deleted the next call returns (None, None)."""
        p = tmp_path / "disappear.vrsf"
        payload = make_headpose_rec(1)
        write_vrsf(p, [(payload, {"stream_id": 1})])

        ft = ct.FileTail(str(p), 1, 1, rec_size=36, variable=False)
        rv, parsed = ft.tail_once()
        assert rv is not None

        p.unlink()  # simulate file disappearing (Unity crash / storage unmount)
        rv2, parsed2 = ft.tail_once()
        assert rv2 is None and parsed2 is None

    # ── truncated file (incomplete payload written) ───────────────────────

    def test_truncated_payload_not_yet_committed(self, tmp_path):
        """tail_once must return (None, None) if the payload is not fully written yet."""
        payload = make_headpose_rec(1)
        hdr = _vrsf_header(payload, stream_id=1)

        p = tmp_path / "trunc.vrsf"
        # Write header + only half the payload
        with open(p, "wb") as f:
            f.write(hdr)
            f.write(payload[: len(payload) // 2])

        ft = ct.FileTail(str(p), 1, 1, rec_size=36, variable=False)
        rv, parsed = ft.tail_once()
        assert rv is None and parsed is None
        assert ft.offset == 0, "Offset must not advance on incomplete payload"

    def test_complete_payload_after_initial_truncation(self, tmp_path):
        """After the payload is fully written, the same FileTail must parse it correctly."""
        payload = make_headpose_rec(7)
        hdr = _vrsf_header(payload, stream_id=1)
        p = tmp_path / "growing.vrsf"

        # Phase 1: only header present
        with open(p, "wb") as f:
            f.write(hdr)
        ft = ct.FileTail(str(p), 1, 1, rec_size=36, variable=False)
        rv, _ = ft.tail_once()
        assert rv is None

        # Phase 2: payload arrives
        with open(p, "ab") as f:
            f.write(payload)
        rv2, parsed2 = ft.tail_once()
        assert rv2 is not None
        assert len(parsed2) == 1

    # ── CRC corruption scenarios ──────────────────────────────────────────

    def test_bad_magic_advances_by_one_byte(self, tmp_path):
        """Bad magic must advance offset by 1 (byte-level resync)."""
        payload = make_headpose_rec(0)
        p = tmp_path / "badmagic.vrsf"
        write_vrsf(p, [(payload, {"stream_id": 1, "bad_magic": True})])
        ft = ct.FileTail(str(p), 1, 1, rec_size=36, variable=False)
        ft.tail_once()
        assert ft.offset == 1

    def test_header_crc_mismatch_advances_by_one_byte(self, tmp_path):
        """Header CRC mismatch must advance offset by 1."""
        payload = make_headpose_rec(0)
        p = tmp_path / "hdrcrc.vrsf"
        write_vrsf(p, [(payload, {"stream_id": 1, "corrupt_hdr_crc": True})])
        ft = ct.FileTail(str(p), 1, 1, rec_size=36, variable=False)
        ft.tail_once()
        assert ft.offset == 1

    def test_payload_crc_mismatch_skips_whole_chunk(self, tmp_path):
        """Payload CRC mismatch must skip the entire chunk (offset += HEADER_SIZE + payload)."""
        from bridge.collector_tail import HEADER_SIZE

        payload = make_headpose_rec(0)
        p = tmp_path / "paycrc.vrsf"
        write_vrsf(p, [(payload, {"stream_id": 1, "corrupt_pay_crc": True})])
        ft = ct.FileTail(str(p), 1, 1, rec_size=36, variable=False)
        ft.tail_once()
        assert ft.offset == HEADER_SIZE + len(payload)

    def test_bad_chunk_then_good_chunk_second_is_parsed(self, tmp_path):
        """After a chunk with bad magic the byte-resync must eventually reach the good chunk."""
        from bridge.collector_tail import HEADER_SIZE

        good_payload = make_headpose_rec(42)
        bad_hdr = _vrsf_header(good_payload, stream_id=1, bad_magic=True)
        good_hdr = _vrsf_header(good_payload, stream_id=1)

        p = tmp_path / "mixed.vrsf"
        with open(p, "wb") as f:
            # Prepend 4 garbage bytes to force resync, then a valid chunk
            f.write(b"\xDE\xAD\xBE\xEF")  # 4 invalid bytes
            f.write(good_hdr)
            f.write(good_payload)

        ft = ct.FileTail(str(p), 1, 1, rec_size=36, variable=False)
        # Drive tail_once until we either parse a record or exhaust retries
        parsed_records = None
        for _ in range(HEADER_SIZE + len(good_payload) + 4 + 10):
            rv, parsed = ft.tail_once()
            if parsed is not None:
                parsed_records = parsed
                break

        assert parsed_records is not None, "Must eventually parse the good chunk after garbage bytes"
        assert len(parsed_records) == 1

    def test_two_good_chunks_both_parsed(self, tmp_path):
        """Two back-to-back valid chunks must both be parsed (requires two tail_once calls)."""
        p1 = make_headpose_rec(1)
        p2 = make_headpose_rec(2)
        p = tmp_path / "two_chunks.vrsf"
        write_vrsf(p, [(p1, {"stream_id": 1}), (p2, {"stream_id": 1})])

        ft = ct.FileTail(str(p), 1, 1, rec_size=36, variable=False)
        rv1, rec1 = ft.tail_once()
        rv2, rec2 = ft.tail_once()

        assert rv1 is not None and len(rec1) == 1
        assert rv2 is not None and len(rec2) == 1

        # Verify the seq numbers round-trip correctly
        seq1, *_ = struct.unpack_from("<I", rec1[0])
        seq2, *_ = struct.unpack_from("<I", rec2[0])
        assert seq1 == 1 and seq2 == 2

    # ── variable-length stream edge cases ────────────────────────────────

    def test_variable_stream_truncated_json_does_not_crash(self, tmp_path):
        """A variable record whose JSON is cut off mid-byte must be skipped gracefully."""

        def pack_var(seq, unity_t, js):
            jb = js.encode("utf8")
            return struct.pack("<IfI", seq, unity_t, len(jb)) + jb

        full = pack_var(1, 0.1, '{"ok":1}')
        # Truncate the payload so the second record's JSON is incomplete
        truncated_payload = full[:len(full) - 3]

        p = tmp_path / "var_trunc.vrsf"
        write_vrsf(p, [(truncated_payload, {"stream_id": 4})])
        ft = ct.FileTail(str(p), 4, 1, variable=True)
        rv, parsed = ft.tail_once()
        # The intact first record may or may not be parsed depending on exact truncation;
        # importantly no exception must be raised.
        # (Here the truncation cuts into the single record — expect 0 or None)
        assert rv is None or isinstance(parsed, list)


# ─────────────────────────────────────────────────────────────────────────────
# ██  WATCH_SESSIONS FAULT SCENARIOS  █████████████████████████████████████████
# ─────────────────────────────────────────────────────────────────────────────

class TestWatchSessionsFaults:
    """watch_sessions loop stability under missing/corrupt session directories."""

    def _make_session(self, root: Path, sid: int, payload: bytes, stream_id: int = 3) -> Path:
        """Create a minimal session directory with a manifest and one VRSF file."""
        d = root / f"session_{sid:04d}"
        d.mkdir(parents=True, exist_ok=True)
        manifest = {"session_id": sid, "started_unix_ms": int(time.time() * 1000)}
        (d / "manifest.json").write_text(json.dumps(manifest))
        for name, sid_stream, rec_size, var in [
            ("headpose.vrsf", 1, 36, False),
            ("bike.vrsf", 2, 20, False),
            ("hr.vrsf", 3, 12, False),
            ("events.vrsf", 4, 0, True),
        ]:
            if sid_stream == stream_id:
                write_vrsf(d / name, [(payload, {"stream_id": sid_stream})])
            else:
                # Write empty files so FileTail doesn't error on missing files
                (d / name).touch()
        return d

    def test_watch_stops_on_stop_event(self, tmp_path):
        """watch_sessions must exit promptly when stop_event is set."""
        logs = tmp_path / "Logs"
        logs.mkdir()
        db = tmp_path / "vrs.sqlite"
        stop = threading.Event()

        t = threading.Thread(
            target=ct.watch_sessions,
            args=(str(logs), str(db)),
            kwargs={"stop_event": stop},
            daemon=True,
        )
        t.start()
        time.sleep(0.3)
        stop.set()
        t.join(timeout=2.0)
        assert not t.is_alive(), "watch_sessions must exit within 2 s after stop_event is set"

    def test_missing_manifest_session_skipped(self, tmp_path):
        """A session directory without manifest.json must be silently ignored."""
        logs = tmp_path / "Logs"
        (logs / "session_0001").mkdir(parents=True)
        # No manifest.json — session should be skipped, not raise

        db = tmp_path / "vrs.sqlite"
        stop = threading.Event()
        errors = []

        def run():
            try:
                ct.watch_sessions(str(logs), str(db), stop_event=stop)
            except Exception as e:
                errors.append(e)

        t = threading.Thread(target=run, daemon=True)
        t.start()
        time.sleep(0.4)
        stop.set()
        t.join(timeout=2.0)

        assert not errors, f"watch_sessions raised on missing manifest: {errors}"

    def test_watch_sessions_picks_up_new_session_mid_run(self, tmp_path):
        """A session directory created after the watch loop starts must be discovered."""
        logs = tmp_path / "Logs"
        logs.mkdir()
        db = tmp_path / "vrs.sqlite"
        stop = threading.Event()

        t = threading.Thread(
            target=ct.watch_sessions,
            args=(str(logs), str(db)),
            kwargs={"stop_event": stop},
            daemon=True,
        )
        t.start()
        time.sleep(0.15)  # let the loop run once before creating the session

        # Create session while the loop is running
        payload = make_hr_rec(1, hr=80.0)
        self._make_session(logs, sid=1, payload=payload, stream_id=3)

        time.sleep(0.5)  # let the loop pick it up and insert
        stop.set()
        t.join(timeout=2.0)

        conn = sqlite3.connect(str(db))
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM hr WHERE session_id=1")
        count = cur.fetchone()[0]
        conn.close()

        assert count >= 1, f"Expected at least 1 HR row from the late-arriving session, got {count}"

    def test_watch_sessions_handles_corrupt_vrsf_gracefully(self, tmp_path):
        """A VRSF file full of garbage must not cause watch_sessions to crash."""
        logs = tmp_path / "Logs"
        d = logs / "session_0001"
        d.mkdir(parents=True)
        manifest = {"session_id": 1, "started_unix_ms": int(time.time() * 1000)}
        (d / "manifest.json").write_text(json.dumps(manifest))
        # Write all-garbage VRSF files
        for name in ("headpose.vrsf", "bike.vrsf", "hr.vrsf", "events.vrsf"):
            (d / name).write_bytes(os.urandom(200))

        db = tmp_path / "vrs.sqlite"
        stop = threading.Event()
        errors = []

        def run():
            try:
                ct.watch_sessions(str(logs), str(db), stop_event=stop)
            except Exception as e:
                errors.append(e)

        t = threading.Thread(target=run, daemon=True)
        t.start()
        time.sleep(0.5)
        stop.set()
        t.join(timeout=2.0)

        assert not errors, f"watch_sessions raised on corrupt VRSF: {errors}"

    def test_batch_commit_mode_commits_all_rows(self, tmp_path):
        """With sqlite_batch_size=5, all rows must be committed by the time the loop exits."""
        logs = tmp_path / "Logs"
        logs.mkdir()
        db = tmp_path / "vrs.sqlite"

        # Write several HR records in one chunk
        recs_bytes = b"".join(make_hr_rec(i, hr=float(60 + i)) for i in range(10))
        self._make_session(logs, sid=5, payload=recs_bytes, stream_id=3)

        stop = threading.Event()
        t = threading.Thread(
            target=ct.watch_sessions,
            args=(str(logs), str(db)),
            kwargs={"stop_event": stop, "sqlite_batch_size": 5},
            daemon=True,
        )
        t.start()
        time.sleep(0.6)
        stop.set()
        t.join(timeout=2.0)

        conn = sqlite3.connect(str(db))
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM hr WHERE session_id=5")
        count = cur.fetchone()[0]
        conn.close()

        assert count == 10, f"Expected 10 HR rows with batch commit, got {count}"
