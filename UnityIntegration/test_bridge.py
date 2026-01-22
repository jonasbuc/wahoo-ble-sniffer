#!/usr/bin/env python3
"""
Test WebSocket Bridge Without Unity
Simpel test for at verificere at bridge virker
"""

import asyncio
import websockets

async def test_client():
    uri = "ws://localhost:8765"
    
    print("=" * 60)
    print("  WebSocket Bridge Test")
    print("=" * 60)
    print()
    print(f"Connecting to {uri}...")
    
    try:
        async with websockets.connect(uri) as websocket:
            print("✓ Connected!")
            print()
            print("Receiving data (Ctrl+C to stop):")
            print("-" * 60)
            
            message_count = 0
            async for message in websocket:
                message_count += 1
                print(f"[{message_count}] {message}")
                
                # Send ping every 5 messages
                if message_count % 5 == 0:
                    await websocket.send("ping")
                    
    except ConnectionRefusedError:
        print("✗ Connection refused!")
        print()
        print("Make sure the bridge is running:")
        print("  python wahoo_unity_bridge.py")
        print()
    except KeyboardInterrupt:
        print()
        print("Test stopped by user")
    except Exception as e:
        print(f"✗ Error: {e}")

if __name__ == "__main__":
    print()
    print("Start the bridge first in another terminal:")
    print("  python wahoo_unity_bridge.py")
    print()
    input("Press Enter when bridge is running...")
    print()
    
    asyncio.run(test_client())
