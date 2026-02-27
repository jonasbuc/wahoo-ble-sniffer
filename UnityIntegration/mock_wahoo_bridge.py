#!/usr/bin/env python3
"""
Deprecated mock wrapper.

The canonical mock implementation lives at:
  UnityIntegration/python/mock_wahoo_bridge.py

This wrapper preserves the old entrypoint and will execute the canonical
script if it exists.
"""

from __future__ import annotations

import os
import runpy
import sys


def main() -> None:
    base = os.path.dirname(__file__)
    target = os.path.join(base, "python", "mock_wahoo_bridge.py")
    if os.path.exists(target):
        runpy.run_path(target, run_name="__main__")
    else:
        print("The mock bridge has moved to: UnityIntegration/python/mock_wahoo_bridge.py")
        print("Please run that script directly for mock/testing.")
        sys.exit(1)


if __name__ == "__main__":
    main()
#!/usr/bin/env python3
"""Mock Wahoo Bridge - CLI-configurable test server

Runs a WebSocket server that emits mock cycling frames in the project's
binary protocol by default and optionally emits JSON event messages (spawn)
so the GUI can display markers.
"""

import argparse
import asyncio
import json
import logging
import time
import math
import struct
from typing import Set, Any
import websockets
# Note: avoid importing WebSocketServerProtocol to reduce deprecation warnings from websockets


class MockCyclingData:
    """Simulerer cycling data med stop/start cycles"""
    
    def __init__(self):
        self.time_offset = time.time()
        self.base_power = 150
        self.base_cadence = 80
        self.base_speed = 25.0
        self.base_hr = 140
        self.cycle_duration = 20  # 20 second cycles
        self.stop_duration = 5    # 5 second stops
    
    def get_current_data(self, use_binary=True):
        """Generer realistisk cycling data med stop/start cycles"""
        elapsed = time.time() - self.time_offset
        
        # Cycle between riding and stopping
        cycle_time = elapsed % (self.cycle_duration + self.stop_duration)
        is_stopped = cycle_time > self.cycle_duration
        
        if is_stopped:
            # STOPPED - all zeros except HR
            power = 0
            cadence = 0.0
            speed = 0.0
            hr = self.base_hr - 20  # Lower HR when stopped
        else:
            # RIDING - normal variations
            power_variation = math.sin(elapsed * 0.3) * 30
            cadence_variation = math.sin(elapsed * 0.5) * 10
            speed_variation = math.sin(elapsed * 0.3) * 5
            hr_variation = math.sin(elapsed * 0.2) * 10
            
            # Add random micro-variations
            import random
            micro_noise = random.uniform(-5, 5)
            
            power = max(0, int(self.base_power + power_variation + micro_noise))
            cadence = max(0, self.base_cadence + cadence_variation)
            speed = max(0, self.base_speed + speed_variation)
            hr = max(40, int(self.base_hr + hr_variation))
        
        if use_binary:
            # Binary format: dfffi (24 bytes)
            return struct.pack('dfffi',
                time.time(),
                float(power),
                cadence,
                speed,
                hr
            )
        else:
            # JSON format
            return {
                "timestamp": time.time(),
                "power": power,
                "cadence": cadence,
                "speed": speed,
                "heart_rate": hr
            }


class MockWahooBridge:
    """WebSocket server that sends mock data using binary or JSON frames."""

    def __init__(self, port: int = 8765, use_binary: bool = True, spawn_interval: float = 7.0):
        self.port = port
        self.clients: Set[Any] = set()
        self.mock_data = MockCyclingData()
        self.running = False
        self.use_binary = use_binary
        self.spawn_interval = spawn_interval
        self.logger = logging.getLogger("mock_bridge")
    
    async def register_client(self, websocket: Any):
        """Register en Unity client"""
        # Enable TCP_NODELAY
        try:
            websocket.transport.get_extra_info('socket').setsockopt(
                __import__('socket').IPPROTO_TCP,
                __import__('socket').TCP_NODELAY,
                1
            )
        except:
            pass
        
        self.clients.add(websocket)
        print(f"✓ Unity client connected: {websocket.remote_address}")
        
        # Send handshake
        handshake = json.dumps({
            "protocol": "binary" if self.use_binary else "json",
            "version": "1.0",
            "format": "dfffi (timestamp, power, cadence, speed, hr)"
        })
        await websocket.send(handshake)
        
        try:
            async for message in websocket:
                # Simple echo/ping handler for client messages; ignore otherwise
                try:
                    await websocket.send(json.dumps({"pong": message}))
                except Exception:
                    pass
        finally:
            try:
                self.clients.remove(websocket)
            except Exception:
                pass
            self.logger.info("Unity client disconnected")
    
    async def broadcast_loop(self):
        """Send mock data kontinuerligt med stop/start cycles"""
        self.logger.info("Broadcasting mock cycling data (20s ride / 5s stop)...")

        last_log_time = 0
        last_spawn_time = 0
        spawn_interval = float(self.spawn_interval)
        spawn_counter = 0
        
        while self.running:
            if self.clients:
                message = self.mock_data.get_current_data(use_binary=self.use_binary)
                
                # Broadcast til alle clients
                if self.use_binary:
                    # Binary broadcast
                    for client in list(self.clients):
                        try:
                            await client.send(message)
                        except Exception:
                            try:
                                self.clients.discard(client)
                            except Exception:
                                pass

                    # Parse for logging
                    try:
                        parsed = struct.unpack('dfffi', message)
                        timestamp, power, cadence, speed, hr = parsed
                    except struct.error:
                        power = cadence = speed = hr = 0
                else:
                    # JSON broadcast
                    websockets.broadcast(self.clients, json.dumps(message))
                    power = message['power']
                    cadence = message['cadence']
                    speed = message['speed']
                    hr = message['heart_rate']
                
                # Log every second
                current_time = int(time.time())
                if current_time != last_log_time:
                    last_log_time = current_time
                    status = "🚴 RIDING" if power > 0 else "🛑 STOPPED"
                    print(f"{status} | Power: {power:.0f}W | "
                          f"Cadence: {cadence:.0f}rpm | "
                          f"Speed: {speed:.1f}km/h | "
                          f"HR: {hr}bpm")

                # Emit spawn events periodically while riding so GUI can display markers
                now = time.time()
                try:
                    riding = (power > 0)
                except Exception:
                    riding = False

                if riding and (now - last_spawn_time) >= spawn_interval:
                    last_spawn_time = now
                    spawn_counter += 1
                    event = {
                        "event": "spawn",
                        "entity": "car",
                        "id": f"car_{spawn_counter}",
                        "timestamp": now
                    }
                    try:
                        websockets.broadcast(self.clients, json.dumps(event))
                        self.logger.info("[EVENT] spawn -> %s at %d", event['id'], int(now))
                    except Exception:
                        self.logger.debug("Failed to broadcast event")
            
            await asyncio.sleep(0.05)  # 20Hz update rate = constant smooth data flow
    
    
    async def start_server(self):
        """Start WebSocket server"""
        self.running = True
        
        print("=" * 60)
        print("  Mock Wahoo Bridge - Test Server (BINARY PROTOCOL)")
        print("=" * 60)
        print()
        print("⚠️  Dette er MOCK DATA - ingen rigtige BLE enheder!")
        print()
        print(f"✓ WebSocket server: ws://localhost:{self.port}")
        print(f"✓ Protocol: {'BINARY (24 bytes)' if self.use_binary else 'JSON'}")
        print(f"✓ Update rate: 20 Hz")
        print(f"✓ Simulation: 20s riding → 5s stopped (cycles)")
        print()
        print("Waiting for Unity to connect...")
        print("(Tryk Ctrl+C for at stoppe)")
        print()
        
        async with websockets.serve(self.register_client, "localhost", self.port):
            await self.broadcast_loop()


async def main(port: int, use_binary: bool, spawn_interval: float):
    bridge = MockWahooBridge(port=port, use_binary=use_binary, spawn_interval=spawn_interval)

    try:
        await bridge.start_server()
    except KeyboardInterrupt:
        print()
        print("Shutting down...")
        bridge.running = False


def parse_args():
    p = argparse.ArgumentParser(prog="mock_wahoo_bridge.py")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-binary", dest="use_binary", action="store_false", help="Send JSON frames instead of binary")
    p.add_argument("--spawn-interval", type=float, default=7.0, help="Seconds between spawn events while riding")
    p.add_argument("--log-level", default="INFO")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO), format="%(asctime)s %(levelname)s %(message)s")
    print()
    print("🚴 Mock Wahoo Bridge - ZERO DETECTION TEST")
    print()
    print("Use this to test Unity integration without hardware.")
    print()
    try:
        asyncio.run(main(args.port, args.use_binary, args.spawn_interval))
    except KeyboardInterrupt:
        pass
