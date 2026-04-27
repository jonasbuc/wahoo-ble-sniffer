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
    clients     : set of open WebSocket connections
    _ble_hr     : most-recently-received BLE heart-rate value (None until first read)
    _ble_task   : asyncio Task for the BLE connect/reconnect loop
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
        keepalive_interval: float = 15.0,
        base_backoff: float = 1.0,
        max_backoff: float = 30.0,
        scan_timeout: float = 12.0,
        spawn_interval: Optional[float] = None,
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
        # BLE state (populated by _start_ble() once a TICKR is connected)
        self._ble_hr: Optional[int] = None
        self._ble_task: Optional[asyncio.Task] = None
        # Reconnect backoff parameters for the BLE connect loop
        self.keepalive_interval = keepalive_interval  # seconds between battery reads
        self.base_backoff = base_backoff               # initial backoff (seconds)
        self.max_backoff = max_backoff                 # cap on backoff (seconds)
        self.scan_timeout = scan_timeout               # BLE scan timeout (seconds)
        self.spawn_interval = spawn_interval           # seconds between auto spawn events (None = disabled)

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
                    LOG.debug(
                        "Error handling message from %s: %s", ws.remote_address, e
                    )
        except Exception as e:
            LOG.debug("Client handling error: %s", e)
        finally:
            # Always clean up, even if an exception occurred mid-connection
            if ws in self.clients:
                self.clients.discard(ws)
                LOG.info("Client disconnected %s", ws.remote_address)

    async def broadcast_loop(self):
        """Continuously broadcast live HR data to all connected clients at ~20 Hz.

        In *mock* mode, frames are generated by ``MockCyclingData`` each tick.
        In *live* mode, waits until ``_ble_hr`` is populated by the BLE task
        before sending anything.

        Each iteration:
          1. Pack a 12-byte ``di`` binary frame with the current HR value.
          2. Send the frame to every connected client; remove any that fail.
          3. Log the HR value once per second for monitoring.

        The loop sleeps 50 ms between iterations (~20 Hz cadence).
        """
        LOG.info("Starting broadcast loop on ws://%s:%d", self.host, self.port)
        self.running = True
        last_log = 0
        try:
            while self.running:
                message = None
                if self.clients:
                    if self.mock:
                        # Mock mode: generate a fresh simulated frame every tick
                        message = self.mockgen.get_binary_frame()
                    else:
                        # Live mode: wait until the BLE task delivers the first HR reading
                        if self._ble_hr is None:
                            await asyncio.sleep(0.05)
                            continue
                        # Build the 12-byte binary frame: d(8) + i(4) = 12 bytes
                        message = struct.pack("di", time.time(), int(self._ble_hr))

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

                    # Log HR once per wall-clock second (avoids log flooding at 20 Hz)
                    now = int(time.time())
                    if now != last_log and message is not None:
                        last_log = now
                        try:
                            if len(message) >= 12:
                                _ts, hr = struct.unpack("di", message[:12])
                                LOG.info("HR:%dbpm", hr)
                        except struct.error:
                            LOG.debug("Could not parse broadcast frame for logging")

                # ~20 Hz cadence
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
        # Start BLE task when bleak is available
        if HAVE_BLEAK:
            try:
                self._ble_task = asyncio.create_task(self._start_ble())
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
            ping_task = asyncio.create_task(self.ping_loop())
            broadcast_task = asyncio.create_task(self.broadcast_loop())
            spawn_task = asyncio.create_task(self.spawn_loop())
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
             the BLE link from timing out on macOS.
          5. On disconnect, applies exponential backoff and retries from step 1.

        ``self._ble_hr`` is updated by the ``hr_handler`` notification callback
        and read by ``broadcast_loop`` to build outgoing frames.
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
                    LOG.info("No TICKR device found during scan; will retry")
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
                    except Exception:
                        LOG.debug("Could not register disconnected callback")

                    def hr_handler(sender, data: bytes):
                        """Parse a raw HR notification and update self._ble_hr.

                        HR Measurement byte layout (Bluetooth spec §3.106):
                          Byte 0 bit 0: 0 → HR is uint8 at byte 1
                                        1 → HR is uint16 LE at bytes 1-2
                        """
                        try:
                            flags = data[0]
                            hr_format = flags & 0x01  # bit 0 selects uint8 vs uint16
                            if hr_format == 0:
                                hr = data[1]           # 1-byte HR value
                            else:
                                hr = int.from_bytes(data[1:3], "little")  # 2-byte HR value
                            self._ble_hr = int(hr)
                            LOG.debug("BLE HR update: %d", hr)
                        except Exception:
                            LOG.debug("Failed to parse HR notification")

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

                    # battery characteristic for periodic keepalive reads
                    BAT_UUID = "00002a19-0000-1000-8000-00805f9b34fb"

                    if HR_UUID in char_uuids:
                        try:
                            await client.start_notify(HR_UUID, hr_handler)
                            LOG.info("Subscribed to HR notifications on %s", getattr(target, "address", target))
                            # Reset attempt counter so backoff starts fresh on next disconnection
                            attempt = 0

                            # Stay in this loop while the device is connected.
                            # The loop also performs periodic battery reads (keepalive) to
                            # prevent macOS from dropping the BLE link after ~30 s of
                            # silence on the ATT channel.
                            # Use self.keepalive_interval (from --keepalive-interval arg).
                            keepalive_interval = self.keepalive_interval
                            last_keep = 0.0
                            while client.is_connected and not disconnected_event.is_set():
                                now_ts = asyncio.get_running_loop().time()
                                if BAT_UUID in char_uuids and (now_ts - last_keep) >= keepalive_interval:
                                    try:
                                        # Battery level read (result is discarded — we only
                                        # care about keeping the ATT connection alive)
                                        _ = await client.read_gatt_char(BAT_UUID)
                                        LOG.debug("Performed keepalive battery read")
                                    except Exception:
                                        LOG.debug("Keepalive read failed (ignored)")
                                    last_keep = now_ts

                                await asyncio.sleep(1.0)

                            if disconnected_event.is_set():
                                LOG.info("Detected disconnection via callback for %s", getattr(target, "address", target))
                        finally:
                            try:
                                await client.stop_notify(HR_UUID)
                                LOG.info("Stopped HR notifications on %s", getattr(target, "address", target))
                            except Exception:
                                LOG.debug("Exception while stopping notify on %s", getattr(target, "address", target))
                    else:
                        LOG.info("Target device does not expose HR characteristic; disconnecting")

            except asyncio.CancelledError:
                LOG.info("BLE task cancelled")
                break
            except Exception:
                LOG.exception("BLE connection loop error; will retry")

            # Exponential backoff: delay = min(max_backoff, base * 2^(attempt-1))
            backoff = min(self.max_backoff, self.base_backoff * (2 ** max(0, attempt - 1)))
            LOG.info("Retrying BLE connect in %.1f seconds", backoff)
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
        default=15.0,
        help="Interval (seconds) between keepalive battery reads to prevent supervision timeouts",
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
