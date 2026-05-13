"""
Border P2P - P2PNode

Bundles discovery + gossip + sync into one object.
The unified border-node runner (Task #59) will instantiate this.

Typical usage:
    p2p = P2PNode(
            chain=my_chain,
            self_host="0.0.0.0",
            self_port=9000,
            seeds=["seed.border.network:9000"],
            data_dir="/var/border",
    )
    p2p.start()

    # Broadcast a freshly mined block to the network:
    p2p.broadcast_block(block)

    # Broadcast a new transaction:
    p2p.broadcast_tx(tx)
"""

from __future__ import annotations

import hashlib
import logging
import threading
from typing import List, Optional, TYPE_CHECKING

from .discovery import PeerDiscovery
from .gossip import GossipRouter
from .peer import Peer
from .sync import ChainSync

if TYPE_CHECKING:
    from ..blockchain.block import Block
    from ..blockchain.chain import BorderChain
    from ..blockchain.transaction import Transaction

logger = logging.getLogger("border.p2p.node")


class P2PNode:
    """
    High-level P2P node that ties discovery, gossip, and sync together.
    """

    def __init__(
        self,
        chain: "BorderChain",
        self_host: str = "0.0.0.0",
        self_port: int = 9000,
        seeds: Optional[List[str]] = None,
        data_dir: Optional[str] = None,
        node_id: str = "",
    ):
        self.chain = chain
        self.self_host = self_host
        self.self_port = self_port
        self.node_id = node_id or self._derive_node_id()

        self.discovery = PeerDiscovery(
            self_host=self_host,
            self_port=self_port,
            node_id=self.node_id,
            seeds=seeds or [],
            data_dir=data_dir,
        )

        self.gossip = GossipRouter(
            self_host=self_host,
            self_port=self_port,
            get_peers=self.discovery.get_peers,
        )

        self.sync = ChainSync(
            chain=chain,
            get_peers=self.discovery.get_peers,
        )

        # Wire gossip handlers
        self.gossip.on("block", self._handle_block_gossip)
        self.gossip.on("transaction", self._handle_tx_gossip)

    # -------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------

    def start(self) -> None:
        self.discovery.start()
        self.sync.start()
        logger.info(f"[P2PNode] Running node_id={self.node_id[:12]}... "
                    f"on {self.self_host}:{self.self_port}")

    def stop(self) -> None:
        self.discovery.stop()
        self.sync.stop()

    # -------------------------------------------------------------------
    # Outbound broadcast helpers
    # -------------------------------------------------------------------

    def broadcast_block(self, block: "Block") -> None:
        """Call after successfully mining or receiving a valid block."""
        self.gossip.broadcast("block", block.to_dict())

    def broadcast_tx(self, tx: "Transaction") -> None:
        """Call after adding a transaction to the local mempool."""
        self.gossip.broadcast("transaction", tx.to_dict())

    def get_peers(self) -> List[Peer]:
        return self.discovery.get_peers()

    # -------------------------------------------------------------------
    # Inbound gossip handlers
    # -------------------------------------------------------------------

    def _handle_block_gossip(self, payload: dict) -> None:
        from ..blockchain.block import Block
        try:
            block = Block.from_dict(payload)
            our_height = self.chain.height
            if block.index <= our_height:
                return  # already have it
            if block.index == our_height + 1:
                ok, reason = self.chain.add_block(block)
                if ok:
                    logger.info(f"[P2PNode] Gossip block #{block.index} accepted")
                else:
                    logger.debug(f"[P2PNode] Gossip block #{block.index} rejected: {reason}")
                    self.sync.trigger()   # might need to sync first
            else:
                # Gap — trigger full sync
                logger.info(f"[P2PNode] Gap detected (our={our_height} gossip={block.index}), syncing")
                self.sync.trigger()
        except Exception as e:
            logger.warning(f"[P2PNode] Block gossip parse error: {e}")

    def _handle_tx_gossip(self, payload: dict) -> None:
        from ..blockchain.transaction import Transaction
        try:
            tx = Transaction.from_dict(payload)
            self.chain.add_transaction(tx)
        except Exception as e:
            logger.debug(f"[P2PNode] TX gossip parse error: {e}")

    # -------------------------------------------------------------------
    # Misc
    # -------------------------------------------------------------------

    def _derive_node_id(self) -> str:
        import secrets
        return secrets.token_hex(16)

    def status(self) -> dict:
        peers = self.discovery.get_peers()
        return {
            "node_id": self.node_id,
            "address": f"{self.self_host}:{self.self_port}",
            "chain_height": self.chain.height,
            "peers_known": len(self.discovery.get_peers(reachable_only=False)),
            "peers_reachable": len(peers),
            "peer_list": [p.to_dict() for p in peers[:10]],
        }
