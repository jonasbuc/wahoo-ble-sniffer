#!/usr/bin/env python3
"""
wahoo_bridge_gui.py
===================
Tkinter status monitor and live HR graph for the Wahoo Bridge WebSocket server.

Architecture
------------
The GUI runs entirely on the Tkinter main thread.  A single background daemon
thread (``ws_thread``) runs an ``asyncio`` event loop that maintains the
WebSocket connection.  Whenever new data arrives the background thread uses
``root.after(0, callback, ...)`` to safely schedule updates on the main
thread — the only thread allowed to touch Tkinter widgets.

  Main thread                    Background thread (daemon)
  ─────────────────────────────  ──────────────────────────────────────────
  Tkinter event loop             asyncio.run(websocket_client())
  draw_graph()                   websockets.connect() → ws loop
  update_data(hr) / update_status()  root.after(0, ...) → schedule callbacks
  pan / zoom via mouse events

Graph features
--------------
- Rolling 30-second HR line chart drawn on a dark Canvas widget.
- Trigger events (from Unity via UDP relay) are drawn as orange vertical lines.
- Click-and-drag to pan left/right; double-click to snap back to live view.
- X-axis shows seconds-since-GUI-start; Y-axis auto-scales ±10 BPM.

Wire format (from bridge server)
---------------------------------
Binary frames: 12 bytes — ``struct.pack("di", ts, hr)``
JSON frames  : ``{"heart_rate": …}``
Handshake    : ``{"protocol": "binary", "version": "1.0"}`` (first JSON after connect)
Trigger      : ``{"event": "hall_hit", "source": "udp", "timestamp": …}``
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
    """Main application window.

    Attributes
    ----------
    hr_history  : deque of (epoch_seconds, bpm_int) — rolling history for the graph
    triggers    : deque of (epoch_seconds, name_str) — events drawn as vertical markers
    graph_seconds : width of the visible time window in seconds
    start_time  : epoch time when the GUI launched (used as the X=0 origin)
    pan_offset  : signed seconds added to the live window; negative = look at older data
    bridge_protocol : protocol string received during the server handshake
    """

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Wahoo Bridge Monitor")
        self.root.geometry("400x350")
        self.root.resizable(False, False)

        # ── State ─────────────────────────────────────────────────────────────
        self.connected = False
        self.heart_rate = 0
        # Heart rate history for graph: store (epoch_seconds, bpm) pairs.
        # maxlen=2000 keeps ~33 minutes at 1 Hz without unbounded growth.
        self.hr_history = deque(maxlen=2000)
        # Triggers (vertical strokes) received as JSON events: store (epoch_seconds, name).
        self.triggers = deque(maxlen=500)
        self.graph_seconds = 30.0  # width of the visible window on the X axis (seconds)
        # GUI start time used as the X=0 reference point for the graph.
        self.start_time = time.time()
        # Pan state: pan_offset is added to the base visible-window start.
        # Negative values scroll the view into older data; 0 = follow-live.
        self.pan_offset = 0.0
        self._pan_start_x = None       # X pixel position where the drag started
        self._pan_start_offset = 0.0   # pan_offset value at drag start (restored on cancel)

        # Bridge protocol label text — filled when the server handshake arrives.
        self.bridge_protocol: str | None = None

        # ── Build UI & start background thread ────────────────────────────────
        self.create_widgets()

        # Run the asyncio WebSocket client in a background daemon thread so it
        # doesn't block the Tkinter main loop.  Being a daemon thread means it
        # is killed automatically when the main window closes.
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
        """Update the bridge-protocol label in the status bar after a handshake."""
        if connected:
            text = f"Bridge: {protocol}" if protocol else "Bridge: connected"
            self.bridge_label.config(text=text, fg="green")
        else:
            self.bridge_label.config(text="Bridge: --", fg="gray")

    def _add_trigger(self, name: str, timestamp: float | None = None):
        """Record a trigger event to be displayed as an orange vertical line.

        Parameters
        ----------
        name      : short event label shown above the marker (e.g. "spawn")
        timestamp : absolute epoch seconds for the event; defaults to now
        """
        if timestamp is None:
            timestamp = time.time()
        try:
            self.triggers.append((float(timestamp), str(name)))
        except Exception:
            pass

    def update_data(self, hr):
        self.heart_rate = hr
        now = time.time()
        try:
            bpm = int(hr)
        except Exception:
            bpm = 0
        self.hr_history.append((now, bpm))

        self.hr_value.config(text=str(bpm))

        self.root.after(0, self.draw_graph)

    # ── Panning handlers ──────────────────────────────────────────────────────

    def _on_pan_start(self, event):
        """Record the drag start position and current pan offset."""
        self._pan_start_x = event.x
        self._pan_start_offset = self.pan_offset

    def _on_pan_move(self, event):
        """Update pan_offset proportionally to the horizontal drag distance.

        Dragging right moves toward newer (live) data; dragging left shows
        older data.  The offset is clamped so you cannot pan beyond the
        oldest available history point or past live view.
        """
        if self._pan_start_x is None:
            return
        dx = event.x - self._pan_start_x
        # Convert pixel delta to seconds: full graph width = graph_seconds.
        # Negate dx so dragging right (positive dx) reveals newer data.
        delta_seconds = dx / float(self.graph_width) * self.graph_seconds
        self.pan_offset = self._pan_start_offset - delta_seconds
        # Clamp within available history.
        if self.hr_history:
            latest_rel = self.hr_history[-1][0] - self.start_time
            max_pan_allowed = max(0.0, latest_rel - self.graph_seconds)
            if self.pan_offset < -max_pan_allowed:
                self.pan_offset = -max_pan_allowed
            if self.pan_offset > 0.0:
                self.pan_offset = 0.0  # can't pan into the future
        self.root.after(0, self.draw_graph)

    def _on_pan_end(self, event):
        """Clear the drag anchor when the mouse button is released."""
        self._pan_start_x = None

    def _on_double_click(self, event):
        """Snap the view back to live (reset pan_offset to 0)."""
        self.pan_offset = 0.0
        self.root.after(0, self.draw_graph)

    def draw_graph(self):
        """Redraw the heart-rate graph on self.graph_canvas.

        Coordinate mapping
        ------------------
        X axis: time in seconds relative to self.start_time (left = older, right = newer).
          x_pixel = (t_rel - start_rel) / graph_seconds * graph_width

        Y axis: BPM value, auto-scaled to (min_hr−10) … (max_hr+10), clamped to 30–220.
          y_pixel = graph_height − (bpm − min_hr) / (max_hr − min_hr) * graph_height
          (canvas Y=0 is the top, so subtract from graph_height to flip to BPM-up)

        Trigger markers
        ---------------
        Orange vertical lines are drawn at the X position matching each stored
        trigger timestamp.  A small text label is rendered just above the top of
        the line.

        All items are tagged "graph" so canvas.delete("graph") can clear only
        the graph elements (not the static axis labels created in create_widgets).
        """
        canvas = self.graph_canvas
        canvas.delete("graph")  # clear previous frame's graph items only

        if not self.hr_history:
            return

        now = time.time()
        # Seconds elapsed since GUI start — the right edge of the live view.
        elapsed = now - self.start_time
        # Base window: show the most recent graph_seconds of data (follow-live).
        base_start_rel = max(0.0, elapsed - self.graph_seconds)

        # Apply the pan offset (negative values reveal older data).
        start_rel = base_start_rel + self.pan_offset

        # Clamp so start_rel stays within available history.
        if self.hr_history:
            latest_rel = self.hr_history[-1][0] - self.start_time
            max_start = max(0.0, latest_rel - self.graph_seconds)
            if start_rel < 0.0:
                start_rel = 0.0
            if start_rel > max_start:
                start_rel = max_start

        # ── Collect data points that fall inside the visible window ────────
        visible = [
            ((t - self.start_time), v) for (t, v) in self.hr_history
            if (t - self.start_time) >= start_rel
            and (t - self.start_time) <= (start_rel + self.graph_seconds)
        ]
        if not visible:
            return

        hrs = [v for (_, v) in visible]
        # Dynamic Y range with ±10 BPM padding, clamped to physiological limits.
        min_hr = max(30, min(hrs) - 10)
        max_hr = min(220, max(hrs) + 10)
        if max_hr == min_hr:
            max_hr = min_hr + 1  # prevent division by zero

        gw = self.graph_width
        gh = self.graph_height

        # ── Horizontal grid lines (every 20 BPM) ──────────────────────────
        step = 20
        first = (min_hr // step) * step
        for val in range(first, int(max_hr) + step, step):
            if val < min_hr or val > max_hr:
                continue
            # Flip Y: higher BPM = smaller canvas Y coordinate.
            y = gh - int((val - min_hr) / (max_hr - min_hr) * gh)
            canvas.create_line(0, y, gw, y, fill="#222222", tag="graph")
            canvas.create_text(
                2, y - 10, text=str(val),
                fill="#666666", anchor="nw", font=("Arial", 8), tag="graph",
            )

        # ── X-axis ticks (every 2 seconds) ────────────────────────────────
        tick_interval = 2.0
        tick_count = int(self.graph_seconds // tick_interval)
        for i in range(tick_count + 1):
            t_tick = start_rel + i * tick_interval
            # Map tick time to canvas X pixel.
            x_tick = int((t_tick - start_rel) / self.graph_seconds * gw)
            canvas.create_line(x_tick, gh - 8, x_tick, gh, fill="#444444", tag="graph")
            # Label shows absolute seconds-since-start so you can correlate with logs.
            label = f"{int(round(t_tick))}s"
            canvas.create_text(
                x_tick, gh - 6, text=label,
                fill="#888888", anchor="n", font=("Arial", 8), tag="graph",
            )

        # ── Trigger vertical markers ───────────────────────────────────────
        # Triggers are stored as (absolute_epoch_seconds, event_name).
        # Orange line + text label above the line.
        try:
            for (ts, name) in list(self.triggers):
                t_rel = ts - self.start_time
                if t_rel < start_rel or t_rel > (start_rel + self.graph_seconds):
                    continue
                x_tr = int((t_rel - start_rel) / self.graph_seconds * gw)
                canvas.create_line(x_tr, 0, x_tr, gh, fill="#ff8800", width=2, tag="graph")
                canvas.create_text(
                    x_tr + 3, 2, text=str(name),
                    fill="#ffcc88", anchor="nw", font=("Arial", 8), tag="graph",
                )
        except Exception:
            pass

        # ── HR line ───────────────────────────────────────────────────────
        # Convert (t_rel, bpm) pairs to (x_pixel, y_pixel) and draw as a
        # smoothed polyline.
        pts = []
        for t_rel, v in visible:
            x = int((t_rel - start_rel) / self.graph_seconds * gw)
            y = gh - int((v - min_hr) / (max_hr - min_hr) * gh)
            pts.append((x, y))

        if len(pts) >= 2:
            # canvas.create_line expects a flat list [x1, y1, x2, y2, ...]
            flat = [coord for pt in pts for coord in pt]
            canvas.create_line(*flat, fill="lime", width=2, tag="graph", smooth=True)

        # ── Current-value dot + label ──────────────────────────────────────
        if pts:
            cur_hr = visible[-1][1]
            cur_x, cur_y = pts[-1]
            # Red dot at the tip of the line.
            canvas.create_oval(cur_x-4, cur_y-4, cur_x+4, cur_y+4,
                               fill="red", outline="pink", tag="graph")
            # BPM readout in the top-right corner of the graph.
            canvas.create_text(
                self.graph_width - 80, 12,
                text=f"{cur_hr} BPM",
                fill="white", font=("Arial", 10, "bold"), tag="graph",
            )

        # ── Window range label below the graph ────────────────────────────
        try:
            start_i = int(round(start_rel))
            end_i   = int(round(start_rel + self.graph_seconds))
            self.window_label.config(text=f"Viewing: {start_i}s → {end_i}s")
        except Exception:
            self.window_label.config(text="")

    def run_websocket(self):
        """Entry point for the background daemon thread.

        Creates a new asyncio event loop (Tkinter already owns the main loop so
        we can't use the default one) and blocks until the coroutine returns.
        """
        asyncio.run(self.websocket_client())

    async def websocket_client(self):
        """Continuously connect to the bridge server and process incoming frames.

        Reconnects every 3 seconds on any error (bridge not started yet,
        network interruption, etc.).

        Frame handling
        --------------
        str  frames: JSON parsed and dispatched based on keys:
               "protocol"      → server handshake; update bridge label
               "event"         → trigger marker (only if source is "udp"/"unity")
               other           → cycling data {heart_rate}
        bytes frames: 12-byte binary; unpacked as ``struct.unpack("di", …)``
               fields: timestamp(d), hr(i)
        """
        uri = "ws://localhost:8765"

        while True:
            try:
                async with websockets.connect(uri) as websocket:
                    # Notify the main thread that we are connected.
                    self.root.after(0, self.update_status, True)

                    async for message in websocket:
                        try:
                            if isinstance(message, str):
                                data = json.loads(message)
                                # ── Server handshake ──────────────────────
                                if "protocol" in data:
                                    self.bridge_protocol = data.get("protocol")
                                    self.root.after(0, self.update_bridge_status, True, self.bridge_protocol)
                                    continue

                                # ── Trigger event (e.g. from Unity UDP) ───
                                if isinstance(data, dict) and data.get("event"):
                                    # Only display triggers that originate from
                                    # Unity/UDP — mock spawn events (source="mock")
                                    # are filtered out to avoid clutter.
                                    src = data.get("source")
                                    if src in ("udp", "unity"):
                                        evt_name = data.get("event")
                                        # Use supplied timestamp if available so
                                        # the marker aligns with the actual event time.
                                        ts = data.get("timestamp") or data.get("time") or time.time()
                                        self.root.after(0, self._add_trigger, evt_name, float(ts))

                                # ── Cycling data JSON ──────────────────────
                                self.root.after(
                                    0, self.update_data,
                                    data.get("heart_rate", 0),
                                )
                            else:
                                # ── Binary frame (12 bytes) ────────────────
                                # Format: double(8) + int32(4) = 12 bytes
                                import struct
                                if len(message) >= 12:
                                    timestamp, hr = (
                                        struct.unpack("di", message[:12])
                                    )
                                    self.root.after(0, self.update_data, hr)
                        except Exception:
                            # Parsing errors are non-fatal; ignore the frame and continue.
                            pass

            except Exception:
                # Connection lost or refused — signal disconnected and retry.
                self.root.after(0, self.update_status, False)
                await asyncio.sleep(3)

    def run(self):
        """Start the Tkinter main loop (blocks until the window is closed)."""
        self.root.mainloop()


if __name__ == "__main__":
    app = WahooBridgeGUI()
    app.run()
