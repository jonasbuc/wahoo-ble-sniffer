#!/usr/bin/env python3
"""
Mock Wahoo Bridge - Til test uden rigtige enheder
Sender simulerede cycling data til Unity
UPDATED: Binary protocol + stop/start simulation
"""

import asyncio
import json
import time
import math
import struct
from typing import Set
import websockets
from websockets.server import WebSocketServerProtocol


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
    """WebSocket server der sender mock data med binary protocol

    Backwards-compatible: accepts optional spawn_interval to emit JSON
    spawn events periodically (used by integration tests).
    """

    def __init__(self, port: int = 8765, use_binary: bool = True, spawn_interval: float | None = None):
        self.port = port
        self.clients: Set[WebSocketServerProtocol] = set()
        self.mock_data = MockCyclingData()
        self.running = False
        self.use_binary = use_binary
        # If set, periodically emit JSON spawn events every spawn_interval seconds
        self.spawn_interval = float(spawn_interval) if spawn_interval is not None else None
        self._spawn_task = None
    
    async def register_client(self, websocket: WebSocketServerProtocol):
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
                # Echo for ping/pong
                await websocket.send(json.dumps({"pong": message}))
        finally:
            self.clients.remove(websocket)
            print(f"✗ Unity client disconnected")
    
    async def broadcast_loop(self):
        """Send mock data kontinuerligt med stop/start cycles"""
        print("✓ Broadcasting mock cycling data (20s ride / 5s stop)...")
        print()
        
        last_log_time = 0
        
        while self.running:
            if self.clients:
                message = self.mock_data.get_current_data(use_binary=self.use_binary)
                
                # Broadcast til alle clients
                if self.use_binary:
                    # Binary broadcast
                    for client in self.clients.copy():
                        try:
                            await client.send(message)
                        except:
                            self.clients.discard(client)
                    
                    # Parse for logging
                    parsed = struct.unpack('dfffi', message)
                    timestamp, power, cadence, speed, hr = parsed
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
        
        # If spawn events are requested, start the background task
        try:
            if self.spawn_interval:
                self._spawn_task = asyncio.create_task(self._spawn_loop())

            async with websockets.serve(self.register_client, "localhost", self.port):
                await self.broadcast_loop()
        finally:
            # Cancel spawn task when shutting down
            if self._spawn_task:
                self._spawn_task.cancel()
                try:
                    await self._spawn_task
                except asyncio.CancelledError:
                    pass


    async def _spawn_loop(self):
        """Periodically broadcast JSON spawn events to all connected clients."""
        counter = 0
        while self.running:
            await asyncio.sleep(self.spawn_interval)
            counter += 1
            event = {
                "event": "spawn",
                "entity": "car",
                "id": f"car_{counter}",
                "timestamp": time.time()
            }
            try:
                if self.clients:
                    websockets.broadcast(self.clients, json.dumps(event))
            except Exception:
                # ignore broadcast errors in spawn loop
                pass


async def main():
    bridge = MockWahooBridge(port=8765, use_binary=True)
    
    try:
        await bridge.start_server()
    except KeyboardInterrupt:
        print()
        print("Shutting down...")
        bridge.running = False


if __name__ == "__main__":
    print()
    print("🚴 Mock Wahoo Bridge - ZERO DETECTION TEST")
    print()
    print("Brug dette til at teste Unity integration uden hardware!")
    print("Simulerer stop/start for at teste zero detection.")
    print()
    
    asyncio.run(main())
