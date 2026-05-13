"""
border.p2p — Peer-to-peer networking for the Border protocol.

Modules
-------
peer        Peer data model (host/port, state, latency)
discovery   Peer discovery: bootstrap seeds, ping, peer exchange, persistence
gossip      Push-based gossip router for blocks and transactions
sync        Chain sync: binary-search fork-finding + batch block download
node        P2PNode: high-level object bundling all of the above
server      Flask Blueprint exposing /p2p/* HTTP routes

Quick start
-----------
    from border.blockchain.chain import BorderChain
    from border.p2p.node import P2PNode
    from border.p2p.server import create_p2p_blueprint
    from flask import Flask

    chain = BorderChain(persist_path="data/chain.json")
    p2p   = P2PNode(chain, self_port=9000, seeds=["seed.border.network:9000"])
    p2p.start()

    app = Flask(__name__)
    app.register_blueprint(create_p2p_blueprint(p2p))
    app.run(port=9000)
"""

from .peer import Peer, PeerState
from .discovery import PeerDiscovery
from .gossip import GossipRouter
from .sync import ChainSync
from .node import P2PNode
from .server import create_p2p_blueprint

__all__ = [
    "Peer", "PeerState",
    "PeerDiscovery",
    "GossipRouter",
    "ChainSync",
    "P2PNode",
    "create_p2p_blueprint",
]
