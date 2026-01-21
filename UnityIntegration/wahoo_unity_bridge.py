#!/usr/bin/env python3
"""
Wahoo BLE to Unity Bridge
Streams live KICKR SNAP data to Unity via WebSocket
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
        """JSON format for debugging"""
        return json.dumps(asdict(self))
    
    def to_binary(self) -> bytes:
        """
        Binary format for low-latency transmission
        Format: 'dfffi' = double(8) + float(4) + float(4) + float(4) + int(4) = 24 bytes
        Much faster than JSON parsing!
        """
        return struct.pack('dfffi', 
            self.timestamp,
            float(self.power),
            self.cadence,
            self.speed,
            self.heart_rate
        )


class UnityBridge:
    """Bridges BLE data to Unity via WebSocket"""
    
    def __init__(self, port: int = 8765, use_binary: bool = True):
        self.port = port
        self.clients: Set[WebSocketServerProtocol] = set()
        self.current_data = CyclingData(timestamp=time.time())
        self.running = False
        self.use_binary = use_binary  # Binary mode for low latency
        
    async def register_client(self, websocket: WebSocketServerProtocol):
        """Register a new Unity client"""
        # Enable TCP_NODELAY for low latency
        try:
            websocket.transport.get_extra_info('socket').setsockopt(
                __import__('socket').IPPROTO_TCP,
                __import__('socket').TCP_NODELAY,
                1
            )
        except:
            pass  # Not all transports support this
        
        self.clients.add(websocket)
        logging.info(f"Unity client connected from {websocket.remote_address}")
        
        # Send initial handshake with protocol info
        handshake = json.dumps({
            "protocol": "binary" if self.use_binary else "json",
            "version": "1.0",
            "format": "dfffi (timestamp, power, cadence, speed, hr)"
        })
        await websocket.send(handshake)
        
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
            # Use binary format for speed, JSON only for debugging
            if self.use_binary:
                message = data.to_binary()
            else:
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
            result = {}
            flags = int.from_bytes(data[0:2], byteorder='little')
            offset = 2
            
            # Instantaneous Power (sint16, always present)
            power = int.from_bytes(data[offset:offset+2], byteorder='little', signed=True)
            result["power"] = power
            offset += 2
            
            # Pedal Power Balance (optional, bit 0)
            if flags & 0x0001:
                offset += 1
            
            # Accumulated Torque (optional, bit 2)
            if flags & 0x0004:
                offset += 2
            
            # Wheel Revolution Data (bit 4) - for speed calculation
            if flags & 0x0010 and len(data) >= offset + 6:
                cumulative_wheel_revs = int.from_bytes(data[offset:offset+4], byteorder='little')
                last_wheel_event_time = int.from_bytes(data[offset+4:offset+6], byteorder='little')
                result["wheel_revs"] = cumulative_wheel_revs
                result["wheel_time"] = last_wheel_event_time
                offset += 6
            
            # Crank Revolution Data (bit 5) - for cadence calculation
            if flags & 0x0020 and len(data) >= offset + 4:
                cumulative_crank_revs = int.from_bytes(data[offset:offset+2], byteorder='little')
                last_crank_event_time = int.from_bytes(data[offset+2:offset+4], byteorder='little')
                result["crank_revs"] = cumulative_crank_revs
                result["crank_time"] = last_crank_event_time
                offset += 4
            
            return result
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
        
        # For cadence calculation
        self.last_crank_revs: Optional[int] = None
        self.last_crank_time: Optional[int] = None
        
        # For speed calculation  
        self.last_wheel_revs: Optional[int] = None
        self.last_wheel_time: Optional[int] = None
        self.wheel_circumference_m = 2.105  # ~700x25c road bike tire
        
        # ZERO DETECTION: Track when we last got data
        self.last_update_time = time.time()
        self.zero_timeout = 1.2  # Send zeros if no update for 1.2 seconds
        self.zero_check_task = None
    
    def calculate_cadence(self, crank_revs: int, crank_time: int) -> Optional[float]:
        """Calculate cadence from crank revolution data"""
        if self.last_crank_revs is None or self.last_crank_time is None:
            self.last_crank_revs = crank_revs
            self.last_crank_time = crank_time
            return None
        
        # Handle rollover (uint16)
        rev_diff = (crank_revs - self.last_crank_revs) & 0xFFFF
        time_diff = (crank_time - self.last_crank_time) & 0xFFFF
        
        if time_diff == 0 or rev_diff == 0:
            return None
        
        # Time is in 1/1024 seconds
        cadence = (rev_diff * 1024 * 60) / time_diff
        
        self.last_crank_revs = crank_revs
        self.last_crank_time = crank_time
        
        return cadence if 0 < cadence < 255 else None
    
    def calculate_speed(self, wheel_revs: int, wheel_time: int) -> Optional[float]:
        """Calculate speed from wheel revolution data"""
        if self.last_wheel_revs is None or self.last_wheel_time is None:
            self.last_wheel_revs = wheel_revs
            self.last_wheel_time = wheel_time
            return None
        
        # Handle rollover (uint32 for revs, uint16 for time)
        rev_diff = (wheel_revs - self.last_wheel_revs) & 0xFFFFFFFF
        time_diff = (wheel_time - self.last_wheel_time) & 0xFFFF
        
        if time_diff == 0 or rev_diff == 0:
            return None
        
        # Time is in 1/1024 seconds
        # Speed in m/s = (revs * circumference) / (time / 1024)
        speed_ms = (rev_diff * self.wheel_circumference_m * 1024) / time_diff
        speed_kmh = speed_ms * 3.6
        
        self.last_wheel_revs = wheel_revs
        self.last_wheel_time = wheel_time
        
        return speed_kmh if 0 < speed_kmh < 100 else None
    
    async def zero_detection_loop(self):
        """Monitor for inactivity and send zero values when stopped"""
        while self.running:
            await asyncio.sleep(0.5)  # Check twice per second
            
            time_since_update = time.time() - self.last_update_time
            
            # If no updates for too long, cyclist has stopped
            if time_since_update > self.zero_timeout:
                current = self.bridge.current_data
                
                # Send zeros if we're not already at zero
                if current.power > 0 or current.cadence > 0 or current.speed > 0:
                    zero_data = CyclingData(
                        timestamp=time.time(),
                        power=0,
                        cadence=0.0,
                        speed=0.0,
                        heart_rate=current.heart_rate  # Keep HR
                    )
                    await self.bridge.broadcast_data(zero_data)
                    logging.info("⚠ No activity detected - sending zeros")
    
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
                
                # Start zero detection background task
                if self.zero_check_task is None or self.zero_check_task.done():
                    self.zero_check_task = asyncio.create_task(self.zero_detection_loop())
                    logging.info("✓ Zero detection enabled")
                
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
                            # HR doesn't reset activity timer (only cycling data does)
                            asyncio.create_task(self.bridge.broadcast_data(updated))
                            logging.info(f"HR: {parsed['bpm']} bpm")
                else:
                    # Cycling Power device (KICKR)
                    service_uuid = CYCLING_POWER_SERVICE
                    char_uuid = CYCLING_POWER_MEASUREMENT
                    
                    def callback(sender, data):
                        parsed = CyclingPowerParser.parse(data)
                        
                        # Get current values
                        current = self.bridge.current_data
                        power = parsed.get("power", current.power)
                        cadence = current.cadence
                        speed = current.speed
                        
                        # Calculate cadence if crank data present
                        if "crank_revs" in parsed and "crank_time" in parsed:
                            calc_cadence = self.calculate_cadence(
                                parsed["crank_revs"], 
                                parsed["crank_time"]
                            )
                            if calc_cadence is not None:
                                cadence = calc_cadence
                        elif power > 0 and speed > 0:
                            # Estimate cadence from power and speed if no crank data
                            # Typical relationship: cadence ≈ (power / 2.5) + (speed * 2)
                            # This is a rough approximation
                            cadence = min(max((power / 2.5) + (speed * 2), 0), 180)
                        
                        # Calculate speed if wheel data present
                        if "wheel_revs" in parsed and "wheel_time" in parsed:
                            calc_speed = self.calculate_speed(
                                parsed["wheel_revs"],
                                parsed["wheel_time"]
                            )
                            if calc_speed is not None:
                                speed = calc_speed
                        
                        # Update bridge data
                        updated = CyclingData(
                            timestamp=time.time(),
                            power=power,
                            cadence=cadence,
                            speed=speed,
                            heart_rate=current.heart_rate
                        )
                        
                        # Update activity timestamp for zero detection
                        self.last_update_time = time.time()
                        
                        asyncio.create_task(self.bridge.broadcast_data(updated))
                        
                        # Reduced logging for performance (only log every 10th update)
                        if not hasattr(self, '_log_counter'):
                            self._log_counter = 0
                        self._log_counter += 1
                        
                        if self._log_counter % 10 == 0:
                            log_parts = [f"Power: {power} W"]
                            if cadence > 0:
                                log_parts.append(f"Cadence: {cadence:.1f} rpm")
                            if speed > 0:
                                log_parts.append(f"Speed: {speed:.1f} km/h")
                            logging.info(" | ".join(log_parts))
                
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
