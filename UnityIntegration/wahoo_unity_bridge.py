#!/usr/bin/env python3
"""
Wahoo BLE to Unity Bridge
Streams live KICKR SNAP data to Unity via WebSocket
"""

import asyncio
import json
import logging
import time
from typing import Optional, Dict, Any, Set
from dataclasses import dataclass, asdict

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
import websockets
from websockets.server import WebSocketServerProtocol


# GATT UUIDs
CYCLING_POWER_SERVICE = "00001818-0000-1000-8000-00805f9b34fb"
CYCLING_POWER_MEASUREMENT = "00002a63-0000-1000-8000-00805f9b34fb"

HEART_RATE_SERVICE = "0000180d-0000-1000-8000-00805f9b34fb"
HEART_RATE_MEASUREMENT = "00002a37-0000-1000-8000-00805f9b34fb"


@dataclass
class CyclingData:
    """Real-time cycling data for Unity"""
    timestamp: float
    power: int = 0  # Watts
    cadence: float = 0.0  # RPM
    speed: float = 0.0  # km/h
    heart_rate: int = 0  # BPM
    
    def to_json(self) -> str:
        return json.dumps(asdict(self))


class UnityBridge:
    """Bridges BLE data to Unity via WebSocket"""
    
    def __init__(self, port: int = 8765):
        self.port = port
        self.clients: Set[WebSocketServerProtocol] = set()
        self.current_data = CyclingData(timestamp=time.time())
        self.running = False
        
    async def register_client(self, websocket: WebSocketServerProtocol):
        """Register a new Unity client"""
        self.clients.add(websocket)
        logging.info(f"Unity client connected from {websocket.remote_address}")
        
        # Send initial data
        await websocket.send(self.current_data.to_json())
        
        try:
            # Keep connection alive
            async for message in websocket:
                # Echo back for ping/pong
                await websocket.send(json.dumps({"pong": message}))
        finally:
            self.clients.remove(websocket)
            logging.info(f"Unity client disconnected")
    
    async def broadcast_data(self, data: CyclingData):
        """Broadcast data to all connected Unity clients"""
        self.current_data = data
        
        if self.clients:
            message = data.to_json()
            # Send to all connected clients
            websockets.broadcast(self.clients, message)
            logging.debug(f"Broadcast: {message}")
    
    async def start_server(self):
        """Start WebSocket server for Unity"""
        self.running = True
        async with websockets.serve(self.register_client, "localhost", self.port):
            logging.info(f"WebSocket server started on ws://localhost:{self.port}")
            logging.info("Waiting for Unity to connect...")
            await asyncio.Future()  # Run forever


class CyclingPowerParser:
    """Parses Cycling Power Measurement data"""
    
    @staticmethod
    def parse(data: bytearray) -> Dict[str, Any]:
        if len(data) < 4:
            return {}
        
        try:
            flags = int.from_bytes(data[0:2], byteorder='little')
            offset = 2
            
            # Instantaneous Power (sint16, always present)
            power = int.from_bytes(data[offset:offset+2], byteorder='little', signed=True)
            
            return {"power": power}
        except Exception as e:
            logging.warning(f"Cycling Power parse error: {e}")
            return {}


class HeartRateParser:
    """Parses Heart Rate Measurement data"""
    
    @staticmethod
    def parse(data: bytearray) -> Dict[str, Any]:
        if len(data) < 2:
            return {}
        
        flags = data[0]
        hr_format = flags & 0x01
        
        if hr_format == 0:
            bpm = data[1]
        else:
            bpm = int.from_bytes(data[1:3], byteorder='little')
        
        return {"bpm": bpm}


class WahooDeviceHandler:
    """Handles a single Wahoo BLE device"""
    
    def __init__(self, device: BLEDevice, bridge: UnityBridge, is_hr: bool = False):
        self.device = device
        self.bridge = bridge
        self.is_hr = is_hr
        self.client: Optional[BleakClient] = None
        self.running = False
    
    async def connect_and_stream(self):
        """Connect and stream data to Unity"""
        self.running = True
        
        while self.running:
            try:
                logging.info(f"Connecting to {self.device.name}...")
                self.client = BleakClient(self.device)
                await self.client.connect()
                
                if not self.client.is_connected:
                    logging.error(f"Failed to connect to {self.device.name}")
                    await asyncio.sleep(5)
                    continue
                
                logging.info(f"✓ Connected to {self.device.name}")
                
                if self.is_hr:
                    # Heart Rate device
                    service_uuid = HEART_RATE_SERVICE
                    char_uuid = HEART_RATE_MEASUREMENT
                    
                    def callback(sender, data):
                        parsed = HeartRateParser.parse(data)
                        if "bpm" in parsed:
                            # Update bridge data
                            current = self.bridge.current_data
                            updated = CyclingData(
                                timestamp=time.time(),
                                power=current.power,
                                cadence=current.cadence,
                                speed=current.speed,
                                heart_rate=parsed["bpm"]
                            )
                            asyncio.create_task(self.bridge.broadcast_data(updated))
                            logging.info(f"HR: {parsed['bpm']} bpm")
                else:
                    # Cycling Power device (KICKR)
                    service_uuid = CYCLING_POWER_SERVICE
                    char_uuid = CYCLING_POWER_MEASUREMENT
                    
                    def callback(sender, data):
                        parsed = CyclingPowerParser.parse(data)
                        if "power" in parsed:
                            # Update bridge data
                            current = self.bridge.current_data
                            updated = CyclingData(
                                timestamp=time.time(),
                                power=parsed["power"],
                                cadence=current.cadence,
                                speed=current.speed,
                                heart_rate=current.heart_rate
                            )
                            asyncio.create_task(self.bridge.broadcast_data(updated))
                            logging.info(f"Power: {parsed['power']} W")
                
                # Get service and characteristic
                service = None
                for svc in self.client.services:
                    if svc.uuid.lower() == service_uuid.lower():
                        service = svc
                        break
                
                if not service:
                    logging.error(f"Service {service_uuid} not found on {self.device.name}")
                    await asyncio.sleep(5)
                    continue
                
                characteristic = None
                for char in service.characteristics:
                    if char.uuid.lower() == char_uuid.lower():
                        characteristic = char
                        break
                
                if not characteristic:
                    logging.error(f"Characteristic {char_uuid} not found")
                    await asyncio.sleep(5)
                    continue
                
                # Subscribe to notifications
                await self.client.start_notify(characteristic, callback)
                logging.info(f"✓ Subscribed to {self.device.name} notifications")
                
                # Keep connection alive
                while self.running and self.client.is_connected:
                    await asyncio.sleep(1)
                
                await self.client.stop_notify(characteristic)
                await self.client.disconnect()
                
                if self.running:
                    logging.warning(f"{self.device.name} disconnected. Reconnecting in 5s...")
                    await asyncio.sleep(5)
            
            except Exception as e:
                logging.error(f"Error with {self.device.name}: {e}")
                if self.running:
                    await asyncio.sleep(5)
    
    async def stop(self):
        """Stop the device handler"""
        self.running = False
        if self.client and self.client.is_connected:
            await self.client.disconnect()


async def scan_for_device(name_contains: str) -> Optional[BLEDevice]:
    """Scan for a BLE device"""
    logging.info(f"Scanning for {name_contains}...")
    
    devices = await BleakScanner.discover(timeout=10)
    
    for device in devices:
        if device.name and name_contains.upper() in device.name.upper():
            logging.info(f"Found {device.name} at {device.address}")
            return device
    
    logging.warning(f"No device found containing '{name_contains}'")
    return None


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S"
    )
    
    print("=" * 60)
    print("  Wahoo BLE to Unity Bridge")
    print("=" * 60)
    print()
    
    # Create Unity bridge
    bridge = UnityBridge(port=8765)
    
    # Find devices
    kickr = await scan_for_device("KICKR")
    tickr = await scan_for_device("TICKR")
    
    if not kickr:
        logging.error("KICKR not found! Make sure it's on and pedaling.")
        return
    
    print()
    print("✓ Devices ready!")
    print(f"✓ WebSocket server: ws://localhost:8765")
    print()
    print("Next steps:")
    print("1. Start Unity")
    print("2. Attach the WahooDataReceiver script to a GameObject")
    print("3. Press Play in Unity")
    print()
    print("Press Ctrl+C to stop")
    print("=" * 60)
    print()
    
    # Start tasks
    tasks = [bridge.start_server()]
    
    handlers = []
    
    if kickr:
        kickr_handler = WahooDeviceHandler(kickr, bridge, is_hr=False)
        handlers.append(kickr_handler)
        tasks.append(kickr_handler.connect_and_stream())
    
    if tickr:
        tickr_handler = WahooDeviceHandler(tickr, bridge, is_hr=True)
        handlers.append(tickr_handler)
        tasks.append(tickr_handler.connect_and_stream())
    
    try:
        await asyncio.gather(*tasks)
    except KeyboardInterrupt:
        logging.info("Shutting down...")
        for handler in handlers:
            await handler.stop()


if __name__ == "__main__":
    asyncio.run(main())
