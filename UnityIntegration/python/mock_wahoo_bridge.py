#!/usr/bin/env python3
"""
mock_wahoo_bridge.py — Lightweight mock WebSocket bridge for testing
=====================================================================
Provides a self-contained WebSocket server that emits simulated HR
frames at ~20 Hz **without** requiring any BLE hardware.

Use this when:
* Developing or testing the Unity client without a physical Wahoo device
* Running automated tests that need a live WebSocket endpoint
* Demonstrating the integration on machines without Bluetooth

Wire format (same as the real bridge)
--------------------------------------
Each broadcast frame is a 12-byte binary struct::

    struct.pack("di", timestamp, hr)

    d = double  timestamp (8 bytes, Unix epoch seconds)
    i = int32   hr        (4 bytes, BPM)

Bike data (speed, cadence, steering, brakes) comes from the Arduino over
UDP and is NOT included in binary frames — it is sent as JSON events.

Optional JSON mode
------------------
Pass ``--no-binary`` to emit JSON dicts instead of binary frames.

Optional spawn events
---------------------
Pass ``--spawn-interval N`` to also emit a JSON ``{"event": "spawn", ...}``
message every N seconds.  The GUI uses these as vertical marker lines.

Usage
-----
::

    python mock_wahoo_bridge.py [--port 8765] [--no-binary] [--spawn-interval 5]
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
    """Generates simulated HR metrics for the mock bridge.

    Heart rate oscillates around ``base_hr`` using a sine wave and
    random noise.  Bike data (speed, cadence, steering, brakes) comes
    from the Arduino and is not simulated here.
    """

    def __init__(self) -> None:
        self.time_offset = time.time()   # epoch reference for elapsed-time calculations
        self.base_hr       = 140         # BPM — midpoint of the simulated HR range

    def get_current_data(self, use_binary: bool = True):
        """Return either a 12-byte binary frame or a JSON dict.

        HR is simulated as ``base_hr ± sine_variation ± random_noise``.

        Args:
            use_binary: If True return bytes; if False return a dict.
        """
        elapsed = time.time() - self.time_offset
        import random

        # Sine-wave variation: period ≈ 31 s, amplitude ±8 BPM
        hr_variation = math.sin(elapsed * 0.2) * 8.0
        micro_noise  = random.uniform(-2.0, 2.0)
        hr = max(40, int(self.base_hr + hr_variation + micro_noise))

        if use_binary:
            # 12-byte binary frame: d(8) + i(4)
            return struct.pack("di", time.time(), int(hr))
        else:
            return {"timestamp": time.time(), "heart_rate": hr}


class MockWahooBridge:
    """WebSocket server that broadcasts simulated cycling frames.

    Lifecycle:
      1. ``start_server()`` opens the server and starts the broadcast loop.
      2. ``broadcast_loop()`` wakes every 50 ms and sends a frame to all clients.
      3. If ``spawn_interval`` is set, ``_spawn_loop()`` sends JSON spawn events.

    Attributes:
        clients:        Set of active WebSocket connections.
        mock_data:      Data generator (MockCyclingData instance).
        spawn_interval: Seconds between spawn events, or None to disable.
    """

    def __init__(
        self,
        port: int = 8765,
        use_binary: bool = True,
        spawn_interval: Optional[float] = None,
    ) -> None:
        self.port = port
        self.use_binary = use_binary          # True = binary frames; False = JSON
        self.mock_data = MockCyclingData()
        self.running = False
        self.clients: Set[Any] = set()        # All currently connected WebSocket clients
        self.spawn_interval = (
            float(spawn_interval) if spawn_interval is not None else None
        )
        self._spawn_task: Optional[asyncio.Task] = None

    async def register_client(self, websocket: Any) -> None:
        """Handle a single WebSocket client connection.

        - Tries to set TCP_NODELAY for lower latency.
        - Sends a handshake JSON message announcing the protocol.
        - Echoes any incoming messages with a ``{"pong": True}`` reply
          to keep the connection alive.
        """
        # TCP_NODELAY reduces buffering latency for small frequent frames
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
                "format": "di (timestamp, hr)",
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
        """Broadcast mock data to connected clients at ~20 Hz.

        Each iteration:
          1. Call ``mock_data.get_current_data()`` to produce a frame.
          2. Send the frame to every connected client (binary or JSON).
          3. Log the current HR once per wall-clock second.
          4. Sleep 50 ms (→ ~20 Hz cadence).

        Clients that fail to receive a frame are silently removed.
        """
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
                            _ts, hr = struct.unpack("di", message[:12])
                        except struct.error:
                            hr = 0
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
        """Open the WebSocket server and run the broadcast loop.

        Also starts the optional spawn-event loop if ``spawn_interval`` is set.
        Cleans up the spawn task on exit.
        """
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
        """Periodically emit JSON spawn events to all connected clients.

        Each event looks like::

            {"event": "spawn", "entity": "car", "id": "car_N",
             "timestamp": <epoch>, "source": "mock"}

        The GUI displays these as orange vertical marker lines on the HR graph.
        """
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
    """Parse command-line arguments for the mock bridge."""
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
