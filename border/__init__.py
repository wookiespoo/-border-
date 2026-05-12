"""
Project Phantom v0.1
Free internet for everyone. Invisible, unblockable, unstoppable.

Three layers:
  1. Relay Network    — paid relay nodes forward traffic (BorderCoin)
  2. Obfuscation      — traffic disguised as normal HTTPS
  3. LoRa Last Mile   — radio broadcast for the truly disconnected

Quick start — run a relay node:
    from border import serve_relay
    serve_relay(node_id="my-relay", region="EU", port=8080)

Quick start — connect as a client:
    from border import BorderClient
    import asyncio

    async def main():
        client = BorderClient("my-device", relay_url="http://localhost:8080")
        result = await client.get("https://example.com")
        print(result["body"])

    asyncio.run(main())
"""

from .client import BorderClient, quick_fetch
from .discovery import BorderDiscovery, RelayNode
from .ledger import BandwidthLedger, BandwidthReceipt
from .lora import LoRaBroadcaster, LoRaContent, LoRaReceiver, PRIORITY_EMERGENCY, PRIORITY_NEWS
from .node import BorderRelayNode, serve_relay
from .obfuscate import BorderObfuscator, BorderSession

__version__ = "0.1.0"
__all__ = [
    "BorderClient",
    "BorderRelayNode",
    "BorderObfuscator",
    "BorderSession",
    "BorderDiscovery",
    "RelayNode",
    "BandwidthLedger",
    "BandwidthReceipt",
    "LoRaBroadcaster",
    "LoRaReceiver",
    "LoRaContent",
    "PRIORITY_EMERGENCY",
    "PRIORITY_NEWS",
    "serve_relay",
    "quick_fetch",
]
