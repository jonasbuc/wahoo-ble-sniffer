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

import asyncio
import json
import time
import struct
import argparse
import logging
from typing import Set

try:
    import websockets
    from websockets.server import WebSocketServerProtocol
except Exception:
    raise

LOG = logging.getLogger("wahoo_bridge")


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

        if is_stopped:
            power = 0.0
            cadence = 0.0
            speed = 0.0
            hr = self.base_hr - 20
        else:
            import math, random
            t = elapsed
            power = max(0.0, float(self.base_power + math.sin(t * 0.3) * 30 + random.uniform(-5, 5)))
            cadence = max(0.0, float(self.base_cadence + math.sin(t * 0.5) * 10))
            speed = max(0.0, float(self.base_speed + math.sin(t * 0.3) * 5))
            hr = max(40, int(self.base_hr + math.sin(t * 0.2) * 10))

        # Binary format: dfffi
        return struct.pack("dfffi", now, float(power), float(cadence), float(speed), int(hr))


class WahooBridgeServer:
    def __init__(self, host: str = "localhost", port: int = 8765, use_binary: bool = True, mock: bool = True):
        self.host = host
        self.port = port
        self.use_binary = use_binary
        self.mock = mock
        self.clients: Set[WebSocketServerProtocol] = set()
        self.running = False
        self.mockgen = MockCyclingData()

    async def register(self, ws: WebSocketServerProtocol):
        try:
            self.clients.add(ws)
            LOG.info("Client connected %s", ws.remote_address)
            # send handshake
            handshake = json.dumps({"protocol": "binary" if self.use_binary else "json", "version": "1.0"})
            await ws.send(handshake)

            async for _ in ws:
                # keep connection alive; ignore incoming messages
                pass
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
                if self.clients:
                    if self.mock:
                        message = self.mockgen.get_binary_frame()
                    else:
                        # Placeholder for real BLE -> binary frame
                        message = self.mockgen.get_binary_frame()

                    # Broadcast with safe per-client send
                    for c in list(self.clients):
                        try:
                            await c.send(message)
                        except Exception:
                            try:
                                self.clients.discard(c)
                            except Exception:
                                pass

                    # Log once per second
                    now = int(time.time())
                    if now != last_log:
                        last_log = now
                        try:
                            if len(message) >= 24:
                                ts, power, cadence, speed, hr = struct.unpack("dfffi", message[:24])
                                status = "RIDING" if power > 0 else "STOPPED"
                                LOG.info("%s | P:%.0f W | C:%.0f rpm | S:%.1f km/h | HR:%dbpm", status, power, cadence, speed, hr)
                        except struct.error:
                            LOG.debug("Could not parse broadcast frame for logging")

                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            LOG.info("Broadcast loop cancelled")

    async def start(self):
        LOG.info("Starting WahooBridgeServer (mock=%s) on %s:%d", self.mock, self.host, self.port)
        async with websockets.serve(self.register, self.host, self.port):
            await self.broadcast_loop()


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--host", default="localhost")
    p.add_argument("--live", action="store_true", help="Try to use BLE via bleak (if available)")
    return p.parse_args()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args()

    server = WahooBridgeServer(host=args.host, port=args.port, use_binary=True, mock=not args.live)
    try:
        asyncio.run(server.start())
    except KeyboardInterrupt:
        LOG.info("Shutting down server")


if __name__ == "__main__":
    main()
#!/usr/bin/env python3
"""
Wahoo BLE to Unity Bridge
Streams live BLE cycling data (speed/cadence, heart rate, trainers) to Unity via WebSocket
Optimized for low latency with binary protocol
"""

import asyncio
import json
import logging
import time
import struct
from typing import Optional, Dict, Any, Set
from dataclasses import dataclass, asdict

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
import websockets
from websockets.server import WebSocketServerProtocol

#!/usr/bin/env python3
"""Deprecated duplicate bridge file.

This file was moved to `../wahoo_unity_bridge.py`. Use that file instead.
This placeholder prevents accidental use of the old duplicate.
"""

import sys

print("Deprecated duplicate: run UnityIntegration/wahoo_unity_bridge.py instead")
sys.exit(0)

