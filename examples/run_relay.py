"""
Run a Border Relay Node
Be part of the network. Earn BorderCoin. Give people their internet back.

Usage:
    python run_relay.py
    python run_relay.py --port 9090 --region US
"""

import argparse
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from phantom import serve_relay

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a Border relay node")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--region", default="UNKNOWN", help="Your region (EU, US, APAC, etc.)")
    parser.add_argument("--node-id", default=None, help="Custom node ID (auto-generated if not set)")
    args = parser.parse_args()

    print("""
╔═══════════════════════════════════════════════════════╗
║           PROJECT PHANTOM — Relay Node                ║
║   Free internet for people who don't have it.         ║
╚═══════════════════════════════════════════════════════╝
    """)

    serve_relay(
        node_id=args.node_id,
        port=args.port,
        region=args.region,
    )
