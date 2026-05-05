"""
Simulate a realistic cycling session by streaming telemetry batches
over WebSocket to the analytics ingest server.  Run while the analytics
server is up to watch the dashboard populate live.

The simulation encodes a single scenario:
  - 0–15 %  ramp-up phase (speed 2 → 8 m/s)
  - 15–70 % cruise phase (speed ≈ 8 m/s with natural variation)
  - 40–45 % sharp turn zone (elevated steering angle)
  - 63–72 % braking event (``"red_light"`` trigger + progressive brake input)
  - 70–85 % slow-down phase
  - 85–100 % low-speed trailing phase

Heart rate rises proportionally with speed throughout and smoothly tracks a
moving target to mimic physiological response latency.

Usage:  python simulate_ride.py [--duration 60] [--hz 20]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
import time

import websockets


async def simulate(duration_sec: float = 45.0, hz: float = 20.0) -> None:
    uri = "ws://127.0.0.1:8766"
    session_id = f"sim_{int(time.time())}"
    interval = 1.0 / hz
    batch_size = 10  # send 10 records per WS message

    print(f">  Simulating session '{session_id}' for {duration_sec}s at {hz} Hz")
    print(f"   Server: {uri}")
    print()

    try:
        ws_conn = websockets.connect(uri)
    except Exception as exc:
        print(f"\n[ERROR] Could not prepare connection to {uri}")
        print(f"        {type(exc).__name__}: {exc}")
        return

    try:
        async with ws_conn as ws:
            t = 0.0
            seq = 0
            base_hr = 72.0
            records_sent = 0

            while t < duration_sec:
                batch_records = []
                for _ in range(batch_size):
                    if t >= duration_sec:
                        break

                    # ── Simulate realistic cycling data ──────────────────
                    phase = t / duration_sec  # 0→1

                    # Speed: ramp up, cruise, slow down
                    if phase < 0.15:
                        speed = 2.0 + (phase / 0.15) * 6.0        # 2 → 8
                    elif phase < 0.7:
                        speed = 8.0 + math.sin(t * 0.5) * 1.5     # cruise ~8 ± 1.5
                    elif phase < 0.85:
                        speed = 8.0 - ((phase - 0.7) / 0.15) * 5  # slow down
                    else:
                        speed = 3.0 + random.gauss(0, 0.3)          # slow

                    # Steering: gentle weaving + occasional sharp turns
                    steering = math.sin(t * 1.2) * 4.0 + random.gauss(0, 1.5)
                    if 0.4 < phase < 0.45:  # sharp turn zone
                        steering += 18.0 * math.sin((phase - 0.4) / 0.05 * math.pi)

                    # Heart rate: rises with effort
                    hr_target = base_hr + 15.0 * phase + 10.0 * (speed / 10.0)
                    base_hr += (hr_target - base_hr) * 0.02  # smoothed
                    heart_rate = base_hr + random.gauss(0, 0.8)

                    # Brakes: trigger a braking event around 65-70% of ride
                    brake_front = 0
                    brake_rear = 0
                    trigger_id = ""
                    if 0.63 < phase < 0.65:
                        trigger_id = "red_light"
                    if 0.65 < phase < 0.72:
                        brake_front = int(min(255, 50 + (phase - 0.65) / 0.07 * 200))
                        brake_rear = int(brake_front * 0.6)

                    # Head rotation: scanning behaviour
                    head_yaw = math.sin(t * 0.8) * 0.15 + random.gauss(0, 0.03)
                    head_pitch = math.sin(t * 0.3) * 0.05

                    rec = {
                        "session_id": session_id,
                        "unix_ms": int(time.time() * 1000),
                        "unity_time": round(t, 4),
                        "scenario_id": "city_intersection",
                        "trigger_id": trigger_id,
                        "speed": round(max(0, speed), 3),
                        "steering_angle": round(steering, 3),
                        "brake_front": brake_front,
                        "brake_rear": brake_rear,
                        "heart_rate": round(max(50, heart_rate), 2),
                        "head_pos_x": round(math.sin(t * 0.1) * 0.3, 4),
                        "head_pos_y": 1.70,
                        "head_pos_z": round(t * speed * 0.01, 4),
                        "head_rot_x": round(head_pitch, 5),
                        "head_rot_y": round(head_yaw, 5),
                        "head_rot_z": 0.0,
                        "head_rot_w": round(math.sqrt(max(0, 1.0 - head_yaw**2 - head_pitch**2)), 5),
                        "record_type": "gameplay",
                    }
                    batch_records.append(rec)
                    t += interval
                    seq += 1

                if batch_records:
                    batch = {
                        "records": batch_records,
                        "count": len(batch_records),
                        "sent_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    }
                    await ws.send(json.dumps(batch))
                    records_sent += len(batch_records)

                    # Read feedback
                    try:
                        resp = await asyncio.wait_for(ws.recv(), timeout=0.1)
                        fb = json.loads(resp)
                        # Feedback from the server: live stress/risk scores echoed back by ws_ingest
                        stress = fb.get("stress_score", 0)
                        risk = fb.get("risk_score", 0)
                        spd = batch_records[-1]["speed"]
                        hr = batch_records[-1]["heart_rate"]
                        br = batch_records[-1]["brake_front"]
                        pct = int(phase * 100)
                        print(
                            f"  [{pct:3d}%]  t={t:5.1f}s  speed={spd:5.1f}  hr={hr:5.1f}  "
                            f"brake={br:3d}  stress={stress:5.1f}  risk={risk:5.1f}  "
                            f"sent={records_sent}",
                        )
                    except asyncio.TimeoutError:
                        pass

                # Pace the simulation to ~real-time.
                # Factor 0.3 makes it run 3× faster than real-time so a full
                # 45-second ride completes in ≈15 seconds during demos.
                await asyncio.sleep(interval * batch_size * 0.3)  # 0.3 = 3× faster than real-time

    except OSError as exc:
        print(f"\n[ERROR] Could not connect to ingest server at {uri}")
        print(f"        Is the analytics server running?  Start it first (run_server.bat).")
        print(f"        {type(exc).__name__}: {exc}")
        return
    except Exception as exc:
        print(f"\n[ERROR] Unexpected error during simulation: {type(exc).__name__}: {exc}")
        return

    print()
    print(f"*  Done! Sent {records_sent} records for session '{session_id}'")
    print(f"    Check the dashboard at http://127.0.0.1:8501")



if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Simulate a cycling session to the analytics ingest server.")
    parser.add_argument("--duration", type=float, default=45.0, metavar="SECONDS",
                        help="Total ride duration in seconds (default: 45)")
    parser.add_argument("--hz", type=float, default=20.0, metavar="HZ",
                        help="Telemetry sample rate in Hz (default: 20)")
    args = parser.parse_args()
    asyncio.run(simulate(duration_sec=args.duration, hz=args.hz))
