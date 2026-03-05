import asyncio
import json

import pytest
import websockets

from UnityIntegration.mock_wahoo_bridge import MockWahooBridge


@pytest.mark.asyncio
async def test_mock_server_emits_frames(tmp_path):
    # Start mock server on an ephemeral port
    port = 8766
    bridge = MockWahooBridge(port=port, use_binary=True, spawn_interval=2.0)

    server_task = asyncio.create_task(bridge.start_server())
    await asyncio.sleep(0.2)  # let server start

    uri = f"ws://localhost:{port}"
    recv_count = 0
    try:
        async with websockets.connect(uri) as ws:
            # Expect handshake
            msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
            assert isinstance(msg, str)
            # Then receive a couple of binary frames
            # We may receive JSON events interleaved; accept binary frames and count them
            while recv_count < 3:
                msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
                if isinstance(msg, (bytes, bytearray)):
                    recv_count += 1
                else:
                    # JSON event or handshake; ignore for counting
                    try:
                        _ = json.loads(msg)
                    except Exception:
                        pass
    finally:
        bridge.running = False
        server_task.cancel()
