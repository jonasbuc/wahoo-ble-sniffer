#!/usr/bin/env python3
"""
Mock Wahoo Bridge - Til test uden rigtige enheder
Sender simulerede cycling data til Unity
"""

import asyncio
import json
import time
import math
from typing import Set
import websockets
from websockets.server import WebSocketServerProtocol


class MockCyclingData:
    """Simulerer cycling data"""
    
    def __init__(self):
        self.time_offset = time.time()
        self.base_power = 150
        self.base_cadence = 80
        self.base_speed = 25.0
        self.base_hr = 140
    
    def get_current_data(self):
        """Generer realistisk cycling data"""
        elapsed = time.time() - self.time_offset
        
        # Simuler variation med sine waves
        power_variation = math.sin(elapsed * 0.3) * 30
        cadence_variation = math.sin(elapsed * 0.5) * 10
        speed_variation = math.sin(elapsed * 0.3) * 5
        hr_variation = math.sin(elapsed * 0.2) * 10
        
        # Add random micro-variations
        import random
        micro_noise = random.uniform(-5, 5)
        
        return {
            "timestamp": time.time(),
            "power": max(0, int(self.base_power + power_variation + micro_noise)),
            "cadence": max(0, self.base_cadence + cadence_variation),
            "speed": max(0, self.base_speed + speed_variation),
            "heart_rate": max(40, int(self.base_hr + hr_variation))
        }


class MockWahooBridge:
    """WebSocket server der sender mock data"""
    
    def __init__(self, port: int = 8765):
        self.port = port
        self.clients: Set[WebSocketServerProtocol] = set()
        self.mock_data = MockCyclingData()
        self.running = False
    
    async def register_client(self, websocket: WebSocketServerProtocol):
        """Register en Unity client"""
        self.clients.add(websocket)
        print(f"✓ Unity client connected: {websocket.remote_address}")
        
        try:
            async for message in websocket:
                # Echo for ping/pong
                await websocket.send(json.dumps({"pong": message}))
        finally:
            self.clients.remove(websocket)
            print(f"✗ Unity client disconnected")
    
    async def broadcast_loop(self):
        """Send mock data kontinuerligt"""
        print("✓ Broadcasting mock cycling data...")
        print()
        
        while self.running:
            if self.clients:
                data = self.mock_data.get_current_data()
                message = json.dumps(data)
                
                # Broadcast til alle clients
                websockets.broadcast(self.clients, message)
                
                # Log every second
                if int(time.time()) % 1 == 0:
                    print(f"📡 Power: {data['power']}W | "
                          f"Cadence: {data['cadence']:.0f}rpm | "
                          f"Speed: {data['speed']:.1f}km/h | "
                          f"HR: {data['heart_rate']}bpm")
            
            await asyncio.sleep(0.1)  # 10Hz update rate
    
    async def start_server(self):
        """Start WebSocket server"""
        self.running = True
        
        print("=" * 60)
        print("  Mock Wahoo Bridge - Test Server")
        print("=" * 60)
        print()
        print("⚠️  Dette er MOCK DATA - ingen rigtige BLE enheder!")
        print()
        print(f"✓ WebSocket server: ws://localhost:{self.port}")
        print()
        print("Waiting for Unity to connect...")
        print("(Tryk Ctrl+C for at stoppe)")
        print()
        
        async with websockets.serve(self.register_client, "localhost", self.port):
            await self.broadcast_loop()


async def main():
    bridge = MockWahooBridge(port=8765)
    
    try:
        await bridge.start_server()
    except KeyboardInterrupt:
        print()
        print("Shutting down...")
        bridge.running = False


if __name__ == "__main__":
    print()
    print("🚴 Mock Wahoo Bridge")
    print()
    print("Brug dette til at teste Unity integration uden KICKR!")
    print()
    
    asyncio.run(main())
