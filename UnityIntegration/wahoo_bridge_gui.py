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
from collections import deque
import threading
import json
import math

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
        # Increased default window size so graphs and axis labels are visible
        self.root.geometry("700x520")
        self.root.resizable(False, False)
        
        # State
        self.connected = False
        self.heart_rate = 0
        # Start time for elapsed display (00:00:00 when GUI starts)
        self.start_time = time.time()
        # Follow (live) vs pan mode. When True the graph auto-follows live data.
        self.follow = True
        # Pan offset in seconds (negative = view earlier than live)
        self.pan_offset = 0.0
        # Heart rate history for graph: store (timestamp, hr)
        self.hr_history = deque(maxlen=2000)
        # Event markers from Unity (scenario starts/ends, spawns, etc.)
        # Each marker: dict with keys: ts (timestamp), label (str), color (str)
        self.markers = []
        self.graph_seconds = 30.0  # show last 30 seconds on X axis
        # Graph margins (so axis labels and markers are not clipped)
        self.graph_left_margin = 48
        self.graph_right_margin = 80
        self.graph_top_margin = 18
        self.graph_bottom_margin = 18
        
        # Create UI
        self.create_widgets()
        
        # Start WebSocket listener in background
        self.ws_thread = threading.Thread(target=self.run_websocket, daemon=True)
        self.ws_thread.start()
        # Demo (internal mock) state
        self.demo_running = False
        self.demo_thread = None
        self.demo_event = None
        # Start periodic tick to keep time labels updating even without incoming frames
        self.root.after(250, self._tick)

        # Drawing throttling and downsampling
        self.min_draw_interval = 1.0 / 25.0  # seconds (25 FPS)
        self._last_draw_time = 0.0
        self._draw_pending = False
        self.max_plot_points = 600
        
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
        
        # Heart rate display and graph
        hr_frame = tk.Frame(self.root)
        hr_frame.pack(pady=6, fill='x')

        tk.Label(
            hr_frame,
            text="Heart Rate:",
            font=("Arial", 11),
            width=12,
            anchor='w'
        ).pack(side=tk.LEFT, padx=10)

        self.hr_value = tk.Label(
            hr_frame,
            text="0",
            font=("Arial", 18, "bold"),
            fg="red",
            width=6,
            anchor='e'
        )
        self.hr_value.pack(side=tk.LEFT)

        # Graph canvas (larger so labels and margins fit comfortably)
        self.graph_width = 600
        self.graph_height = 220
        self.graph_canvas = tk.Canvas(self.root, width=self.graph_width, height=self.graph_height, bg="#111111")
        self.graph_canvas.pack(pady=8)
        # Draw static axis labels (positions will remain within margins)
        self.graph_canvas.create_text(6, self.graph_top_margin, text="BPM", fill="white", anchor='nw', font=("Arial", 10))
        self.graph_canvas.create_text(self.graph_width - self.graph_right_margin + 6, self.graph_height - self.graph_bottom_margin + 2, text="time (s)", fill="white", anchor='nw', font=("Arial", 10))
        
        # Instructions
        ttk.Separator(self.root, orient='horizontal').pack(fill='x', pady=10)
        
        instructions = tk.Label(
            self.root,
            text="Make sure the bridge is running!\n(START_WAHOO_BRIDGE)",
            font=("Arial", 9),
            fg="gray"
        )
        instructions.pack(pady=5)
        # Demo button to feed internal mock data when bridge is not available
        controls = tk.Frame(self.root)
        controls.pack(pady=4)

        self.demo_button = tk.Button(controls, text="Demo", command=self.toggle_demo)
        self.demo_button.pack(side=tk.LEFT, padx=6)

        # Follow/Lock button: when enabled, graph auto-follows live data
        self.follow_button = tk.Button(controls, text="Following", command=self.toggle_follow)
        self.follow_button.pack(side=tk.LEFT, padx=6)

        # Bind mouse interactions for panning on the graph canvas
        self.graph_canvas.bind("<ButtonPress-1>", self._on_graph_press)
        self.graph_canvas.bind("<B1-Motion>", self._on_graph_drag)
        self.graph_canvas.bind("<ButtonRelease-1>", self._on_graph_release)
        self.graph_canvas.bind("<Double-Button-1>", self._on_graph_doubleclick)
        
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
        # We only care about heart rate for this GUI
        self.heart_rate = hr
        now = time.time()
        # Append to history
        try:
            self.hr_history.append((now, int(hr)))
        except Exception:
            # fallback if hr is not int-convertible
            self.hr_history.append((now, 0))

        # Update numeric label
        self.hr_value.config(text=str(int(hr)))

        # Redraw graph in main thread (draw_graph will throttle itself)
        try:
            self.root.after(0, self.draw_graph)
        except Exception:
            pass

    def draw_graph(self):
        """Draw heart rate time-series on the canvas (Y=BPM, X=time)."""
        # Throttle drawing to avoid excessive CPU/GPU use
        nowt = time.time()
        if nowt - self._last_draw_time < self.min_draw_interval:
            # mark pending draw and return; periodic _tick will call draw_graph later
            self._draw_pending = True
            return
        self._draw_pending = False
        self._last_draw_time = nowt
        canvas = self.graph_canvas
        canvas.delete("graph")

        if not self.hr_history:
            return

        now = time.time()

        # Compute visible window start depending on follow/pan state
        live_start = now - self.graph_seconds
        if self.follow:
            # live follow: zero offset
            start = live_start
        else:
            # panned view: apply pan_offset but clamp so start >= earliest history
            start = live_start + self.pan_offset
            if self.hr_history:
                earliest_ts = self.hr_history[0][0]
                min_start = earliest_ts
                if start < min_start:
                    start = min_start
                    self.pan_offset = start - live_start

        # Determine visible points
        visible = [(t, v) for (t, v) in self.hr_history if t >= start and t <= start + self.graph_seconds]
        if not visible:
            return

        # Determine Y range from data, with safe padding
        hrs = [v for (_, v) in visible]
        min_hr = max(30, min(hrs) - 10)
        max_hr = min(220, max(hrs) + 10)
        if max_hr == min_hr:
            max_hr = min_hr + 1

        gw = self.graph_width
        gh = self.graph_height

        # Inner drawing area respects margins so axis labels are not clipped
        lm = self.graph_left_margin
        rm = self.graph_right_margin
        tm = self.graph_top_margin
        bm = self.graph_bottom_margin

        inner_w = gw - lm - rm
        inner_h = gh - tm - bm

        # Draw horizontal grid lines for readability (every 20 BPM)
        step = 20
        first = (min_hr // step) * step
        for val in range(first, int(max_hr) + step, step):
            if val < min_hr or val > max_hr:
                continue
            y = tm + inner_h - int((val - min_hr) / (max_hr - min_hr) * inner_h)
            canvas.create_line(lm, y, lm + inner_w, y, fill="#222222", tag="graph")
            canvas.create_text(8, y-8, text=str(val), fill="#666666", anchor='nw', font=("Arial", 8), tag="graph")

        # Draw vertical ticks at 1s intervals; label every N seconds to avoid crowding
        # Use elapsed seconds since GUI start as the label anchor so labels count up by 1s
        major_every = 2  # show a label every 2 seconds
        elapsed_start = int(math.floor(start - self.start_time))
        elapsed_end = int(math.ceil(start - self.start_time + self.graph_seconds))
        for s in range(elapsed_start, elapsed_end + 1):
            t = self.start_time + float(s)
            if t < start:
                continue
            if t > start + self.graph_seconds:
                break
            x = lm + int((t - start) / self.graph_seconds * inner_w)

            # Minor tick every 1s (short line)
            minor_y0 = tm + inner_h
            minor_y1 = minor_y0 + 4
            canvas.create_line(x, minor_y0, x, minor_y1, fill="#222222", tag="graph")

            # Label every major_every seconds as compact seconds (e.g., '12s')
            if (s % major_every) == 0:
                try:
                    elapsed = int(round(max(0.0, s)))
                    label = f"{elapsed}s"
                except Exception:
                    label = f"{int(round(t - self.start_time))}s"

                # Use a compact font to reduce overlap
                canvas.create_text(x + 6, tm + inner_h + 8, text=label, fill="#ffffff", anchor='n', font=("Arial", 8), tag="graph")

        

        # Build polyline points inside inner area, downsampling if too many points
        pts = []
        visible_len = len(visible)
        if visible_len > self.max_plot_points:
            step = int(math.ceil(visible_len / float(self.max_plot_points)))
        else:
            step = 1

        for i in range(0, visible_len, step):
            (t, v) = visible[i]
            x = lm + int((t - start) / self.graph_seconds * inner_w)
            y = tm + inner_h - int((v - min_hr) / (max_hr - min_hr) * inner_h)
            pts.append((x, y))

        # Draw polyline
        if len(pts) >= 2:
            flat = []
            for (x, y) in pts:
                flat.extend([x, y])
            canvas.create_line(*flat, fill="lime", width=2, tag="graph", smooth=True)

        # Draw current HR marker (if available)
        if pts:
            cur_hr = visible[-1][1]
            cur_x = pts[-1][0]
            cur_y = pts[-1][1]
            canvas.create_oval(cur_x-4, cur_y-4, cur_x+4, cur_y+4, fill="red", outline="pink", tag="graph")
            canvas.create_text(gw - rm + 6, tm, text=f"{cur_hr} BPM", fill="white", font=("Arial", 10, "bold"), tag="graph")
        
        
    def run_websocket(self):
        """Run WebSocket client in background thread"""
        asyncio.run(self.websocket_client())

    def toggle_demo(self):
        """Toggle internal demo/mock mode on/off."""
        if self.demo_running:
            self.stop_demo()
        else:
            self.start_demo()

    def start_demo(self):
        if self.demo_running:
            return
        self.demo_running = True
        self.demo_event = threading.Event()
        self.demo_event.set()
        self.demo_button.config(text="Stop Demo")
        # Mark UI as connected for visual feedback
        self.root.after(0, self.update_status, True)
        self.demo_thread = threading.Thread(target=self._demo_loop, daemon=True)
        self.demo_thread.start()

    def stop_demo(self):
        if not self.demo_running:
            return
        self.demo_running = False
        if self.demo_event:
            self.demo_event.clear()
        self.demo_button.config(text="Demo")
        self.root.after(0, self.update_status, False)

    def _demo_loop(self):
        """Background loop generating mock cycling/HR frames at ~20Hz."""
        ride_duration = 20.0
        stop_duration = 5.0
        period = ride_duration + stop_duration
        while self.demo_event and self.demo_event.is_set():
            now = time.time()
            phase = (now % period)
            if phase < ride_duration:
                t = phase / ride_duration
                hr = int(130 + 18 * math.sin(2 * math.pi * t))
                power = int(150 + 18 * math.sin(2 * math.pi * t + 0.3))
                cadence = float(80 + 8 * math.sin(2 * math.pi * t + 0.6))
                speed = float(26.0 + 2.0 * math.sin(2 * math.pi * t + 0.1))
            else:
                hr = 60
                power = 0
                cadence = 0.0
                speed = 0.0

            try:
                self.root.after(0, self.update_data, power, cadence, speed, hr)
            except Exception:
                pass

            time.sleep(0.05)
        
    def toggle_follow(self):
        """Toggle follow (live) mode on/off. When enabled, the graph auto-follows live data."""
        self.follow = not self.follow
        if self.follow:
            self.pan_offset = 0.0
            self.follow_button.config(text="Following")
        else:
            self.follow_button.config(text="Locked")
        # Redraw immediately to reflect state change
        self.root.after(0, self.draw_graph)

    def _on_graph_press(self, event):
        """Begin panning: record start X and current pan offset. Enter pan (unlock) mode."""
        try:
            self._pan_start_x = event.x
            self._pan_start_offset = self.pan_offset
        except Exception:
            self._pan_start_x = event.x
            self._pan_start_offset = 0.0
        # disable follow when user starts interacting
        self.follow = False
        self.follow_button.config(text="Locked")

    def _on_graph_drag(self, event):
        """Handle mouse drag to pan the time window."""
        lm = self.graph_left_margin
        inner_w = self.graph_width - lm - self.graph_right_margin
        if inner_w <= 0:
            return
        dx = event.x - getattr(self, '_pan_start_x', event.x)
        delta_seconds = dx / float(inner_w) * self.graph_seconds
        new_offset = getattr(self, '_pan_start_offset', 0.0) + delta_seconds
        # clamp offset: cannot go into future (offset > 0)
        if new_offset > 0.0:
            new_offset = 0.0
        # cannot pan earlier than earliest available history
        now = time.time()
        live_start = now - self.graph_seconds
        if self.hr_history:
            earliest = self.hr_history[0][0]
            min_offset = earliest - live_start
            if new_offset < min_offset:
                new_offset = min_offset
        self.pan_offset = new_offset
        self.root.after(0, self.draw_graph)

    def _on_graph_release(self, event):
        """End pan drag. Nothing special to do currently."""
        pass

    def _on_graph_doubleclick(self, event):
        """Double-click resets to live follow mode."""
        self.follow = True
        self.pan_offset = 0.0
        self.follow_button.config(text="Following")
        self.root.after(0, self.draw_graph)

    def _tick(self):
        """Periodic tick to keep the axis time labels updating even if no new data arrives."""
        try:
            self.root.after(250, self._tick)
            # Always redraw periodically so elapsed labels advance
            self.draw_graph()
        except Exception:
            pass

    def handle_event(self, data: dict):
        """Handle an event message from Unity and add a marker to the graph.

        Expected event format (examples):
          {"event":"scenario","action":"start","id":1,"name":"S1","timestamp": 167"}
          {"event":"spawn","entity":"car","id":"car_1","timestamp": 168}
        """
        try:
            ev = data.get('event')
            ts = data.get('timestamp', time.time())
            label = None
            color = '#ff66aa'
            if ev == 'scenario':
                action = data.get('action', '')
                sid = data.get('id')
                name = data.get('name') or f"Scenario {sid}"
                if action == 'start':
                    label = f"{name} start"
                    color = '#66ccff'
                elif action == 'end':
                    label = f"{name} end"
                    color = '#6666ff'
            elif ev == 'spawn':
                ent = data.get('entity', 'obj')
                eid = data.get('id', '')
                label = f"spawn:{ent}{(':'+str(eid)) if eid else ''}"
                color = '#ffcc66'
            else:
                # Generic event label
                label = ev or data.get('type') or 'event'

            if label:
                # Store marker (keep list bounded)
                self.markers.append({'ts': float(ts), 'label': str(label), 'color': color})
                if len(self.markers) > 500:
                    # drop oldest
                    self.markers.pop(0)
                # redraw to show marker
                try:
                    self.root.after(0, self.draw_graph)
                except Exception:
                    pass
        except Exception:
            pass
        
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
                                # Handle events from Unity (scenario start/end, spawn, etc.)
                                if "event" in data:
                                    # schedule marker handling on main thread
                                    try:
                                        self.root.after(0, self.handle_event, data)
                                    except Exception:
                                        pass
                                    continue
                                    
                                # Update UI
                                # Update UI (only heart rate is used)
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
                                    # Only forward HR value; keep other params for compatibility
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
