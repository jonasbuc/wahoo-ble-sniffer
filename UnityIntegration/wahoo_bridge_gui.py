#!/usr/bin/env python3
"""
Wahoo Bridge GUI - Simple status monitor with tray icon
Shows connection status and live cycling data
"""

import asyncio
import time
import tkinter as tk
from tkinter import ttk
from typing import Optional
import threading
import json

try:
    import websockets
except ImportError:
    print("Installing websockets...")
    import subprocess
    subprocess.check_call(["pip", "install", "websockets"])
    import websockets


class WahooBridgeGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Wahoo Bridge Monitor")
        self.root.geometry("400x350")
        self.root.resizable(False, False)
        
        # State
        self.connected = False
        self.power = 0
        self.cadence = 0.0
        self.speed = 0.0
        self.heart_rate = 0
        
        # Create UI
        self.create_widgets()
        
        # Start WebSocket listener in background
        self.ws_thread = threading.Thread(target=self.run_websocket, daemon=True)
        self.ws_thread.start()
        
    def create_widgets(self):
        # Title
        title = tk.Label(
            self.root, 
            text="Wahoo Bridge Monitor",
            font=("Arial", 18, "bold"),
            pady=10
        )
        title.pack()
        
        # Status indicator
        self.status_frame = tk.Frame(self.root, pady=10)
        self.status_frame.pack()
        
        self.status_canvas = tk.Canvas(
            self.status_frame, 
            width=30, 
            height=30, 
            highlightthickness=0
        )
        self.status_canvas.pack(side=tk.LEFT, padx=5)
        
        # Draw initial red circle
        self.status_led = self.status_canvas.create_oval(
            5, 5, 25, 25, 
            fill="red", 
            outline="darkred"
        )
        
        self.status_label = tk.Label(
            self.status_frame,
            text="Not Connected",
            font=("Arial", 12),
            fg="red"
        )
        self.status_label.pack(side=tk.LEFT)
        
        # Separator
        ttk.Separator(self.root, orient='horizontal').pack(fill='x', pady=10)
        
        # Data display
        data_frame = tk.Frame(self.root)
        data_frame.pack(pady=10)
        
        # Power
        self.create_data_row(data_frame, "Power:", "W", 0)
        self.power_value = self.create_value_label(data_frame, "0", 0)
        
        # Cadence
        self.create_data_row(data_frame, "Cadence:", "RPM", 1)
        self.cadence_value = self.create_value_label(data_frame, "0.0", 1)
        
        # Speed
        self.create_data_row(data_frame, "Speed:", "km/h", 2)
        self.speed_value = self.create_value_label(data_frame, "0.0", 2)
        
        # Heart Rate
        self.create_data_row(data_frame, "Heart Rate:", "BPM", 3)
        self.hr_value = self.create_value_label(data_frame, "0", 3)
        
        # Instructions
        ttk.Separator(self.root, orient='horizontal').pack(fill='x', pady=10)
        
        instructions = tk.Label(
            self.root,
            text="Make sure the bridge is running!\n(START_WAHOO_BRIDGE)",
            font=("Arial", 9),
            fg="gray"
        )
        instructions.pack(pady=5)
        
    def create_data_row(self, parent, label, unit, row):
        tk.Label(
            parent, 
            text=label, 
            font=("Arial", 11),
            width=12,
            anchor='w'
        ).grid(row=row, column=0, sticky='w', padx=10, pady=5)
        
        tk.Label(
            parent,
            text=unit,
            font=("Arial", 11),
            fg="gray"
        ).grid(row=row, column=2, sticky='w', padx=5)
        
    def create_value_label(self, parent, initial_value, row):
        label = tk.Label(
            parent,
            text=initial_value,
            font=("Arial", 14, "bold"),
            width=8,
            anchor='e'
        )
        label.grid(row=row, column=1, sticky='e', padx=5)
        return label
        
    def update_status(self, connected: bool):
        self.connected = connected
        if connected:
            self.status_canvas.itemconfig(self.status_led, fill="green", outline="darkgreen")
            self.status_label.config(text="Connected", fg="green")
        else:
            self.status_canvas.itemconfig(self.status_led, fill="red", outline="darkred")
            self.status_label.config(text="Not Connected", fg="red")
            
    def update_data(self, power, cadence, speed, hr):
        self.power = power
        self.cadence = cadence
        self.speed = speed
        self.heart_rate = hr
        
        # Update labels
        self.power_value.config(text=str(int(power)))
        self.cadence_value.config(text=f"{cadence:.1f}")
        self.speed_value.config(text=f"{speed:.1f}")
        self.hr_value.config(text=str(hr))
        
    def run_websocket(self):
        """Run WebSocket client in background thread"""
        asyncio.run(self.websocket_client())
        
    async def websocket_client(self):
        """Connect to bridge and receive data"""
        uri = "ws://localhost:8765"
        
        while True:
            try:
                async with websockets.connect(uri) as websocket:
                    # Connected!
                    self.root.after(0, self.update_status, True)
                    
                    # Receive data
                    async for message in websocket:
                        try:
                            # Try JSON first (handshake)
                            if isinstance(message, str):
                                data = json.loads(message)
                                if "protocol" in data:
                                    continue  # Skip handshake
                                    
                                # Update UI
                                self.root.after(
                                    0,
                                    self.update_data,
                                    data.get("power", 0),
                                    data.get("cadence", 0.0),
                                    data.get("speed", 0.0),
                                    data.get("heart_rate", 0)
                                )
                            else:
                                # Binary data
                                import struct
                                if len(message) >= 24:
                                    timestamp, power, cadence, speed, hr = struct.unpack('dfffi', message[:24])
                                    self.root.after(
                                        0,
                                        self.update_data,
                                        power, cadence, speed, hr
                                    )
                        except Exception as e:
                            print(f"Parse error: {e}")
                            
            except Exception as e:
                # Connection failed
                self.root.after(0, self.update_status, False)
                await asyncio.sleep(3)  # Retry after 3 seconds
                
    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = WahooBridgeGUI()
    app.run()
