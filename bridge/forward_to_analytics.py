#!/usr/bin/env python3
"""
forward_to_analytics.py — Bridge ↔ Analytics forwarder
========================================================
Connects to the Wahoo bridge WebSocket (default ws://localhost:8765)
and forwards every HR reading as a proper TelemetryBatch JSON message
to the Analytics ingest WebSocket (default ws://localhost:8766).

This is useful for testing the full pipeline without Unity running.
In production, Unity performs this role — receiving binary HR frames
from the bridge and sending JSON telemetry to Analytics.

Usage::

    python bridge/forward_to_analytics.py [--bridge-url ws://localhost:8765]
                                          [--ingest-url ws://localhost:8766]
                                          [--session-id test_session]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import struct
import time
import uuid

import websockets

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
)
LOG = logging.getLogger("forwarder")


async def forward(
    bridge_url: str,
    ingest_url: str,
    session_id: str,
) -> None:
    """Read binary HR frames from bridge and send JSON batches to ingest."""

    LOG.info("Connecting to bridge at %s ...", bridge_url)
    async with websockets.connect(bridge_url) as bridge_ws:
        LOG.info("✓ Connected to bridge")

        LOG.info("Connecting to analytics ingest at %s ...", ingest_url)
        async with websockets.connect(ingest_url) as ingest_ws:
            LOG.info("✓ Connected to analytics ingest")
            LOG.info("Forwarding HR data as session '%s' ...", session_id)

            count = 0

            # Drain feedback from ingest in background (it sends LiveFeedback JSON back)
            async def _drain_ingest():
                try:
                    async for _msg in ingest_ws:
                        pass  # discard feedback
                except Exception:
                    pass

            asyncio.create_task(_drain_ingest())

            async for message in bridge_ws:
                # Bridge sends 12-byte binary: struct.pack("di", timestamp, hr)
                if isinstance(message, bytes) and len(message) == 12:
                    ts, hr = struct.unpack("di", message)
                elif isinstance(message, str):
                    # Could be JSON event — just log and skip
                    LOG.debug("JSON from bridge: %s", message[:120])
                    continue
                else:
                    LOG.debug("Unknown frame (%d bytes)", len(message))
                    continue

                count += 1
                unix_ms = int(ts * 1000)

                record = {
                    "session_id": session_id,
                    "unix_ms": unix_ms,
                    "unity_time": ts - 1.7e9,  # approximate
                    "scenario_id": "live_test",
                    "heart_rate": float(hr),
                    "speed": 0.0,
                    "record_type": "gameplay",
                }

                batch = {
                    "records": [record],
                    "count": 1,
                    "sent_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                }

                try:
                    await ingest_ws.send(json.dumps(batch))
                except Exception as exc:
                    LOG.error("Failed to send to ingest: %s", exc)
                    return

                if count % 10 == 1:
                    LOG.info("❤️  HR=%d bpm  (forwarded %d records)", hr, count)


def main() -> None:
    parser = argparse.ArgumentParser(description="Bridge → Analytics forwarder")
    parser.add_argument("--bridge-url", default="ws://localhost:8765",
                        help="Wahoo bridge WebSocket URL")
    parser.add_argument("--ingest-url", default="ws://localhost:8766",
                        help="Analytics ingest WebSocket URL")
    parser.add_argument("--session-id", default=None,
                        help="Session ID (auto-generated if omitted)")
    args = parser.parse_args()

    session_id = args.session_id or f"live_{uuid.uuid4().hex[:10]}"

    try:
        asyncio.run(forward(args.bridge_url, args.ingest_url, session_id))
    except KeyboardInterrupt:
        LOG.info("Forwarder stopped.")
    except Exception as exc:
        LOG.error("Forwarder error: %s — retrying in 3s", exc)
        import time as _t
        _t.sleep(3)
        try:
            asyncio.run(forward(args.bridge_url, args.ingest_url, session_id))
        except KeyboardInterrupt:
            LOG.info("Forwarder stopped.")


if __name__ == "__main__":
    main()
