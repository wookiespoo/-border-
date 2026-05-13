"""
Border P2P - Gossip protocol.

Propagates new blocks and transactions to the network using a
push-based fanout gossip: broadcast to K random peers, each of whom
forwards to K more, with a seen-message cache to prevent loops.

Message types
-------------
  block       -- a newly mined block (full JSON)
  transaction -- a new signed transaction
  proof       -- a bandwidth / compute / storage proof for the mempool
"""

from __future__ import annotations

import logging
import random
import threading
import time
from collections import OrderedDict
from typing import Callable, Dict, List, Optional

import requests

from .peer import Peer

logger = logging.getLogger("border.p2p.gossip")

GOSSIP_FANOUT = 4            # peers to forward to per hop
GOSSIP_TIMEOUT = 5           # HTTP timeout
SEEN_CACHE_MAX = 2048        # max entries in dedup cache
MSG_TTL = 8                  # max hops


class GossipRouter:
    """
    Sends and receives gossip messages.

    Register handlers for inbound messages:
        router.on("block", my_block_handler)   # handler(payload: dict) -> None

    Broadcast outbound messages:
        router.broadcast("block", block.to_dict())
    """

    def __init__(
        self,
        self_host: str,
        self_port: int,
        get_peers: Callable[[], List[Peer]],
    ):
        self.self_host = self_host
        self.self_port = self_port
        self._get_peers = get_peers
        self._handlers: Dict[str, List[Callable]] = {}
        self._seen: "OrderedDict[str, float]" = OrderedDict()
        self._lock = threading.Lock()

    # -------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------

    def on(self, msg_type: str, handler: Callable[[dict], None]) -> None:
        self._handlers.setdefault(msg_type, []).append(handler)

    def broadcast(self, msg_type: str, payload: dict, ttl: int = MSG_TTL) -> None:
        """Fan out a new message to GOSSIP_FANOUT random peers."""
        msg_id = self._make_id(msg_type, payload)
        self._mark_seen(msg_id)
        envelope = {
            "msg_id": msg_id,
            "msg_type": msg_type,
            "ttl": ttl,
            "origin": f"{self.self_host}:{self.self_port}",
            "payload": payload,
        }
        peers = self._get_peers()
        targets = random.sample(peers, min(GOSSIP_FANOUT, len(peers)))
        for peer in targets:
            threading.Thread(
                target=self._send_to,
                args=(peer, envelope),
                daemon=True,
            ).start()

    def receive(self, envelope: dict) -> bool:
        """
        Called by the HTTP layer when a /p2p/gossip POST arrives.
        Returns True if the message was fresh (not seen before).
        """
        msg_id = envelope.get("msg_id", "")
        msg_type = envelope.get("msg_type", "")
        payload = envelope.get("payload", {})
        ttl = int(envelope.get("ttl", 0))

        if self._already_seen(msg_id):
            return False

        self._mark_seen(msg_id)

        # Invoke local handlers
        for handler in self._handlers.get(msg_type, []):
            try:
                handler(payload)
            except Exception as e:
                logger.warning(f"[Gossip] Handler error ({msg_type}): {e}")

        # Forward with decremented TTL
        if ttl > 1:
            envelope["ttl"] = ttl - 1
            peers = self._get_peers()
            targets = random.sample(peers, min(GOSSIP_FANOUT, len(peers)))
            for peer in targets:
                threading.Thread(
                    target=self._send_to,
                    args=(peer, envelope),
                    daemon=True,
                ).start()

        return True

    # -------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------

    def _send_to(self, peer: Peer, envelope: dict) -> None:
        try:
            requests.post(
                f"{peer.base_url}/p2p/gossip",
                json=envelope,
                timeout=GOSSIP_TIMEOUT,
            )
        except Exception as e:
            logger.debug(f"[Gossip] Send failed to {peer.addr}: {e}")

    def _make_id(self, msg_type: str, payload: dict) -> str:
        import hashlib, json
        raw = json.dumps({"t": msg_type, "p": payload}, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()[:24]

    def _already_seen(self, msg_id: str) -> bool:
        with self._lock:
            return msg_id in self._seen

    def _mark_seen(self, msg_id: str) -> None:
        with self._lock:
            self._seen[msg_id] = time.time()
            while len(self._seen) > SEEN_CACHE_MAX:
                self._seen.popitem(last=False)
