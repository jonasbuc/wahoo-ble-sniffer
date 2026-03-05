#!/usr/bin/env python3
"""Small WebSocket tail client that prints decoded frames from the mock/bridge.

Usage: python UnityIntegration/ws_tail.py
"""
import asyncio
import json
import struct
import time
import argparse

try:
    import websockets
except ImportError:
    print("Please install websockets: pip install websockets")
    raise


async def run(uri: str):
    while True:
        try:
            async with websockets.connect(uri) as ws:
                print(f"Connected to {uri}")
                async for message in ws:
                    if isinstance(message, str):
                        try:
                            data = json.loads(message)
                            print("WS-TAIL JSON:", data)
                        except Exception:
                            print("WS-TAIL TEXT:", message)
                    else:
                        if len(message) >= 24:
                            try:
                                ts, power, cadence, speed, hr = struct.unpack("dfffi", message[:24])
                                tstr = time.strftime("%H:%M:%S", time.localtime(ts))
                                print(f"WS-TAIL BIN ts={ts:.3f} ({tstr}) P={power:.1f} C={cadence:.1f} S={speed:.1f} HR={int(hr)}")
                            except Exception as e:
                                print("WS-TAIL BIN parse error:", e)
                        else:
                            print("WS-TAIL BIN (short):", message)
        except Exception as exc:
            print("WS-TAIL: disconnected, retrying in 2s (", exc, ")")
            await asyncio.sleep(2)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--uri", default="ws://localhost:8765", help="WebSocket URI")
    args = p.parse_args()
    try:
        asyncio.run(run(args.uri))
    except KeyboardInterrupt:
        print("WS-TAIL: stopped by user")
