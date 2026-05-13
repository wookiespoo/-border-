"""
Border P2P - Chain sync.

When we connect to a peer with a longer chain we:
1. Find the common ancestor (fork point) via binary search on block indices.
2. Download missing blocks in order.
3. Hand them to BorderChain.add_block() for validation.

Also exposes the HTTP handler helpers that the P2P server calls.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import List, Optional, TYPE_CHECKING

import requests

from .peer import Peer

if TYPE_CHECKING:
    from ..blockchain.chain import BorderChain

logger = logging.getLogger("border.p2p.sync")

SYNC_INTERVAL = 30       # seconds between proactive sync attempts
REQUEST_TIMEOUT = 10
BATCH_SIZE = 50          # blocks per /p2p/blocks request


class ChainSync:
    """
    Keeps our chain in sync with the best peer.

    Usage:
        sync = ChainSync(chain, get_peers)
        sync.start()           # background thread
        sync.trigger()         # force an immediate sync pass
        sync.stop()
    """

    def __init__(
        self,
        chain: "BorderChain",
        get_peers,              # Callable[[], List[Peer]]
    ):
        self._chain = chain
        self._get_peers = get_peers
        self._running = False
        self._event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._sync_loop, daemon=True, name="p2p-sync"
        )
        self._thread.start()
        logger.info("[Sync] Started chain sync")

    def stop(self) -> None:
        self._running = False
        self._event.set()

    def trigger(self) -> None:
        """Kick the sync loop immediately (e.g. after receiving a new block gossip)."""
        self._event.set()

    # -------------------------------------------------------------------
    # Sync loop
    # -------------------------------------------------------------------

    def _sync_loop(self) -> None:
        while self._running:
            self._event.wait(timeout=SYNC_INTERVAL)
            self._event.clear()
            if not self._running:
                break
            try:
                self._sync_pass()
            except Exception as e:
                logger.warning(f"[Sync] Sync pass error: {e}")

    def _sync_pass(self) -> None:
        peers = self._get_peers()
        if not peers:
            return

        our_height = len(self._chain) - 1
        best_peer = max(peers, key=lambda p: p.chain_height, default=None)
        if not best_peer or best_peer.chain_height <= our_height:
            return

        logger.info(f"[Sync] Behind by {best_peer.chain_height - our_height} "
                    f"blocks. Syncing from {best_peer.addr}")
        self._sync_from(best_peer, our_height)

    def _sync_from(self, peer: Peer, our_height: int) -> None:
        # Find fork point
        fork_height = self._find_fork(peer, our_height)
        if fork_height < 0:
            logger.warning(f"[Sync] Could not find common ancestor with {peer.addr}")
            return

        # Download and apply blocks from fork_height+1 onwards
        start = fork_height + 1
        peer_height = peer.chain_height
        applied = 0

        while start <= peer_height:
            end = min(start + BATCH_SIZE - 1, peer_height)
            blocks = self._fetch_blocks(peer, start, end)
            if not blocks:
                break
            for block_dict in blocks:
                from ..blockchain.block import Block
                try:
                    block = Block.from_dict(block_dict)
                    ok, _ = self._chain.add_block(block)
                    if ok:
                        applied += 1
                    else:
                        logger.warning(f"[Sync] Block {block.index} rejected")
                        return
                except Exception as e:
                    logger.warning(f"[Sync] Block parse error: {e}")
                    return
            start = end + 1

        if applied:
            logger.info(f"[Sync] Applied {applied} blocks, new height={len(self._chain)-1}")

    def _find_fork(self, peer: Peer, our_height: int) -> int:
        """
        Binary search for the highest block index where our hash == peer's hash.
        Returns that index, or -1 on failure.
        """
        lo, hi = 0, min(our_height, peer.chain_height)

        # Fast path: check if tip hashes match at our_height
        peer_hash = self._fetch_block_hash(peer, our_height)
        our_hash = self._chain.block_hash_at(our_height)
        if peer_hash and peer_hash == our_hash:
            return our_height  # already in sync up to our tip

        while lo < hi:
            mid = (lo + hi + 1) // 2
            peer_hash = self._fetch_block_hash(peer, mid)
            our_hash = self._chain.block_hash_at(mid)
            if peer_hash and peer_hash == our_hash:
                lo = mid
            else:
                hi = mid - 1
        return lo

    def _fetch_block_hash(self, peer: Peer, index: int) -> Optional[str]:
        try:
            resp = requests.get(
                f"{peer.base_url}/p2p/block_hash",
                params={"index": index},
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code == 200:
                return resp.json().get("hash")
        except Exception as e:
            logger.debug(f"[Sync] fetch_block_hash error: {e}")
        return None

    def _fetch_blocks(self, peer: Peer, start: int, end: int) -> List[dict]:
        try:
            resp = requests.get(
                f"{peer.base_url}/p2p/blocks",
                params={"start": start, "end": end},
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code == 200:
                return resp.json().get("blocks", [])
        except Exception as e:
            logger.debug(f"[Sync] fetch_blocks error: {e}")
        return []
