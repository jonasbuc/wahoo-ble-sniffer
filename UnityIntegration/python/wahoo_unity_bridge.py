#!/usr/bin/env python3
"""
Authoritative Wahoo BLE → WebSocket bridge

This file is the canonical bridge implementation for the project. It runs a
WebSocket server that broadcasts cycling frames to connected clients. By
default it runs a mock data generator (safe for testing). If `--live` is
specified and bleak is available, it will attempt to read BLE devices.

Robustness features:
- Validates binary frame length before unpacking
- Catches struct errors and logs parse problems without crashing
- Graceful client handling and broadcasting with cleanup
"""

import argparse
import asyncio
import json
import logging
import struct
import time
from typing import Any, Optional, Set
from typing import Tuple

try:
    import websockets
except Exception:
    raise

LOG = logging.getLogger("wahoo_bridge")

# Conditional BLE support
HAVE_BLEAK = False
try:
    from bleak import BleakClient, BleakScanner

    HAVE_BLEAK = True
except Exception:
    HAVE_BLEAK = False


class MockCyclingData:
    def __init__(self):
        self.time_offset = time.time()
        self.base_power = 150
        self.base_cadence = 80
        self.base_speed = 25.0
        self.base_hr = 140
        self.cycle_duration = 20
        self.stop_duration = 5

    def get_binary_frame(self):
        now = time.time()
        elapsed = now - self.time_offset
        cycle_time = elapsed % (self.cycle_duration + self.stop_duration)
        is_stopped = cycle_time > self.cycle_duration
        # We only care about heart rate now. Keep other fields zero for
        # compatibility with existing binary protocol (dfffi) but populate
        # only the HR value.
        import math
        import random

        # Simulate modest HR variation over time
        hr_variation = math.sin(elapsed * 0.2) * 8.0
        micro_noise = random.uniform(-2.0, 2.0)
        hr = max(40, int(self.base_hr + hr_variation + micro_noise))

        power = 0.0
        cadence = 0.0
        speed = 0.0

        # Binary format: dfffi (timestamp, power, cadence, speed, hr)
        return struct.pack("dfffi", now, float(power), float(cadence), float(speed), int(hr))


class WahooBridgeServer:
    def __init__(
        self,
        host: str = "localhost",
        port: int = 8765,
        use_binary: bool = True,
        mock: bool = False,
        udp_host: str = "127.0.0.1",
        udp_port: int = 5005,
        ble_address: Optional[str] = None,
    ):
        self.host = host
        self.port = port
        self.use_binary = use_binary
        self.mock = mock
        self.udp_host = udp_host
        self.udp_port = udp_port
        self.ble_address = ble_address
        self.clients: Set[Any] = set()
        self.running = False
        self.mockgen = MockCyclingData()
        # BLE state (populated if --live and bleak is available)
        self._ble_hr: Optional[int] = None
        self._ble_task: Optional[asyncio.Task] = None

    async def register(self, ws: Any):
        try:
            self.clients.add(ws)
            LOG.info("Client connected %s", ws.remote_address)
            # send handshake
            handshake = json.dumps(
                {
                    "protocol": "binary" if self.use_binary else "json",
                    "version": "1.0",
                    "modes": ["hr", "triggers"],
                }
            )
            await ws.send(handshake)

            # Receive messages from this client and handle them (event proxying)
            async for message in ws:
                try:
                    # If we get JSON with an "event" field, forward it to all clients
                    if isinstance(message, str):
                        try:
                            data = json.loads(message)
                        except Exception:
                            data = None
                        if data and isinstance(data, dict) and "event" in data:
                            # Broadcast event JSON to all connected clients
                            await self.broadcast_json(data, exclude=ws)
                        # otherwise ignore
                    else:
                        # Binary data from client - not expected; ignore
                        pass
                except Exception as e:
                    LOG.debug(
                        "Error handling message from %s: %s", ws.remote_address, e
                    )
        except Exception as e:
            LOG.debug("Client handling error: %s", e)
        finally:
            if ws in self.clients:
                self.clients.discard(ws)
                LOG.info("Client disconnected %s", ws.remote_address)

    async def broadcast_loop(self):
        LOG.info("Starting broadcast loop on ws://%s:%d", self.host, self.port)
        self.running = True
        last_log = 0
        try:
            while self.running:
                message = None
                if self.clients:
                    # Only broadcast real BLE-sourced measurements.
                    # If we don't yet have BLE data, skip broadcasting until available.
                    if self._ble_hr is None:
                        # Nothing to broadcast yet; wait a short time
                        await asyncio.sleep(0.05)
                        continue
                    # pack timestamp + zeroed power/cadence/speed + HR
                    message = struct.pack(
                        "dfffi", time.time(), 0.0, 0.0, 0.0, int(self._ble_hr)
                    )

                    # Broadcast with safe per-client send
                    if message is not None:
                        for c in list(self.clients):
                            try:
                                await c.send(message)
                            except Exception:
                                LOG.debug(
                                    "Removing client after send failure: %s",
                                    getattr(c, "remote_address", None),
                                )
                                try:
                                    self.clients.discard(c)
                                except Exception:
                                    pass

                    # Log once per second
                    now = int(time.time())
                    if now != last_log and message is not None:
                        last_log = now
                        try:
                            if len(message) >= 24:
                                    ts, power, cadence, speed, hr = struct.unpack(
                                        "dfffi", message[:24]
                                    )
                                    # We only publish heart-rate now; other fields are
                                    # zero for compatibility. Log HR for quick inspection.
                                    LOG.info("HR:%dbpm", hr)
                        except struct.error:
                            LOG.debug("Could not parse broadcast frame for logging")

                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            LOG.info("Broadcast loop cancelled")

    async def start(self):
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
            except Exception:
                LOG.debug("Failed to bind UDP listener on %s:%d", self.udp_host, self.udp_port)

            # Run broadcast loop and ping loop concurrently while server context is active
            ping_task = asyncio.create_task(self.ping_loop())
            broadcast_task = asyncio.create_task(self.broadcast_loop())
            try:
                await asyncio.gather(ping_task, broadcast_task)
            finally:
                ping_task.cancel()
                broadcast_task.cancel()
                # Close UDP transport if opened
                try:
                    if getattr(self, "_udp_transport", None):
                        self._udp_transport.close()
                except Exception:
                    pass

    async def broadcast_json(self, data: dict, exclude: Optional[Any] = None):
        """Broadcast a JSON dict to all connected clients (optionally excluding the sender)."""
        text = json.dumps(data)
        for c in list(self.clients):
            if c is exclude:
                continue
            try:
                await c.send(text)
            except Exception:
                LOG.debug(
                    "Error sending JSON to client, removing: %s",
                    getattr(c, "remote_address", None),
                )
                try:
                    self.clients.discard(c)
                except Exception:
                    pass

    # --- UDP listener for external trigger events (Arduino/Unity) ---
    class _UDPProtocol(asyncio.DatagramProtocol):
        def __init__(self, server: "WahooBridgeServer"):
            self.server = server

        def datagram_received(self, data: bytes, addr: Tuple[str, int]):
            try:
                text = data.decode("utf-8", errors="ignore").strip()
            except Exception:
                text = ""
            # Schedule async handling on the running loop
            try:
                asyncio.create_task(self._handle(text, addr))
            except Exception:
                # If loop not running or create_task fails, try to call broadcast directly
                pass

        async def _handle(self, text: str, addr: Tuple[str, int]):
            if not text:
                return
            # If caller sends JSON, forward as-is
            data = None
            if text.startswith("{"):
                try:
                    data = json.loads(text)
                except Exception:
                    data = None

            if data is None:
                # Map simple ASCII trigger strings to event names
                mapping = {
                    "HALL_HIT": "hall_hit",
                    "HIT": "hall_hit",
                    "SWITCH_HIT": "switch_hit",
                    "Switch HIT": "switch_hit",
                }
                evt = mapping.get(text, text)
                data = {
                    "event": evt,
                    "raw": text,
                }

            # Attach metadata
            data.setdefault("source", "udp")
            data.setdefault("addr", f"{addr[0]}:{addr[1]}")
            data.setdefault("timestamp", time.time())

            try:
                await self.server.broadcast_json(data)
                LOG.info("Relayed UDP event to %d clients: %s", len(self.server.clients), data.get("event"))
            except Exception:
                LOG.debug("Failed to broadcast UDP event: %s", data)


    # --- BLE helpers ---
    async def _start_ble(self):
        """Scan and connect to a BLE device that exposes Heart Rate measurement.

        This is a minimal implementation: it looks for the first device and
        subscribes to the HR measurement characteristic (0x2A37) if present.
        Received HR values are stored in self._ble_hr and used in broadcasts.
        """
        if not HAVE_BLEAK:
            LOG.info("Bleak not available; live BLE mode disabled")
            return

        # Long-running BLE connect loop with reconnect/backoff
        attempt = 0
        base_backoff = 1.0
        HR_UUID = "00002a37-0000-1000-8000-00805f9b34fb"
        while True:
            attempt += 1
            try:
                LOG.info("Starting BLE scan (looking for HR devices)... attempt %d", attempt)
                devices = await BleakScanner.discover(timeout=5.0)
                if not devices:
                    LOG.info("No BLE devices found during scan; will retry")
                    raise RuntimeError("no_devices")

                # If a specific address was supplied prefer it
                target = None
                if self.ble_address:
                    for d in devices:
                        if getattr(d, "address", None) == self.ble_address:
                            target = d
                            break

                # Restrict connections to Wahoo TICKR devices only (by name)
                # If a ble_address was provided above it will be used; otherwise
                # find the first device whose name contains 'tickr' (case-insensitive).
                if target is None:
                    for d in devices:
                        try:
                            name = (d.name or "").lower()
                        except Exception:
                            name = ""
                        if "tickr" in name:
                            target = d
                            break

                if target is None:
                    # No suitable TICKR device found; do not connect to arbitrary devices.
                    LOG.info("No TICKR device found during scan; will retry")
                    raise RuntimeError("no_tickr_found")

                LOG.info(
                    "Attempting BLE connect to %s (%s)",
                    getattr(target, "name", None),
                    getattr(target, "address", target),
                )
                async with BleakClient(target) as client:
                    LOG.info("Connected to BLE device %s", getattr(target, "address", target))

                    def hr_handler(sender, data: bytes):
                        try:
                            flags = data[0]
                            hr_format = flags & 0x01
                            if hr_format == 0:
                                hr = data[1]
                            else:
                                hr = int.from_bytes(data[1:3], "little")
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
                    LOG.debug("Discovered characteristic UUIDs on %s: %s", getattr(target, "address", target), char_uuids)

                    if HR_UUID in char_uuids:
                        try:
                            await client.start_notify(HR_UUID, hr_handler)
                            LOG.info("Subscribed to HR notifications on %s", getattr(target, "address", target))
                            # Reset attempt counter after a successful subscription
                            attempt = 0
                            # Keep the client alive until cancelled or an exception occurs
                            while True:
                                await asyncio.sleep(1.0)
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

            # Exponential backoff before retrying
            backoff = min(30.0, base_backoff * (2 ** max(0, attempt - 1)))
            LOG.info("Retrying BLE connect in %.1f seconds", backoff)
            try:
                await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                LOG.info("BLE task cancelled during backoff; exiting")
                break

    async def ping_loop(self):
        """Periodically ping clients to detect dead connections."""
        while True:
            await asyncio.sleep(10)
            for c in list(self.clients):
                try:
                    pong_waiter = await c.ping()
                    await asyncio.wait_for(pong_waiter, timeout=5)
                except Exception:
                    LOG.debug(
                        "Ping failed for client %s, removing",
                        getattr(c, "remote_address", None),
                    )
                    try:
                        self.clients.discard(c)
                        await c.close()
                    except Exception:
                        pass


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--host", default="localhost")
    p.add_argument(
        "--live", action="store_true", help="Try to use BLE via bleak (if available)"
    )
    p.add_argument(
        "--ble-address",
        default=None,
        help="Optional BLE device address/identifier to connect directly (preferable if multiple devices present)",
    )
    return p.parse_args()


def main():
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    args = parse_args()

    server = WahooBridgeServer(
        host=args.host,
        port=args.port,
        use_binary=True,
        mock=not args.live,
        ble_address=args.ble_address,
    )
    try:
        asyncio.run(server.start())
    except KeyboardInterrupt:
        LOG.info("Shutting down server")


if __name__ == "__main__":
    main()
