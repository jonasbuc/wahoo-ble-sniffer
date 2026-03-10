#!/usr/bin/env python3
"""
Wahoo Bridge GUI - Simple status monitor with tray icon
Shows connection status and live cycling data
"""

import asyncio
import json
import threading
import time
import tkinter as tk
from collections import deque
from tkinter import ttk

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
        self.heart_rate = 0
        # Heart rate history for graph: store (timestamp, hr)
        self.hr_history = deque(maxlen=2000)
        # Triggers (vertical strokes) received as JSON events: store (timestamp, name)
        self.triggers = deque(maxlen=500)
        self.graph_seconds = 30.0  # show last 30 seconds on X axis
        # GUI start time (t=0). X axis will be seconds since this moment.
        self.start_time = time.time()
        # Panning state (seconds offset applied to the base visible window).
        # pan_offset is added to the base start_rel (negative values show older data)
        self.pan_offset = 0.0
        self._pan_start_x = None
        self._pan_start_offset = 0.0

        # Bridge protocol reported by server (handshake)
        self.bridge_protocol: str | None = None

        # Create UI
        self.create_widgets()

        # Start WebSocket listener in background
        self.ws_thread = threading.Thread(target=self.run_websocket, daemon=True)
        self.ws_thread.start()

    def create_widgets(self):
        # Title
        title = tk.Label(
            self.root, text="Wahoo Bridge Monitor", font=("Arial", 18, "bold"), pady=10
        )
        title.pack()

        # Status indicator
        self.status_frame = tk.Frame(self.root, pady=10)
        self.status_frame.pack()

        self.status_canvas = tk.Canvas(
            self.status_frame, width=30, height=30, highlightthickness=0
        )
        self.status_canvas.pack(side=tk.LEFT, padx=5)

        # Draw initial red circle
        self.status_led = self.status_canvas.create_oval(
            5, 5, 25, 25, fill="red", outline="darkred"
        )

        self.status_label = tk.Label(
            self.status_frame, text="Not Connected", font=("Arial", 12), fg="red"
        )
        self.status_label.pack(side=tk.LEFT)

        # Separator
        ttk.Separator(self.root, orient="horizontal").pack(fill="x", pady=10)

        # Data display
        data_frame = tk.Frame(self.root)
        data_frame.pack(pady=10)

        # Heart rate display and graph
        hr_frame = tk.Frame(self.root)
        hr_frame.pack(pady=6, fill="x")

        tk.Label(
            hr_frame, text="Heart Rate:", font=("Arial", 11), width=12, anchor="w"
        ).pack(side=tk.LEFT, padx=10)

        self.hr_value = tk.Label(
            hr_frame,
            text="0",
            font=("Arial", 18, "bold"),
            fg="red",
            width=6,
            anchor="e",
        )
        self.hr_value.pack(side=tk.LEFT)

        # Graph canvas
        self.graph_width = 360
        self.graph_height = 150
        self.graph_canvas = tk.Canvas(
            self.root, width=self.graph_width, height=self.graph_height, bg="#111111"
        )
        self.graph_canvas.pack(pady=8)
        self.graph_canvas.create_text(
            30, 10, text="BPM", fill="white", anchor="nw", font=("Arial", 9)
        )
        self.graph_canvas.create_text(
            self.graph_width - 40,
            self.graph_height - 15,
            text="time (s)",
            fill="white",
            anchor="nw",
            font=("Arial", 9),
        )

        # Small label showing bridge/protocol and visible window (pan)
        self.bridge_label = tk.Label(self.status_frame, text="Bridge: --", font=("Arial", 10), fg="gray")
        self.bridge_label.pack(side=tk.RIGHT, padx=6)

        self.window_label = tk.Label(self.root, text="", font=("Arial", 9), fg="gray")
        self.window_label.pack()

        # Bind mouse events for panning on the graph canvas
        self.graph_canvas.bind("<ButtonPress-1>", self._on_pan_start)
        self.graph_canvas.bind("<B1-Motion>", self._on_pan_move)
        self.graph_canvas.bind("<ButtonRelease-1>", self._on_pan_end)
        self.graph_canvas.bind("<Double-Button-1>", self._on_double_click)

        # Instructions
        ttk.Separator(self.root, orient="horizontal").pack(fill="x", pady=10)

        instructions = tk.Label(
            self.root,
            text="Make sure the bridge is running!\n(START_WAHOO_BRIDGE)",
            font=("Arial", 9),
            fg="gray",
        )
        instructions.pack(pady=5)

    def create_data_row(self, parent, label, unit, row):
        tk.Label(
            parent,
            text=label,
            font=("Arial", 11),
            width=12,
            anchor="w",
        ).grid(row=row, column=0, sticky="w", padx=10, pady=5)

        tk.Label(
            parent,
            text=unit,
            font=("Arial", 11),
            fg="gray",
        ).grid(row=row, column=2, sticky="w", padx=5)

    def create_value_label(self, parent, initial_value, row):
        label = tk.Label(
            parent, text=initial_value, font=("Arial", 14, "bold"), width=8, anchor="e"
        )
        label.grid(row=row, column=1, sticky="e", padx=5)
        return label

    def update_status(self, connected: bool):
        self.connected = connected
        if connected:
            self.status_canvas.itemconfig(
                self.status_led, fill="green", outline="darkgreen"
            )
            self.status_label.config(text="Connected", fg="green")
        else:
            self.status_canvas.itemconfig(
                self.status_led, fill="red", outline="darkred"
            )
            self.status_label.config(text="Not Connected", fg="red")

    def update_bridge_status(self, connected: bool, protocol: str | None = None):
        """Update bridge/server handshake status shown in the status bar."""
        if connected:
            text = f"Bridge: {protocol}" if protocol else "Bridge: connected"
            self.bridge_label.config(text=text, fg="green")
        else:
            self.bridge_label.config(text="Bridge: --", fg="gray")

    def _add_trigger(self, name: str, timestamp: float | None = None):
        """Add a trigger event to be shown as a vertical stroke on the graph.

        name: event name (e.g. 'spawn', 'hall_hit')
        timestamp: absolute epoch seconds. If None, uses current time.
        """
        if timestamp is None:
            timestamp = time.time()
        try:
            self.triggers.append((float(timestamp), str(name)))
        except Exception:
            pass

    def update_data(self, power, cadence, speed, hr):
        self.heart_rate = hr
        now = time.time()
        try:
            self.hr_history.append((now, int(hr)))
        except Exception:
            self.hr_history.append((now, 0))

        self.hr_value.config(text=str(int(hr)))

        self.root.after(0, self.draw_graph)

    # --- Panning handlers -------------------------------------------------
    def _on_pan_start(self, event):
        self._pan_start_x = event.x
        self._pan_start_offset = self.pan_offset

    def _on_pan_move(self, event):
        if self._pan_start_x is None:
            return
        dx = event.x - self._pan_start_x
        # dragging right should move to newer data, so invert sign
        delta_seconds = dx / float(self.graph_width) * self.graph_seconds
        self.pan_offset = self._pan_start_offset - delta_seconds
        # clamp pan_offset to available history
        if self.hr_history:
            latest_rel = self.hr_history[-1][0] - self.start_time
            max_pan_allowed = max(0.0, latest_rel - self.graph_seconds)
            if self.pan_offset < -max_pan_allowed:
                self.pan_offset = -max_pan_allowed
            if self.pan_offset > 0.0:
                self.pan_offset = 0.0
        self.root.after(0, self.draw_graph)

    def _on_pan_end(self, event):
        self._pan_start_x = None

    def _on_double_click(self, event):
        # reset pan to follow-live
        self.pan_offset = 0.0
        self.root.after(0, self.draw_graph)

    def draw_graph(self):
        canvas = self.graph_canvas
        canvas.delete("graph")

        if not self.hr_history:
            return

        now = time.time()
        # seconds since GUI start
        elapsed = now - self.start_time
        # base visible window in seconds since start_time (follow-live)
        base_start_rel = max(0.0, elapsed - self.graph_seconds)

        # apply pan offset (negative => older data)
        start_rel = base_start_rel + self.pan_offset

        # clamp start_rel within available history
        if self.hr_history:
            latest_rel = self.hr_history[-1][0] - self.start_time
            max_start = max(0.0, latest_rel - self.graph_seconds)
            if start_rel < 0.0:
                start_rel = 0.0
            if start_rel > max_start:
                start_rel = max_start

        # Collect visible points as (t_rel, hr)
        visible = [
            ((t - self.start_time), v) for (t, v) in self.hr_history
            if (t - self.start_time) >= start_rel
            and (t - self.start_time) <= (start_rel + self.graph_seconds)
        ]
        if not visible:
            return

        hrs = [v for (_, v) in visible]
        min_hr = max(30, min(hrs) - 10)
        max_hr = min(220, max(hrs) + 10)
        if max_hr == min_hr:
            max_hr = min_hr + 1

        gw = self.graph_width
        gh = self.graph_height

        step = 20
        first = (min_hr // step) * step
        for val in range(first, int(max_hr) + step, step):
            if val < min_hr or val > max_hr:
                continue
            y = gh - int((val - min_hr) / (max_hr - min_hr) * gh)
            canvas.create_line(0, y, gw, y, fill="#222222", tag="graph")
            canvas.create_text(
                2,
                y - 10,
                text=str(val),
                fill="#666666",
                anchor="nw",
                font=("Arial", 8),
                tag="graph",
            )

        # Draw X-axis ticks and labels every 2 seconds (seconds since GUI start)
        tick_interval = 2.0
        tick_count = int(self.graph_seconds // tick_interval)
        for i in range(tick_count + 1):
            t_tick = start_rel + i * tick_interval
            x_tick = int((t_tick - start_rel) / self.graph_seconds * gw)
            # small tick line
            canvas.create_line(x_tick, gh - 8, x_tick, gh, fill="#444444", tag="graph")
            # label seconds since GUI start
            # Show absolute seconds since GUI start for the tick (e.g. "12s").
            label = f"{int(round(t_tick))}s"
            canvas.create_text(
                x_tick,
                gh - 6,
                text=label,
                fill="#888888",
                anchor="n",
                font=("Arial", 8),
                tag="graph",
            )

        # Draw trigger vertical lines (from self.triggers)
        # Each trigger stored as (absolute_timestamp, name)
        try:
            for (ts, name) in list(self.triggers):
                t_rel = ts - self.start_time
                if t_rel < start_rel or t_rel > (start_rel + self.graph_seconds):
                    continue
                x_tr = int((t_rel - start_rel) / self.graph_seconds * gw)
                # vertical stroke and small label
                canvas.create_line(x_tr, 0, x_tr, gh, fill="#ff8800", width=2, tag="graph")
                canvas.create_text(
                    x_tr + 3,
                    2,
                    text=str(name),
                    fill="#ffcc88",
                    anchor="nw",
                    font=("Arial", 8),
                    tag="graph",
                )
        except Exception:
            pass

        pts = []
        for t_rel, v in visible:
            x = int((t_rel - start_rel) / self.graph_seconds * gw)
            y = gh - int((v - min_hr) / (max_hr - min_hr) * gh)
            pts.append((x, y))

        if len(pts) >= 2:
            flat = []
            for x, y in pts:
                flat.extend([x, y])
            canvas.create_line(*flat, fill="lime", width=2, tag="graph", smooth=True)
        if pts:
            cur_hr = visible[-1][1]
            cur_x = pts[-1][0]
            cur_y = pts[-1][1]
            canvas.create_oval(
                cur_x - 4,
                cur_y - 4,
                cur_x + 4,
                cur_y + 4,
                fill="red",
                outline="pink",
                tag="graph",
            )
            canvas.create_text(
                self.graph_width - 80,
                12,
                text=f"{cur_hr} BPM",
                fill="white",
                font=("Arial", 10, "bold"),
                tag="graph",
            )

        # Update small window label to indicate what seconds range is currently shown
        try:
            start_i = int(round(start_rel))
            end_i = int(round(start_rel + self.graph_seconds))
            self.window_label.config(text=f"Viewing: {start_i}s → {end_i}s")
        except Exception:
            self.window_label.config(text="")

    def run_websocket(self):
        asyncio.run(self.websocket_client())

    async def websocket_client(self):
        uri = "ws://localhost:8765"

        while True:
            try:
                async with websockets.connect(uri) as websocket:
                    self.root.after(0, self.update_status, True)

                    async for message in websocket:
                        try:
                            if isinstance(message, str):
                                data = json.loads(message)
                                # Server handshake announcing protocol/version
                                if "protocol" in data:
                                    self.bridge_protocol = data.get("protocol")
                                    # Update bridge status label
                                    self.root.after(0, self.update_bridge_status, True, self.bridge_protocol)
                                    continue

                                # If this JSON contains an event, record it as a trigger (vertical stroke)
                                if isinstance(data, dict) and data.get("event"):
                                    # Only add triggers originating from external sources
                                    # (for example UDP from Unity). Mock spawn events will
                                    # include "source":"mock" and are ignored here so
                                    # the GUI only shows Unity-driven triggers.
                                    src = data.get("source")
                                    if src in ("udp", "unity"):
                                        evt_name = data.get("event")
                                        # prefer supplied timestamp, fall back to now
                                        ts = data.get("timestamp") or data.get("time") or time.time()
                                        # schedule adding trigger on main thread
                                        self.root.after(0, self._add_trigger, evt_name, float(ts))

                                # Otherwise treat as a data JSON (spawn/events with power/hr fields)
                                self.root.after(
                                    0,
                                    self.update_data,
                                    data.get("power", 0),
                                    data.get("cadence", 0.0),
                                    data.get("speed", 0.0),
                                    data.get("heart_rate", 0),
                                )
                            else:
                                import struct

                                if len(message) >= 24:
                                    timestamp, power, cadence, speed, hr = (
                                        struct.unpack("dfffi", message[:24])
                                    )
                                    self.root.after(
                                        0,
                                        self.update_data,
                                        power,
                                        cadence,
                                        speed,
                                        hr,
                                    )
                        except Exception:
                            # parsing errors are non-fatal; ignore and continue
                            pass

            except Exception:
                self.root.after(0, self.update_status, False)
                await asyncio.sleep(3)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = WahooBridgeGUI()
    app.run()
