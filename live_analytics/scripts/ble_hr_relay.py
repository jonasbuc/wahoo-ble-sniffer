"""
BLE HR Relay
============
Bridges the Wahoo BLE bridge (port 8765, binary frames) into the analytics
ingest WebSocket (port 8766, TelemetryBatch JSON).

This lets you see real heart-rate data in the dashboard WITHOUT Unity running.

Usage:
    python -m live_analytics.scripts.ble_hr_relay [--bridge ws://127.0.0.1:8765]
                                                   [--ingest ws://127.0.0.1:8766]

The BLE bridge must be started separately in live mode:
    python -m bridge.bike_bridge --live
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import logging
import struct
import time

import httpx
import websockets

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
)
log = logging.getLogger("ble_hr_relay")

FRAME_SIZE = 12  # struct.pack("di", timestamp, hr)  — 8 + 4 bytes


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Relay BLE HR bridge → analytics ingest")
    p.add_argument("--bridge", default="ws://127.0.0.1:8765", help="BLE bridge WebSocket URL")
    p.add_argument("--ingest", default="ws://127.0.0.1:8766", help="Analytics ingest WebSocket URL")
    p.add_argument("--api", default="http://127.0.0.1:8080", help="Analytics HTTP API base URL")
    p.add_argument("--session", default=None, help="Session ID (default: unix-ms at start)")
    p.add_argument("--scenario", default="live_hr_test", help="Scenario ID tag")
    return p.parse_args()


def _make_batch(session_id: str, unix_ms: int, hr: float, unity_time: float, scenario: str) -> str:
    record = {
        "session_id": session_id,
        "unix_ms": unix_ms,
        "unity_time": unity_time,
        "scenario_id": scenario,
        "trigger_id": "",
        "speed": 0.0,
        "steering_angle": 0.0,
        "brake_front": 0,
        "brake_rear": 0,
        "heart_rate": float(hr),
        "head_pos_x": 0.0,
        "head_pos_y": 1.7,
        "head_pos_z": 0.0,
        "head_rot_x": 0.0,
        "head_rot_y": 0.0,
        "head_rot_z": 0.0,
        "head_rot_w": 1.0,
        "record_type": "hr_only",
    }
    batch = {
        "records": [record],
        "count": 1,
        "sent_at": datetime.datetime.utcnow().isoformat() + "Z",
    }
    return json.dumps(batch)


_POLL_INTERVAL = 5  # seconds between session-tracker polls


async def _track_latest_session(
    api_base: str,
    current_session: list[str],  # mutable single-element list used as a ref
    fallback_session_id: str,
) -> None:
    """Poll the HTTP API every _POLL_INTERVAL seconds and update current_session[0]
    to whichever session has the most recent start_unix_ms.  This ensures HR data
    is always routed to the session Unity most recently created."""
    async with httpx.AsyncClient(timeout=5) as client:
        while True:
            try:
                resp = await client.get(f"{api_base}/api/sessions")
                resp.raise_for_status()
                sessions = resp.json()
                if sessions:
                    latest = max(sessions, key=lambda s: s.get("start_unix_ms", 0))
                    new_id = latest["session_id"]
                    if new_id != current_session[0]:
                        log.info(
                            "Session switched: %s → %s",
                            current_session[0],
                            new_id,
                        )
                        current_session[0] = new_id
            except Exception as exc:  # noqa: BLE001
                log.debug("Session poll failed (will retry): %s", exc)
            await asyncio.sleep(_POLL_INTERVAL)


async def relay(bridge_url: str, ingest_url: str, session_id: str, scenario: str, api_base: str) -> None:
    start_time = time.time()
    # Mutable ref so the tracker coroutine can update it while relay reads it.
    current_session: list[str] = [session_id]

    log.info("Initial session : %s", session_id)
    log.info("Bridge          : %s", bridge_url)
    log.info("Ingest          : %s", ingest_url)
    log.info("API base        : %s", api_base)
    log.info("Scenario        : %s", scenario)
    log.info("────────────────────────────────────────────────────")
    log.info("HR will follow the most recently created session (polling every %ds)", _POLL_INTERVAL)

    asyncio.get_event_loop().create_task(
        _track_latest_session(api_base, current_session, session_id)
    )

    while True:
        try:
            async with websockets.connect(bridge_url, ping_interval=20) as bridge_ws:
                log.info("Connected to BLE bridge – waiting for HR frames…")
                try:
                    async with websockets.connect(ingest_url, ping_interval=20) as ingest_ws:
                        log.info("Connected to analytics ingest – relaying…")
                        async for raw in bridge_ws:
                            if isinstance(raw, bytes) and len(raw) == FRAME_SIZE:
                                ts_epoch, hr = struct.unpack("di", raw)
                                unix_ms = int(ts_epoch * 1000)
                                unity_time = round(ts_epoch - start_time, 3)
                                sid = current_session[0]
                                batch_json = _make_batch(sid, unix_ms, hr, unity_time, scenario)
                                await ingest_ws.send(batch_json)
                                log.info("HR = %3d bpm  session=%s  (unity_t=%.1f s)", hr, sid, unity_time)
                            else:
                                # JSON event frame from bridge (UDP trigger etc.) – skip
                                pass
                except (websockets.ConnectionClosed, OSError) as exc:
                    log.warning("Lost ingest connection: %s – reconnecting…", exc)
        except (websockets.ConnectionClosed, OSError) as exc:
            log.warning("Lost bridge connection: %s – retrying in 3 s…", exc)
            await asyncio.sleep(3)


def main() -> None:
    args = _parse_args()
    session_id = args.session or str(int(time.time() * 1000))
    asyncio.run(relay(args.bridge, args.ingest, session_id, args.scenario, args.api))


if __name__ == "__main__":
    main()
