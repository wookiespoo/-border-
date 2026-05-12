"""
Project Border — Full Demo
Shows all three layers working together:
  1. Relay node running locally
  2. Client routing traffic through it
  3. LoRa broadcaster queuing content for radio transmission

Run: python demo.py
"""

import asyncio
import sys
import os
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from phantom import (
    BorderClient,
    BorderRelayNode,
    LoRaBroadcaster,
    LoRaReceiver,
    LoRaContent,
    PRIORITY_NEWS,
    PRIORITY_EMERGENCY,
    BandwidthLedger,
)
from border.node import create_app_from_node
import uvicorn


RELAY_PORT = 18766


def start_relay_in_background():
    """Start relay node in a background thread."""
    node = BorderRelayNode(
        node_id="demo-relay-01",
        endpoint=f"http://localhost:{RELAY_PORT}",
        region="DEMO",
    )

    import fastapi
    app = node.create_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=RELAY_PORT, log_level="error")
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run)
    thread.daemon = True
    thread.start()
    time.sleep(1.5)
    return node, server


async def demo_relay_and_client(node):
    """Demo: client routing requests through relay."""
    print("\n" + "="*55)
    print("  LAYER 1 + 2: Relay Network + Obfuscation")
    print("="*55)

    client = BorderClient(
        client_id="demo-client-iran-01",
        relay_url=f"http://localhost:{RELAY_PORT}",
    )

    print(f"\n[Demo] Connecting to relay at localhost:{RELAY_PORT}...")
    connected = await client.connect()
    print(f"[Demo] Connected: {connected}")
    print(f"[Demo] Session: {client.session.session_id}")
    print(f"[Demo] Traffic is encrypted + disguised as normal HTTPS")

    # Fetch a page through the relay
    print(f"\n[Demo] Fetching http://example.com through Border relay...")
    result = await client.get("http://example.com")

    print(f"[Demo] Status: {result.get('status_code')}")
    body = result.get('body', '')
    print(f"[Demo] Response ({len(body)} chars): {body[:150]}...")
    print(f"[Demo] Bytes forwarded: {result.get('bytes', 0)}")
    print(f"[Demo] Duration: {result.get('duration_ms', 0)}ms")
    print(f"\n[Demo] Client stats: {client.stats}")

    # Show bandwidth ledger
    summary = node.ledger.get_summary()
    print(f"\n[Demo] Relay bandwidth summary:")
    print(f"   Total bytes forwarded: {summary.total_bytes}")
    print(f"   Requests served: {summary.total_receipts}")
    print(f"   BorderCoin earned: {summary.border_coin_earned:.6f} PC")


async def demo_lora():
    """Demo: LoRa radio broadcast for the last mile."""
    print("\n" + "="*55)
    print("  LAYER 3: LoRa Last Mile Radio Broadcast")
    print("="*55)

    print("\n[Demo] Bridge node: has internet, near censored border")
    print("[Demo] Receiver: $10 device, 10km inside censored area")
    print("[Demo] Range: up to 15km urban, 50km line-of-sight\n")

    broadcaster = LoRaBroadcaster(
        frequency_mhz=868.0,
        simulation_mode=True,
    )
    receiver = LoRaReceiver(simulation_mode=True)

    # Queue emergency alert
    broadcaster.queue_emergency(
        "Internet shutdown detected. Border bridge active on 868MHz. "
        "Free press continues. Stay safe."
    )

    # Queue news articles
    broadcaster.queue_news(
        title="Protest Update — City Center",
        body="Thousands gathered peacefully in the city center today. "
             "Organizers report strong turnout despite communications blackout. "
             "Medical teams are on standby. Meet at the north gate at 6pm.",
        url="https://example-news.com/protest-update",
    )

    broadcaster.queue_news(
        title="Wikipedia: How to stay safe during a protest",
        body="Key safety tips: Stay with a group. Know your rights. "
             "Keep emergency contacts memorized. Have a meeting point. "
             "Document everything safely. Know the nearest medical center.",
    )

    print(f"[Demo] Queue size: {broadcaster.queue_size} items\n")

    # Simulate broadcast + receive
    for i in range(broadcaster.queue_size + 1):
        content = await broadcaster.broadcast_next()
        if not content:
            break

        # Simulate radio transmission → reception
        packets = __import__('phantom.lora', fromlist=['LoRaChunker']).LoRaChunker.chunk(content, i)
        print(f"[Demo] Transmitted {len(packets)} LoRa packets")

        # Simulate receiver picking up packets
        received = None
        for packet in packets:
            received = receiver.receive_packet(packet.to_bytes())

        if received:
            print(f"[Demo] ✓ Receiver got: '{received.title}'")
            print(f"[Demo]   Content: {received.body[:100]}...")
        print()

    print(f"[Demo] Broadcaster stats: {broadcaster.stats}")
    print(f"[Demo] Receiver has {len(receiver.received)} items")


async def main():
    print("""
╔═══════════════════════════════════════════════════════════╗
║              PROJECT PHANTOM v0.1 — DEMO                  ║
║                                                           ║
║   Free internet for everyone.                             ║
║   Invisible. Unblockable. Unstoppable.                    ║
║                                                           ║
║   Layer 1: Relay Network (pay nodes with BorderCoin)     ║
║   Layer 2: Obfuscation (looks like normal HTTPS)          ║
║   Layer 3: LoRa Radio (last mile, $10 receiver)           ║
╚═══════════════════════════════════════════════════════════╝
    """)

    # Start relay
    print("[Demo] Starting relay node...")
    node, server = start_relay_in_background()
    print(f"[Demo] Relay running on port {RELAY_PORT}")

    # Run demos
    await demo_relay_and_client(node)
    await demo_lora()

    print("\n" + "="*55)
    print("  PROJECT PHANTOM")
    print("="*55)
    print()
    print("  What you just saw:")
    print("  ✓ Encrypted tunnel through relay node")
    print("  ✓ Traffic disguised as normal HTTPS")
    print("  ✓ Bandwidth proof generated for BorderCoin")
    print("  ✓ LoRa radio protocol for $10 receivers")
    print()
    print("  Next steps:")
    print("  → Buy LoRa hardware: ESP32 + SX1276 (~$10)")
    print("  → Deploy relay nodes near censored borders")
    print("  → Distribute receivers inside censored regions")
    print("  → BorderCoin pays relay operators automatically")
    print()
    print("  The internet that can't be turned off.")
    print()

    server.should_exit = True


if __name__ == "__main__":
    asyncio.run(main())
