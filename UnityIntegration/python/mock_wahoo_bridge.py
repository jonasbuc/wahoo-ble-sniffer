#!/usr/bin/env python3
"""
Mock Wahoo Bridge - Test server for UnityIntegration

Provides a small WebSocket server that emits a simple binary frame
(format: dfffi -> timestamp, power, cadence, speed, hr) at ~20Hz.

This file is intentionally lightweight and self-contained for local
testing. It supports an optional ``spawn_interval`` which will emit
JSON "spawn" events (used by integration tests / GUI markers).
"""

import argparse
import asyncio
import json
import logging
import math
import struct
import time
from typing import Any, Optional, Set

import websockets

LOG = logging.getLogger("mock_wahoo_bridge")


class MockCyclingData:
    """Simulerer cycling data med stop/start cycles"""

    def __init__(self) -> None:
        self.time_offset = time.time()
        self.base_power = 150
        self.base_cadence = 80
        self.base_speed = 25.0
        self.base_hr = 140
        self.cycle_duration = 20  # 20 second cycles
        self.stop_duration = 5  # 5 second stops

    def get_current_data(self, use_binary: bool = True):
        """Generate realistic cycling data with ride/stop cycles."""
        elapsed = time.time() - self.time_offset
        # We only simulate heart-rate now. Other fields are zero but we keep
        # the same binary format for compatibility (dfffi).
        import random

        hr_variation = math.sin(elapsed * 0.2) * 8.0
        micro_noise = random.uniform(-2.0, 2.0)
        hr = max(40, int(self.base_hr + hr_variation + micro_noise))

        power = 0.0
        cadence = 0.0
        speed = 0.0

        if use_binary:
            # Binary format: dfffi (timestamp, power, cadence, speed, hr)
            return struct.pack(
                "dfffi", time.time(), float(power), float(cadence), float(speed), int(hr)
            )
        else:
            return {"timestamp": time.time(), "power": 0.0, "cadence": 0.0, "speed": 0.0, "heart_rate": hr}


class MockWahooBridge:
    """Simple WebSocket server that broadcasts mock cycling frames.

    If ``spawn_interval`` is set, the bridge will also periodically
    emit JSON spawn events to all connected clients.
    """

    def __init__(
        self,
        port: int = 8765,
        use_binary: bool = True,
        spawn_interval: Optional[float] = None,
    ) -> None:
        self.port = port
        self.use_binary = use_binary
        self.mock_data = MockCyclingData()
        self.running = False
        self.clients: Set[Any] = set()
        self.spawn_interval = (
            float(spawn_interval) if spawn_interval is not None else None
        )
        self._spawn_task: Optional[asyncio.Task] = None

    async def register_client(self, websocket: Any) -> None:
        """Register a client and handle incoming messages (basic echo/pong)."""
        # Try to set TCP_NODELAY where possible (best-effort)
        try:
            sock = websocket.transport.get_extra_info("socket")
            if sock is not None:
                sock.setsockopt(
                    __import__("socket").IPPROTO_TCP,
                    __import__("socket").TCP_NODELAY,
                    1,
                )
        except Exception:
            pass

        self.clients.add(websocket)
        LOG.info("Client connected: %s", getattr(websocket, "remote_address", None))

        handshake = json.dumps(
            {
                "protocol": "binary" if self.use_binary else "json",
                "version": "1.0",
                "format": "dfffi (timestamp, power, cadence, speed, hr)",
            }
        )
        await websocket.send(handshake)

        try:
            async for message in websocket:
                # Keep connection alive; echo simple pings
                try:
                    await websocket.send(json.dumps({"pong": True}))
                except Exception:
                    break
        finally:
            self.clients.discard(websocket)
            LOG.info("Client disconnected")

    async def broadcast_loop(self) -> None:
        """Broadcast mock data to connected clients at ~20Hz."""
        LOG.info("Starting broadcast loop on ws://localhost:%d", self.port)
        last_log_time = 0
        while self.running:
            if self.clients:
                message = self.mock_data.get_current_data(use_binary=self.use_binary)

                if self.use_binary:
                    for client in list(self.clients):
                        try:
                            await client.send(message)
                        except Exception:
                            self.clients.discard(client)
                        try:
                            _, power, cadence, speed, hr = struct.unpack("dfffi", message[:24])
                        except struct.error:
                            power = cadence = speed = hr = 0
                else:
                    try:
                        websockets.broadcast(self.clients, json.dumps(message))
                    except Exception:
                        pass
                now = int(time.time())
                if now != last_log_time:
                    last_log_time = now
                    # Only log heart-rate now; other fields are unused.
                    try:
                        LOG.info("HR:%dbpm", int(hr))
                    except Exception:
                        LOG.info("HR:unknown")

            await asyncio.sleep(0.05)

    async def start_server(self) -> None:
        """Start the WebSocket server and optional spawn loop."""
        self.running = True
        LOG.info("Mock Wahoo Bridge starting on ws://localhost:%d", self.port)

        try:
            if self.spawn_interval:
                self._spawn_task = asyncio.create_task(self._spawn_loop())

            async with websockets.serve(self.register_client, "localhost", self.port):
                await self.broadcast_loop()
        finally:
            if self._spawn_task:
                self._spawn_task.cancel()
                try:
                    await self._spawn_task
                except asyncio.CancelledError:
                    pass

    async def _spawn_loop(self) -> None:
        """Periodically broadcast JSON spawn events to all connected clients."""
        counter = 0
        while self.running and self.spawn_interval:
            await asyncio.sleep(self.spawn_interval)
            counter += 1
            event = {
                "event": "spawn",
                "entity": "car",
                "id": f"car_{counter}",
                "timestamp": time.time(),
                "source": "mock",
            }
            try:
                if self.clients:
                    websockets.broadcast(self.clients, json.dumps(event))
            except Exception:
                LOG.debug("Failed to broadcast spawn event")


def parse_args():
    p = argparse.ArgumentParser(prog="mock_wahoo_bridge")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument(
        "--no-binary", action="store_true", help="Emit JSON instead of binary frames"
    )
    p.add_argument(
        "--spawn-interval",
        type=float,
        default=None,
        help="Emit spawn events every N seconds",
    )
    return p.parse_args()


async def _main():
    args = parse_args()
    bridge = MockWahooBridge(
        port=args.port,
        use_binary=not args.no_binary,
        spawn_interval=args.spawn_interval,
    )
    try:
        await bridge.start_server()
    except asyncio.CancelledError:
        pass


def main():
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        LOG.info("Shutting down mock bridge")


if __name__ == "__main__":
    print("\n🚴 Mock Wahoo Bridge - ZERO DETECTION TEST\n")
    print(
        "Use this tool to test Unity integration without hardware (simulated ride/stop)."
    )
    main()
