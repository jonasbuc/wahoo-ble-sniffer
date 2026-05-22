#!/usr/bin/env python3
"""
bike_bridge.py — BLE + Arduino → Unity WebSocket bridge
=========================================================
Canonical real-time bridge.  Runs a WebSocket server (default port 8765)
that streams heart-rate data from a Wahoo TICKR FIT and relays UDP trigger
events from the Arduino to any number of Unity clients.

Modes
-----
* **Mock mode** (default / ``--mock``): generates simulated HR data with a
  sine-wave variation and random noise so the Unity scene can be tested
  without any physical hardware.
* **Live mode** (``--live``): scans for a Wahoo TICKR via Bleak, subscribes
  to Heart Rate GATT notifications, and forwards the real BPM to clients.

Wire format
-----------
Every broadcast frame is a 12-byte binary struct:

  ``struct.pack("di", timestamp, hr)``

  +-----------+--------+-------+-------------------------------------------+
  | Field     | Type   | Bytes | Notes                                     |
  +===========+========+=======+===========================================+
  | timestamp | double |   8   | Unix epoch seconds (float)                |
  | hr        | int32  |   4   | Heart rate in BPM                         |
  +-----------+--------+-------+-------------------------------------------+

Bike data (speed, cadence, steering, brakes) comes from the Arduino over UDP
and is forwarded to Unity clients as JSON event messages — it is NOT packed
into the binary frame.

UDP trigger listener
--------------------
The server also binds a UDP socket (default 127.0.0.1:5005).  The Arduino
(or any other sender) can send either:

* Plain ASCII strings like ``HALL_HIT``, ``SWITCH_HIT`` — mapped to canonical
  event names and broadcast as JSON.
* JSON objects ``{"event": "...", ...}`` — forwarded as-is.

All UDP events are broadcast to every connected WebSocket client as JSON.

Robustness features
-------------------
- Validates binary frame length before unpacking
- Catches struct errors and logs parse problems without crashing
- Graceful per-client error handling — one bad client cannot kill others
- Exponential reconnect backoff for BLE
- Periodic battery keepalive reads to prevent BLE supervision timeouts
- Ping loop to detect and evict dead WebSocket connections
"""

import argparse
import asyncio
import json
import logging
import math
import random
import struct
import time
from typing import Any, Optional, Set, Tuple

try:
    import websockets
except Exception:
    raise

LOG = logging.getLogger("wahoo_bridge")

# ── Optional BLE support (bleak) ─────────────────────────────────────────────
# bleak is not a hard dependency so the bridge can run in mock mode without it.
HAVE_BLEAK = False
try:
    from bleak import BleakClient, BleakScanner
    HAVE_BLEAK = True
except Exception:
    HAVE_BLEAK = False


class MockCyclingData:
    """Generates simulated HR data for testing without hardware.

    The heart rate oscillates around a ``base_hr`` value using a sine wave
    (period ~31 s) plus small random noise, mimicking a realistic HR trace.
    Bike data (speed, cadence, steering, brakes) comes from the Arduino and
    is not simulated here.
    """

    def __init__(self):
        self.time_offset = time.time()   # reference epoch for elapsed-time calculations
        self.base_hr = 140               # BPM — centre of the simulated HR range

    def get_binary_frame(self):
        """Return a 12-byte binary frame with simulated HR data.

        Format: ``struct.pack("di", timestamp, hr)``
        """
        now = time.time()
        elapsed = now - self.time_offset

        # Sine-wave HR variation with period ≈ 31 s (2π / 0.2)
        hr_variation = math.sin(elapsed * 0.2) * 8.0
        micro_noise  = random.uniform(-2.0, 2.0)
        hr = max(40, int(self.base_hr + hr_variation + micro_noise))

        # Pack into the 12-byte wire format: d(8) + i(4) = 12 bytes
        return struct.pack("di", now, int(hr))


class WahooBridgeServer:
    """WebSocket server that bridges Wahoo BLE data to Unity clients.

    Lifecycle
    ---------
    ``start()`` is the entry point.  It:
      1. Optionally launches a BLE background task (``_start_ble``) when
         ``--live`` is passed and bleak is installed.
      2. Opens the WebSocket server (``websockets.serve``).
      3. Binds a UDP socket for external trigger events from Arduino/Unity.
      4. Runs ``broadcast_loop`` and ``ping_loop`` concurrently.

    Each Unity client that connects calls ``register()``, which:
      - Sends a JSON handshake announcing the protocol version.
      - Forwards any ``"event"`` JSON messages from the client to all others.

    Attributes
    ----------
    clients          : set of open WebSocket connections
    _ble_connected   : True while a live HR notification subscription is active
    _hr_queue        : Queue of ``(timestamp, hr)`` tuples from the BLE callback thread
    _ble_task        : asyncio Task for the BLE connect/reconnect loop
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8765,
        use_binary: bool = True,
        mock: bool = False,
        udp_host: str = "127.0.0.1",
        udp_port: int = 5005,
        ble_address: Optional[str] = None,
        keepalive_interval: float = 10.0,
        base_backoff: float = 1.0,
        max_backoff: float = 30.0,
        scan_timeout: float = 12.0,
        spawn_interval: Optional[float] = None,
        max_reconnect_attempts: int = 0,
    ):
        self.host = host
        self.port = port
        self.use_binary = use_binary   # True = send binary frames; False = send JSON
        self.mock = mock               # True = use MockCyclingData; False = use BLE
        self.udp_host = udp_host       # Host to bind the UDP trigger listener on
        self.udp_port = udp_port       # Port to bind the UDP trigger listener on
        self.ble_address = ble_address # Optional BLE device address to connect to directly
        self.clients: Set[Any] = set() # Active WebSocket connections
        self.running = False
        self.mockgen = MockCyclingData()   # Simulated data generator (used in mock mode)
        # ── BLE state ────────────────────────────────────────────────────────
        # hr_handler (a bleak callback that may run on a worker thread) places
        # (timestamp, hr) tuples onto this Queue using
        # loop.call_soon_threadsafe(queue.put_nowait, ...).
        # broadcast_loop drains the Queue on the event-loop thread — no shared
        # mutable fields, no flag races.
        self._hr_queue: asyncio.Queue = asyncio.Queue(maxsize=32)
        self._ble_connected: bool = False  # True only while a live subscription is active
        self._ble_task: Optional[asyncio.Task] = None
        # ── Reconnect policy ─────────────────────────────────────────────────
        self.keepalive_interval = keepalive_interval  # seconds between battery reads
        self.base_backoff = base_backoff               # initial backoff (seconds)
        self.max_backoff = max_backoff                 # cap on backoff (seconds)
        self.scan_timeout = scan_timeout               # BLE scan timeout (seconds)
        self.spawn_interval = spawn_interval           # seconds between auto spawn events
        # max_reconnect_attempts: 0 = retry forever (default).
        # Set > 0 to cap total consecutive failures before giving up.
        self.max_reconnect_attempts = max_reconnect_attempts

    async def register(self, ws: Any):
        """Handle a single WebSocket client for its entire lifetime.

        Called by ``websockets.serve`` for every new connection.
        Steps:
          1. Add the websocket to the ``clients`` set.
          2. Send a JSON handshake so the client knows the protocol.
          3. Listen for incoming messages — JSON events are forwarded to all
             other clients (e.g. Hall-effect trigger events from Unity).
          4. On disconnect/error, remove the client from the set.
        """
        try:
            self.clients.add(ws)
            LOG.info("Client connected %s", ws.remote_address)

            # Handshake: tell the client which protocol variant is in use
            handshake = json.dumps(
                {
                    "protocol": "binary" if self.use_binary else "json",
                    "version": "1.0",
                    "modes": ["hr", "triggers"],
                }
            )
            await ws.send(handshake)

            # Receive messages from this client and proxy event JSON to all others
            async for message in ws:
                try:
                    if isinstance(message, str):
                        # Guard against oversized payloads from rogue/buggy clients.
                        # 4 096 bytes is far more than any legitimate event JSON needs.
                        if len(message) > 4096:
                            LOG.warning(
                                "Dropping oversized message from %s (%d bytes > 4096 limit)",
                                ws.remote_address, len(message),
                            )
                            continue
                        try:
                            data = json.loads(message)
                        except Exception:
                            data = None
                        if data and isinstance(data, dict) and "event" in data:
                            # Client sent an event (e.g. spawn, hall_hit) — relay it
                            await self.broadcast_json(data, exclude=ws)
                        # Non-event JSON is silently ignored
                    else:
                        # Binary data from clients is not expected in this direction
                        pass
                except Exception as e:
                    LOG.warning(
                        "Error handling message from %s: %s: %s",
                        ws.remote_address, type(e).__name__, e,
                    )
        except Exception as e:
            LOG.warning(
                "Unexpected error on client connection from %s – closing: %s: %s",
                ws.remote_address, type(e).__name__, e,
            )
        finally:
            # Always clean up, even if an exception occurred mid-connection
            if ws in self.clients:
                self.clients.discard(ws)
                LOG.info("Client disconnected %s", ws.remote_address)

    async def broadcast_loop(self):
        """Broadcast live HR data to all connected clients.

        **Mock mode** — generates a fresh simulated frame every tick at ~20 Hz
        so the Unity client sees smooth, varied data during development.

        **Live mode** — consumes ``(timestamp, hr)`` tuples from ``_hr_queue``,
        which is filled by ``hr_handler`` via ``loop.call_soon_threadsafe``.
        This means:

        * No shared mutable state between the BLE callback thread and the
          event-loop thread — the Queue is the only crossing point, so the
          flag-race class of bug is eliminated entirely.
        * Frames are sent only when the Wahoo TICKR delivers a real BLE
          notification (~1 Hz), never from stale cached values.
        * Multiple fast notifications are not lost — each one is enqueued
          individually and consumed in order.
        """
        LOG.info("Starting broadcast loop on ws://%s:%d", self.host, self.port)
        self.running = True
        last_log = 0
        try:
            while self.running:
                message = None
                if self.clients:
                    if self.mock:
                        # Mock mode: generate a fresh simulated frame every tick (~20 Hz)
                        message = self.mockgen.get_binary_frame()
                    else:
                        # Live mode: drain one item from the Queue if available.
                        # get_nowait() never blocks; returns None path via exception.
                        try:
                            ts, hr = self._hr_queue.get_nowait()
                            message = struct.pack("di", ts, hr)
                        except asyncio.QueueEmpty:
                            message = None

                    if message is not None:
                        # Send to every client; on any error remove the offending client
                        for c in list(self.clients):
                            try:
                                await c.send(message)
                            except Exception as exc:
                                LOG.info(
                                    "Removing dead broadcast client %s: %s: %s",
                                    getattr(c, "remote_address", "<unknown>"),
                                    type(exc).__name__, exc,
                                )
                                try:
                                    self.clients.discard(c)
                                except Exception:
                                    pass

                    # Log HR once per wall-clock second (avoids log flooding)
                    now = int(time.time())
                    if now != last_log and message is not None:
                        last_log = now
                        try:
                            if len(message) >= 12:
                                _ts, hr = struct.unpack("di", message[:12])
                                LOG.info("HR:%dbpm", hr)
                        except struct.error:
                            LOG.debug("Could not parse broadcast frame for logging")

                # ~20 Hz poll cadence — in live mode most iterations are no-ops
                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            LOG.info("Broadcast loop cancelled")

    async def start(self):
        """Start the full server stack.

        1. Launch the BLE task (if ``--live`` and bleak is available).
        2. Open the WebSocket server (binds ``self.host:self.port``).
        3. Bind a UDP socket for external trigger events.
        4. Run ``broadcast_loop`` + ``ping_loop`` concurrently until cancelled.
        5. On shutdown: cancel tasks, close the UDP transport.
        """
        LOG.info(
            "Starting WahooBridgeServer (mock=%s) on %s:%d",
            self.mock,
            self.host,
            self.port,
        )
        # Start BLE task when bleak is available, guarding against duplicate tasks.
        # If start() is ever called twice, a second task must not be created while
        # the first is still running — both would scan and subscribe concurrently,
        # leading to duplicate callbacks and double-writes to _ble_hr.
        if HAVE_BLEAK and not self.mock:
            if self._ble_task is not None and not self._ble_task.done():
                LOG.warning(
                    "BLE task already running (task=%s); skipping duplicate start",
                    self._ble_task.get_name(),
                )
            else:
                try:
                    self._ble_task = asyncio.create_task(
                        self._start_ble(), name="ble_connect_loop"
                    )
                except Exception:
                    LOG.exception("Failed to start BLE task")

        async with websockets.serve(self.register, self.host, self.port):
            # Start UDP listener for trigger events (Arduino/Unity)
            udp_transport = None
            try:
                loop = asyncio.get_running_loop()
                udp_transport, udp_protocol = await loop.create_datagram_endpoint(
                    lambda: WahooBridgeServer._UDPProtocol(self),
                    local_addr=(self.udp_host, self.udp_port),
                )
                self._udp_transport = udp_transport
                LOG.info("UDP event listener bound to %s:%d", self.udp_host, self.udp_port)
            except Exception as exc:
                LOG.warning(
                    "Failed to bind UDP event listener on %s:%d – %s: %s  "
                    "(Arduino trigger events will NOT be forwarded to Unity clients)",
                    self.udp_host, self.udp_port, type(exc).__name__, exc,
                )

            # Run broadcast loop and ping loop concurrently while server context is active
            ping_task = asyncio.create_task(self.ping_loop(), name="ping_loop")
            broadcast_task = asyncio.create_task(self.broadcast_loop(), name="broadcast_loop")
            spawn_task = asyncio.create_task(self.spawn_loop(), name="spawn_loop")

            def _task_done(task: asyncio.Task) -> None:
                if task.cancelled():
                    return  # normal shutdown via cancel()
                exc = task.exception() if not task.cancelled() else None
                if exc is not None:
                    LOG.critical(
                        "Background task '%s' crashed unexpectedly: %s: %s  "
                        "(ws://%s:%d may now be degraded – the other tasks will also be cancelled)",
                        task.get_name(), type(exc).__name__, exc,
                        self.host, self.port,
                        exc_info=exc,
                    )

            ping_task.add_done_callback(_task_done)
            broadcast_task.add_done_callback(_task_done)
            spawn_task.add_done_callback(_task_done)

            try:
                await asyncio.gather(ping_task, broadcast_task, spawn_task)
            finally:
                ping_task.cancel()
                broadcast_task.cancel()
                spawn_task.cancel()
                # Close UDP transport if opened
                try:
                    if getattr(self, "_udp_transport", None):
                        self._udp_transport.close()
                except Exception:
                    pass

    # Backwards-compatibility alias used by legacy tests and scripts.
    start_server = start

    async def broadcast_json(self, data: dict, exclude: Optional[Any] = None):
        """Broadcast a JSON-serialisable dict to every connected client.

        Args:
            data:    The dict to serialise and send.
            exclude: If provided, skip this specific websocket (used to avoid
                     echoing a message back to the sender).
        """
        text = json.dumps(data)
        for c in list(self.clients):
            if c is exclude:
                continue
            try:
                await c.send(text)
            except Exception as exc:
                LOG.info(
                    "Removing dead JSON broadcast client %s: %s: %s",
                    getattr(c, "remote_address", "<unknown>"),
                    type(exc).__name__, exc,
                )
                try:
                    self.clients.discard(c)
                except Exception:
                    pass

    # ── UDP trigger listener ─────────────────────────────────────────────────
    # The Arduino (or any Unity script) can send plain ASCII strings
    # (e.g. "HALL_HIT", "SWITCH_HIT") or JSON objects to UDP port 5005.
    # The listener normalises them into JSON event dicts and broadcasts
    # them to all WebSocket clients.
    #
    # Expected ASCII trigger strings:
    #   HALL_HIT   / HIT        → {"event": "hall_hit",   ...}
    #   SWITCH_HIT / Switch HIT → {"event": "switch_hit", ...}
    #   <anything else>         → {"event": "<raw text>", ...}
    #
    # JSON objects are forwarded as-is (extra keys are preserved).
    # Every relayed message also gets "source", "addr", and "timestamp" keys.
    class _UDPProtocol(asyncio.DatagramProtocol):
        """asyncio protocol that receives UDP datagrams and relays them as JSON events."""

        def __init__(self, server: "WahooBridgeServer"):
            self.server = server

        def datagram_received(self, data: bytes, addr: Tuple[str, int]):
            """Called by asyncio when a UDP datagram arrives."""
            # Reject oversized datagrams — a legitimate Arduino trigger message
            # is at most a few dozen bytes.  Anything > 1024 bytes is either a
            # misconfigured sender or a malicious flood; drop it immediately.
            if len(data) > 1024:
                LOG.warning(
                    "Dropping oversized UDP datagram from %s:%d (%d bytes > 1024 limit)",
                    addr[0], addr[1], len(data),
                )
                return
            try:
                text = data.decode("utf-8", errors="ignore").strip()
            except Exception:
                text = ""
            # Dispatch async handling without blocking the protocol callback
            try:
                asyncio.create_task(self._handle(text, addr))
            except Exception as exc:
                LOG.warning(
                    "Could not dispatch UDP datagram from %s:%d – %s: %s",
                    addr[0], addr[1], type(exc).__name__, exc,
                )

        async def _handle(self, text: str, addr: Tuple[str, int]):
            """Parse the raw UDP text and broadcast it as a JSON event."""
            if not text:
                return

            # If the sender already provided valid JSON, use it directly
            data = None
            if text.startswith("{"):
                try:
                    data = json.loads(text)
                except Exception:
                    data = None

            if data is None:
                # Map known ASCII trigger strings to canonical event names
                mapping = {
                    "HALL_HIT":   "hall_hit",
                    "HIT":        "hall_hit",
                    "SWITCH_HIT": "switch_hit",
                    "Switch HIT": "switch_hit",
                }
                evt = mapping.get(text, text)   # fall back to the raw string as event name
                data = {
                    "event": evt,
                    "raw":   text,
                }

            # Attach metadata so clients can filter by source
            data.setdefault("source",    "udp")
            data.setdefault("addr",      f"{addr[0]}:{addr[1]}")
            data.setdefault("timestamp", time.time())

            try:
                await self.server.broadcast_json(data)
                LOG.info("Relayed UDP event to %d clients: %s", len(self.server.clients), data.get("event"))
            except Exception as exc:
                LOG.warning(
                    "Failed to broadcast UDP event '%s' to clients: %s: %s",
                    data.get("event"), type(exc).__name__, exc,
                )

    # ── BLE helpers ──────────────────────────────────────────────────────────

    async def _start_ble(self):
        """Scan for a Wahoo TICKR and stream HR notifications into ``self._ble_hr``.

        This coroutine runs for the entire server lifetime.  It:
          1. Scans for nearby BLE devices.
          2. Picks the device matching ``self.ble_address`` (if set) or the
             first device whose name contains "tickr" (case-insensitive).
          3. Connects via BleakClient and subscribes to the HR measurement
             characteristic (UUID ``0x2A37``).
          4. Performs periodic battery-level reads as a keepalive to prevent
             the BLE link from timing out on Windows and macOS.
          5. On disconnect, applies exponential backoff and retries from step 1.

        ``hr_handler`` enqueues ``(timestamp, hr)`` tuples onto ``_hr_queue``
        which ``broadcast_loop`` drains on the event-loop thread.
        """
        if not HAVE_BLEAK:
            LOG.info("Bleak not available; live BLE mode disabled")
            return

        # Attempt counter used to calculate exponential backoff delay
        attempt = 0
        HR_UUID = "00002a37-0000-1000-8000-00805f9b34fb"  # Heart Rate Measurement char
        # Scan timeout: TICKR FIT may take 8-10 s to start advertising after idle.
        # Use self.scan_timeout (default 12 s, configurable via --scan-timeout).
        SCAN_TIMEOUT = self.scan_timeout
        while True:
            attempt += 1
            try:
                target = None

                # ── Fast path: known address → skip full scan ─────────────────
                # If the user provided --ble-address we can connect directly
                # without a discovery scan, which is significantly faster on
                # reconnect and avoids missing the device during a short scan.
                if self.ble_address:
                    LOG.info(
                        "Connecting directly to known address %s (attempt %d)",
                        self.ble_address, attempt,
                    )
                    # BleakScanner.find_device_by_address stops as soon as the
                    # device is found instead of waiting the full timeout.
                    try:
                        target = await BleakScanner.find_device_by_address(
                            self.ble_address, timeout=SCAN_TIMEOUT
                        )
                    except Exception:
                        LOG.debug("find_device_by_address failed; falling back to full scan")

                # ── Slow path: scan for any TICKR ─────────────────────────────
                if target is None:
                    LOG.info("Scanning for TICKR devices... (attempt %d, timeout=%.0fs)",
                             attempt, SCAN_TIMEOUT)
                    # find_device_by_filter stops as soon as the predicate matches,
                    # so we don't waste the full SCAN_TIMEOUT when the device is
                    # nearby and advertising quickly.
                    try:
                        target = await BleakScanner.find_device_by_filter(
                            lambda d, _adv: "tickr" in (d.name or "").lower(),
                            timeout=SCAN_TIMEOUT,
                        )
                    except Exception:
                        # Older bleak versions lack find_device_by_filter — fall
                        # back to the classic discover() call.
                        LOG.debug("find_device_by_filter not available; using discover()")
                        devices = await BleakScanner.discover(timeout=SCAN_TIMEOUT)
                        for d in devices:
                            if "tickr" in (getattr(d, "name", "") or "").lower():
                                target = d
                                break

                if target is None:
                    LOG.warning(
                        "No TICKR device found during scan (attempt %d); will retry after backoff",
                        attempt,
                    )
                    raise RuntimeError("no_tickr_found")

                LOG.info(
                    "Attempting BLE connect to %s (%s)",
                    getattr(target, "name", None),
                    getattr(target, "address", target),
                )
                async with BleakClient(target) as client:
                    LOG.info("Connected to BLE device %s", getattr(target, "address", target))

                    disconnected_event = asyncio.Event()

                    # Register a disconnected callback.
                    # bleak ≥ 0.20 passes the callback to the BleakClient
                    # constructor; older versions expose set_disconnected_callback().
                    def _on_disc(_client):
                        LOG.warning(
                            "BLE device disconnected: %s",
                            getattr(target, "address", target),
                        )
                        try:
                            disconnected_event.set()
                        except Exception:
                            pass

                    try:
                        set_disc = getattr(client, "set_disconnected_callback", None)
                        if callable(set_disc):
                            set_disc(_on_disc)
                        elif hasattr(client, "disconnected_callback"):
                            # bleak ≥ 0.20 style — assign attribute directly
                            client.disconnected_callback = _on_disc
                    except Exception as exc:
                        LOG.warning(
                            "Could not register BLE disconnected callback on %s: %s: %s"
                            " — disconnect detection will rely on is_connected polling only",
                            getattr(target, "address", target), type(exc).__name__, exc,
                        )

                    # Capture the running event loop NOW (on the asyncio thread) so
                    # hr_handler can use call_soon_threadsafe even when bleak invokes
                    # the callback on a worker thread.  asyncio.get_event_loop() from
                    # a non-asyncio thread is deprecated in Python 3.10 and raises
                    # RuntimeError in Python 3.12, which would silently drop all HR data.
                    _ble_loop = asyncio.get_running_loop()

                    def hr_handler(sender, data: bytes):
                        """Parse a raw HR notification and enqueue it for broadcast_loop.

                        HR Measurement byte layout (Bluetooth spec §3.106):
                          Byte 0 bit 0: 0 → HR is uint8 at byte 1
                                        1 → HR is uint16 LE at bytes 1-2

                        This callback may be invoked on a bleak worker thread.
                        We therefore NEVER write to shared server state directly.
                        Instead we schedule a put_nowait onto ``_hr_queue`` via
                        ``call_soon_threadsafe`` so the enqueue happens on the
                        event-loop thread, which is the only thread that may
                        safely mutate asyncio data structures.

                        Range validation: values outside 20–250 bpm are rejected
                        before reaching the queue.  Physiologically impossible
                        readings (e.g. corrupted packet giving hr=0 or hr=65535)
                        would otherwise appear verbatim in Unity and the DB.
                        """
                        try:
                            flags = data[0]
                            hr_format = flags & 0x01  # bit 0 selects uint8 vs uint16
                            if hr_format == 0:
                                hr = data[1]           # 1-byte HR value
                            else:
                                hr = int.from_bytes(data[1:3], "little")  # 2-byte HR value

                            # ── Range validation ─────────────────────────────
                            # Reject physiologically impossible values before they
                            # reach the queue, the wire, or any database.
                            # Normal resting: 40–100 bpm.  Elite exercise: up to ~220.
                            # We allow 20–250 to leave headroom for unusual edge cases.
                            if not (20 <= hr <= 250):
                                LOG.warning(
                                    "BLE HR value %d bpm out of valid range [20–250] "
                                    "from %s — packet discarded",
                                    hr,
                                    getattr(sender, "uuid", "<unknown>"),
                                )
                                return

                            LOG.debug("BLE HR update: %d bpm", hr)

                            # ── Thread-safe enqueue ──────────────────────────
                            # call_soon_threadsafe guarantees the put_nowait runs
                            # on the event-loop thread even if hr_handler was called
                            # from a background thread (which bleak sometimes does).
                            # If the queue is full (>32 items, i.e. loop is behind)
                            # we drop the oldest item first so the queue never blocks
                            # and broadcast_loop always sees fresh data.
                            ts = time.time()

                            def _enqueue():
                                if self._hr_queue.full():
                                    try:
                                        self._hr_queue.get_nowait()   # drop oldest
                                        LOG.debug(
                                            "HR queue full — dropped oldest item to make room"
                                        )
                                    except asyncio.QueueEmpty:
                                        pass
                                try:
                                    self._hr_queue.put_nowait((ts, int(hr)))
                                except asyncio.QueueFull:
                                    pass  # extremely unlikely after the drain above

                            _ble_loop.call_soon_threadsafe(_enqueue)
                        except Exception as exc:
                            LOG.debug(
                                "Failed to parse HR notification (len=%d): %s: %s",
                                len(data) if data else 0, type(exc).__name__, exc,
                            )

                    # Robust service discovery (some bleak versions vary API)
                    services = []
                    try:
                        get_services = getattr(client, "get_services", None)
                        if callable(get_services):
                            try:
                                services = await get_services()
                                LOG.debug("Fetched services via client.get_services()")
                            except TypeError:
                                services = get_services()
                                LOG.debug("Fetched services via client.get_services() (sync)")
                        elif hasattr(client, "services"):
                            services = client.services
                            LOG.debug("Using client.services property")
                        else:
                            LOG.debug("No service API available on Bleak client; continuing with empty list")
                    except Exception:
                        LOG.exception("Error while fetching services on %s", getattr(target, "address", target))

                    char_uuids = []
                    for svc in services:
                        for ch in getattr(svc, "characteristics", []):
                            try:
                                char_uuids.append(getattr(ch, "uuid", str(ch)).lower())
                            except Exception:
                                pass
                    LOG.debug("Discovered characteristic UUIDs on %s: %s",
                              getattr(target, "address", target), char_uuids)

                    if not char_uuids:
                        LOG.warning(
                            "Service discovery returned no characteristics on %s "
                            "— bleak API mismatch or device not ready; will retry",
                            getattr(target, "address", target),
                        )

                    # battery characteristic for periodic keepalive reads
                    BAT_UUID = "00002a19-0000-1000-8000-00805f9b34fb"

                    if HR_UUID in char_uuids:
                        # ── Save _real_put BEFORE try so finally can always restore ──────
                        # BUG FIXED: previously defined inside try — if start_notify raised
                        # before the assignment, the finally block hit NameError.
                        _real_put = self._hr_queue.put_nowait
                        # Two-cell list updated by _timestamped_put on the event-loop thread:
                        #   [0] = wall timestamp of last HR notification (float)
                        #   [1] = total HR notifications received this session (int)
                        _last_notif_cell: list = [0.0, 0]
                        _subscribed_at = time.time()

                        # ── Keepalive / liveness config (computed once per session) ──────
                        # DESIGN: Three distinct link states we must distinguish:
                        #
                        #   HEALTHY      — transport connected + HR notifications flowing
                        #   DEGRADED     — transport connected + notifications absent
                        #   DISCONNECTED — transport gone (handled by loop exit + reconnect)
                        #
                        # A successful battery read ONLY proves the transport is alive (ATT
                        # traffic exchanged).  It does NOT prove the HR subscription is
                        # alive.  We track both axes separately.
                        keepalive_interval = self.keepalive_interval
                        if keepalive_interval > 12.0:
                            LOG.warning(
                                "keepalive_interval=%.1f s is likely too slow for Windows: "
                                "BLE supervision timeouts are typically 4–10 s.  "
                                "Use --keepalive-interval 8 or lower to ensure ATT traffic "
                                "keeps the link alive when HR notifications stop.",
                                keepalive_interval,
                            )
                        # Stale threshold: one full keepalive_interval of silence = stale.
                        # FIXED: was max(10.0, 2.5 × interval) = 37.5 s at default 15 s,
                        # which masked long notification gaps that should have warned early.
                        stale_threshold = max(5.0, keepalive_interval)

                        # After this many consecutive keepalive cycles that succeed
                        # (transport alive) but find zero new HR notifications, force a full
                        # reconnect.  This recovers the case where the subscription silently
                        # died while the BLE transport-level link remained open — a scenario
                        # that keepalive battery reads alone will never detect.
                        STALE_FORCE_RECONNECT_CYCLES = 3

                        # Prefer battery char for keepalive reads; fall back to HR char if
                        # battery was not discovered (e.g. service discovery incomplete).
                        # IMPORTANT: a read to a notify-only HR char may return a GATT
                        # "read not permitted" error, but the ATT Read Request itself still
                        # counts as link traffic and resets the OS supervision timeout.
                        keepalive_uuid = BAT_UUID if BAT_UUID in char_uuids else HR_UUID
                        _using_hr_fallback = keepalive_uuid == HR_UUID

                        try:
                            await client.start_notify(HR_UUID, hr_handler)
                            LOG.info(
                                "Subscribed to HR notifications on %s",
                                getattr(target, "address", target),
                            )
                            if _using_hr_fallback:
                                LOG.info(
                                    "Battery characteristic not found on %s — "
                                    "using HR char (0x2A37) as keepalive traffic fallback "
                                    "(GATT 'read not permitted' responses are expected)",
                                    getattr(target, "address", target),
                                )

                            # Mark as live-connected, broadcast to Unity, reset attempt
                            # counter so backoff starts fresh on the *next* disconnection.
                            self._ble_connected = True
                            attempt = 0
                            try:
                                asyncio.create_task(self.broadcast_json({
                                    "event": "ble_status",
                                    "status": "connected",
                                    "device": getattr(target, "address", str(target)),
                                    "name": getattr(target, "name", None),
                                    "timestamp": time.time(),
                                }))
                            except RuntimeError:
                                pass  # loop shutting down

                            # ── Monkey-patch queue to record last notification wall time ──
                            # Wraps put_nowait so every enqueue also updates _last_notif_cell.
                            # Runs on the event-loop thread (call_soon_threadsafe guarantees).
                            def _timestamped_put(item):
                                _last_notif_cell[0] = item[0]   # wall timestamp
                                _last_notif_cell[1] += 1        # notification count
                                _real_put(item)

                            self._hr_queue.put_nowait = _timestamped_put  # type: ignore[method-assign]

                            _stale_warned = False
                            _keepalive_fail_streak = 0
                            _stale_keepalive_cycles = 0  # keepalive-ok-but-still-stale cycles
                            _force_reconnect = False      # set True to exit loop intentionally
                            silence = 0.0                 # defensive init before first loop tick

                            # FIXED: initialise last_keep to NOW so the first keepalive fires
                            # after exactly one full interval, not immediately on tick 1
                            # (old bug: last_keep = 0.0 caused immediate first-tick fire).
                            _loop = asyncio.get_running_loop()
                            last_keep = _loop.time()

                            while (
                                client.is_connected
                                and not disconnected_event.is_set()
                                and not _force_reconnect
                            ):
                                await asyncio.sleep(1.0)
                                now_ts = _loop.time()
                                now_wall = time.time()
                                _session_notif_count = _last_notif_cell[1]

                                # ── Stale-notification detection ──────────────────────────
                                last_notif = _last_notif_cell[0]
                                silence = (
                                    (now_wall - last_notif)
                                    if last_notif > 0
                                    else (now_wall - _subscribed_at)
                                )

                                if silence > stale_threshold and not _stale_warned:
                                    LOG.warning(
                                        "[BLE DEGRADED] No HR notification for %.0f s from %s "
                                        "(session: %d notifications so far). "
                                        "BLE transport still alive. "
                                        "Possible causes: strap lost skin contact, device in "
                                        "power-save, or OS silently dropped the subscription.",
                                        silence,
                                        getattr(target, "address", target),
                                        _session_notif_count,
                                    )
                                    _stale_warned = True
                                    try:
                                        asyncio.create_task(self.broadcast_json({
                                            "event": "ble_status",
                                            "status": "degraded",
                                            "device": getattr(target, "address", str(target)),
                                            "reason": "no_hr_notifications",
                                            "silence_s": round(silence, 1),
                                            "timestamp": now_wall,
                                        }))
                                    except RuntimeError:
                                        pass

                                elif silence <= stale_threshold and _stale_warned:
                                    LOG.info(
                                        "[BLE RECOVERED] HR notifications resumed from %s "
                                        "after %.0f s gap (session total: %d)",
                                        getattr(target, "address", target),
                                        silence,
                                        _session_notif_count,
                                    )
                                    _stale_warned = False
                                    _stale_keepalive_cycles = 0
                                    try:
                                        asyncio.create_task(self.broadcast_json({
                                            "event": "ble_status",
                                            "status": "connected",
                                            "device": getattr(target, "address", str(target)),
                                            "timestamp": now_wall,
                                        }))
                                    except RuntimeError:
                                        pass

                                # ── Keepalive / transport-liveness read ───────────────────
                                if (now_ts - last_keep) >= keepalive_interval:
                                    try:
                                        _ = await client.read_gatt_char(keepalive_uuid)
                                        _keepalive_fail_streak = 0

                                        if _stale_warned:
                                            # Transport proven alive; notifications still absent.
                                            # This is the "keepalive masks dead subscription"
                                            # case that a battery read alone cannot detect.
                                            _stale_keepalive_cycles += 1
                                            LOG.warning(
                                                "[BLE KEEPALIVE-ONLY] %s read OK on %s "
                                                "but still no HR notifications "
                                                "(silence=%.0f s, stale cycle %d/%d). "
                                                "ATT transport alive; HR subscription appears dead.",
                                                "Battery" if not _using_hr_fallback else "HR-char",
                                                getattr(target, "address", target),
                                                silence,
                                                _stale_keepalive_cycles,
                                                STALE_FORCE_RECONNECT_CYCLES,
                                            )
                                            if _stale_keepalive_cycles >= STALE_FORCE_RECONNECT_CYCLES:
                                                LOG.warning(
                                                    "[BLE FORCE-RECONNECT] %d consecutive "
                                                    "keepalive cycles with no HR notifications "
                                                    "from %s (silence=%.0f s). "
                                                    "Forcing full reconnect to restore subscription.",
                                                    _stale_keepalive_cycles,
                                                    getattr(target, "address", target),
                                                    silence,
                                                )
                                                try:
                                                    asyncio.create_task(self.broadcast_json({
                                                        "event": "ble_status",
                                                        "status": "reconnecting_stale",
                                                        "device": getattr(target, "address", str(target)),
                                                        "silence_s": round(silence, 1),
                                                        "keepalive_cycles": _stale_keepalive_cycles,
                                                        "timestamp": now_wall,
                                                    }))
                                                except RuntimeError:
                                                    pass
                                                _force_reconnect = True
                                        else:
                                            LOG.debug(
                                                "[BLE HEALTHY] Keepalive %s OK on %s "
                                                "(silence=%.1f s ≤ threshold=%.1f s, "
                                                "session notifications: %d)",
                                                "battery" if not _using_hr_fallback else "HR-char",
                                                getattr(target, "address", target),
                                                silence, stale_threshold,
                                                _session_notif_count,
                                            )

                                    except Exception as exc:
                                        _keepalive_fail_streak += 1
                                        # Using HR-char fallback: a GATT "read not permitted"
                                        # error is expected for notify-only chars.  The ATT
                                        # exchange itself still resets the supervision timeout
                                        # — do not count it as a hard transport failure.
                                        exc_str = str(exc).lower()
                                        is_gatt_refuse = _using_hr_fallback and (
                                            "not permitted" in exc_str
                                            or "read not supported" in exc_str
                                            or "insufficient" in exc_str
                                        )
                                        if is_gatt_refuse:
                                            LOG.debug(
                                                "Keepalive HR-char read returned expected GATT "
                                                "error on %s (link IS alive): %s",
                                                getattr(target, "address", target), exc,
                                            )
                                            _keepalive_fail_streak = 0  # not a transport failure
                                        elif _keepalive_fail_streak >= 3:
                                            LOG.warning(
                                                "[BLE KEEPALIVE-FAILED] %s read failed %d "
                                                "times on %s: %s: %s — forcing reconnect "
                                                "(link likely dead).",
                                                "Battery" if not _using_hr_fallback else "HR-char",
                                                _keepalive_fail_streak,
                                                getattr(target, "address", target),
                                                type(exc).__name__, exc,
                                            )
                                            _force_reconnect = True
                                        else:
                                            LOG.debug(
                                                "Keepalive read failed (streak=%d/3): %s: %s",
                                                _keepalive_fail_streak,
                                                type(exc).__name__, exc,
                                            )
                                    last_keep = now_ts

                            # ── Explain why the keepalive loop exited ─────────────────────
                            _session_notif_count = _last_notif_cell[1]
                            if _force_reconnect and _stale_keepalive_cycles >= STALE_FORCE_RECONNECT_CYCLES:
                                LOG.warning(
                                    "BLE keepalive loop exited: forced reconnect after "
                                    "%d stale cycles from %s (silence=%.0f s, "
                                    "session: %d notifications)",
                                    _stale_keepalive_cycles,
                                    getattr(target, "address", target),
                                    silence,
                                    _session_notif_count,
                                )
                            elif _force_reconnect:
                                LOG.warning(
                                    "BLE keepalive loop exited: forced reconnect after "
                                    "%d keepalive failure(s) on %s",
                                    _keepalive_fail_streak,
                                    getattr(target, "address", target),
                                )
                            elif disconnected_event.is_set():
                                LOG.warning(
                                    "BLE device %s disconnected (detected via callback). "
                                    "Session: %d HR notifications received.",
                                    getattr(target, "address", target),
                                    _session_notif_count,
                                )
                            else:
                                LOG.warning(
                                    "BLE device %s disconnected (detected via is_connected "
                                    "polling). Session: %d HR notifications received.",
                                    getattr(target, "address", target),
                                    _session_notif_count,
                                )
                        finally:
                            # ── State reset on disconnect ─────────────────────────────────
                            # _real_put is defined BEFORE the try block, so this restore is
                            # always safe even if start_notify raised before the patch ran.
                            self._hr_queue.put_nowait = _real_put  # type: ignore[method-assign]

                            # Drain any queued readings from the dead session so
                            # broadcast_loop does not send old data after reconnect.
                            drained = 0
                            while not self._hr_queue.empty():
                                try:
                                    self._hr_queue.get_nowait()
                                    drained += 1
                                except asyncio.QueueEmpty:
                                    break
                            if drained:
                                LOG.debug(
                                    "Drained %d stale HR item(s) from queue after disconnect",
                                    drained,
                                )

                            self._ble_connected = False

                            # Notify Unity clients that the sensor feed has dropped.
                            try:
                                asyncio.create_task(self.broadcast_json({
                                    "event": "ble_status",
                                    "status": "disconnected",
                                    "device": getattr(target, "address", str(target)),
                                    "timestamp": time.time(),
                                }))
                            except RuntimeError:
                                pass  # event loop shutting down — not critical

                            try:
                                await client.stop_notify(HR_UUID)
                                LOG.info(
                                    "Stopped HR notifications on %s",
                                    getattr(target, "address", target),
                                )
                            except Exception:
                                LOG.debug(
                                    "Exception while stopping notify on %s",
                                    getattr(target, "address", target),
                                )
                    else:
                        LOG.warning(
                            "HR characteristic (%s) not found on %s "
                            "(discovered %d characteristic(s): %s) "
                            "— device may not support HR notifications or "
                            "service discovery failed; will retry",
                            HR_UUID,
                            getattr(target, "address", target),
                            len(char_uuids),
                            char_uuids[:5] or "none",
                        )

            except asyncio.CancelledError:
                LOG.info("BLE task cancelled")
                break
            except RuntimeError as exc:
                # Known expected condition: device not found during scan.
                # Handled with a plain log already; avoid printing a full traceback
                # for an anticipated operational state.
                if str(exc) == "no_tickr_found":
                    pass  # already logged at WARNING before raise
                else:
                    LOG.exception(
                        "BLE runtime error (attempt %d); will retry: %s", attempt, exc
                    )
            except Exception:
                LOG.exception("BLE connection loop error (attempt %d); will retry", attempt)

            # ── Max-retry hard stop ───────────────────────────────────────────
            # If max_reconnect_attempts > 0 and we have exhausted them, give up
            # rather than looping silently forever in a broken state.
            # The CRITICAL log makes it impossible to miss in any monitoring tool.
            if self.max_reconnect_attempts > 0 and attempt >= self.max_reconnect_attempts:
                LOG.critical(
                    "BLE: gave up after %d consecutive failed attempt(s) "
                    "(max_reconnect_attempts=%d). "
                    "The bridge will continue running in degraded mode — "
                    "WebSocket clients will receive no live HR data. "
                    "Restart the bridge or fix the hardware to recover.",
                    attempt, self.max_reconnect_attempts,
                )
                asyncio.create_task(self.broadcast_json({
                    "event": "ble_status",
                    "status": "failed",
                    "attempts": attempt,
                    "timestamp": time.time(),
                }))
                break

            # Exponential backoff: delay = min(max_backoff, base * 2^(attempt-1))
            backoff = min(self.max_backoff, self.base_backoff * (2 ** max(0, attempt - 1)))
            LOG.warning(
                "BLE reconnect attempt %d — retrying in %.1f s "
                "(base=%.1fs, max=%.1fs)",
                attempt, backoff, self.base_backoff, self.max_backoff,
            )
            # Broadcast a "scanning" status so Unity can show a reconnecting indicator
            asyncio.create_task(self.broadcast_json({
                "event": "ble_status",
                "status": "scanning",
                "attempt": attempt,
                "retry_in": backoff,
                "timestamp": time.time(),
            }))
            try:
                await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                LOG.info("BLE task cancelled during backoff; exiting")
                break

    async def ping_loop(self):
        """Send WebSocket pings every 10 s to detect and remove dead connections.

        If a client doesn't respond to a ping within 5 s it is considered dead,
        removed from ``self.clients``, and its connection is forcibly closed.
        """
        while True:
            await asyncio.sleep(10)
            for c in list(self.clients):
                try:
                    pong_waiter = await c.ping()
                    await asyncio.wait_for(pong_waiter, timeout=5)
                except Exception as exc:
                    addr = getattr(c, "remote_address", "<unknown>")
                    LOG.info(
                        "Ping failed for client %s – removing (%s: %s)",
                        addr, type(exc).__name__, exc,
                    )
                    try:
                        self.clients.discard(c)
                        await c.close()
                    except Exception:
                        pass

    async def spawn_loop(self):
        """Emit periodic ``spawn`` JSON events when ``spawn_interval`` is set.

        This is used in mock/testing scenarios to simulate game events (e.g.
        obstacle spawns in Unity) at a fixed cadence.  Set ``spawn_interval``
        to the number of seconds between events; ``None`` disables the loop.
        """
        if not self.spawn_interval:
            return
        while self.running:
            try:
                await asyncio.sleep(self.spawn_interval)
                await self.broadcast_json({"event": "spawn", "source": "bridge", "timestamp": time.time()})
            except asyncio.CancelledError:
                LOG.info("Spawn loop cancelled")
                break
            except Exception:
                LOG.exception(
                    "Spawn loop encountered an unexpected error – loop will continue"
                )


def parse_args():
    """Parse command-line arguments for the bridge server."""
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--host", default="0.0.0.0",
                   help="Host/interface to bind the WebSocket server on (default: 0.0.0.0 = all interfaces). "
                        "On Windows 11 with IPv6 enabled, 'localhost' may resolve to ::1 only, "
                        "causing Unity IPv4 clients to fail with connection-refused.")
    p.add_argument(
        "--live", action="store_true", help="Try to use BLE via bleak (if available)"
    )
    p.add_argument(
        "--ble-address",
        default=None,
        help="Optional BLE device address/identifier to connect directly (preferable if multiple devices present)",
    )
    p.add_argument(
        "--keepalive-interval",
        type=float,
        default=10.0,
        help=(
            "Interval (seconds) between keepalive ATT reads (battery characteristic). "
            "Default: 10 s.  Windows BLE supervision timeouts are typically 4–10 s, "
            "so values above 12 will log a WARNING. Lower to 8 or less if the TICKR "
            "drops silently when the strap loses skin contact."
        ),
    )
    p.add_argument(
        "--base-backoff",
        type=float,
        default=1.0,
        help="Base backoff (seconds) used for exponential reconnect backoff",
    )
    p.add_argument(
        "--max-backoff",
        type=float,
        default=30.0,
        help="Maximum backoff (seconds) for reconnect attempts",
    )
    p.add_argument(
        "--scan-timeout",
        type=float,
        default=12.0,
        help="BLE scan timeout in seconds (default: 12). Increase if TICKR is slow to advertise.",
    )
    p.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    p.add_argument(
        "--no-binary",
        action="store_true",
        help="Emit JSON frames instead of binary frames",
    )
    p.add_argument(
        "--spawn-interval",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Emit a JSON spawn event every N seconds (useful for testing)",
    )
    p.add_argument(
        "--max-reconnect-attempts",
        type=int,
        default=0,
        metavar="N",
        help=(
            "Maximum number of consecutive BLE reconnect attempts before giving up "
            "(0 = retry forever, the default). When the limit is reached the bridge "
            "logs CRITICAL and enters degraded mode — no live HR data."
        ),
    )
    return p.parse_args()


def main():
    """Entry point: parse args, configure logging, and run the async server."""
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    LOG.info(
        "── WahooBridgeServer starting ──────────────────────────────────────────"
    )
    LOG.info("  mode     = %s", "LIVE (BLE)" if args.live else "MOCK (simulated)")
    LOG.info("  ws       = ws://%s:%d", args.host, args.port)
    LOG.info("  udp      = %s:%d", "127.0.0.1", 5005)
    if args.ble_address:
        LOG.info("  ble addr = %s", args.ble_address)
    LOG.info("  keepalive = %.0f s%s", args.keepalive_interval,
             "  ← WARNING: may be too slow for Windows supervision timeout" if args.keepalive_interval > 12 else "")
    if args.max_reconnect_attempts:
        LOG.info("  max reconnect = %d attempts", args.max_reconnect_attempts)
    LOG.info(
        "──────────────────────────────────────────────────────────────────────────"
    )

    server = WahooBridgeServer(
        host=args.host,
        port=args.port,
        use_binary=not args.no_binary,
        mock=not args.live,
        ble_address=args.ble_address,
        keepalive_interval=args.keepalive_interval,
        base_backoff=args.base_backoff,
        max_backoff=args.max_backoff,
        scan_timeout=args.scan_timeout,
        spawn_interval=args.spawn_interval,
        max_reconnect_attempts=args.max_reconnect_attempts,
    )
    try:
        asyncio.run(server.start())
    except KeyboardInterrupt:
        LOG.info("Shutting down bridge server (KeyboardInterrupt)")
    except Exception:
        LOG.critical(
            "Bridge server crashed – ws://%s:%d is now offline",
            args.host, args.port,
            exc_info=True,
        )
        raise


if __name__ == "__main__":
    main()
