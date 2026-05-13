"""
Border P2P - Peer discovery.

Bootstrap: connect to a hardcoded/configured seed list.
Exchange:  ask each connected peer for its peer table (/p2p/peers).
Persist:   save/load the peer table to disk between restarts.
"""

from __future__ import annotations

import json
import logging
import random
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Set

import requests

from .peer import Peer, PeerState

logger = logging.getLogger("border.p2p.discovery")

# Default bootstrap nodes (override via config)
DEFAULT_SEEDS: List[str] = [
    # "seed1.border.network:9000",
    # "seed2.border.network:9000",
]

MAX_PEERS = 50
EXCHANGE_INTERVAL = 60       # seconds between peer-exchange rounds
PING_INTERVAL = 30           # seconds between liveness pings
REQUEST_TIMEOUT = 5          # HTTP timeout in seconds


class PeerDiscovery:
    """
    Manages peer discovery and maintenance for a Border node.

    Usage:
        discovery = PeerDiscovery(self_host="0.0.0.0", self_port=9000,
                                  seeds=["seed.border.network:9000"],
                                  data_dir="/var/border")
        discovery.start()
        peers = discovery.get_peers()   # reachable peers
        discovery.stop()
    """

    def __init__(
        self,
        self_host: str,
        self_port: int,
        node_id: str = "",
        seeds: Optional[List[str]] = None,
        data_dir: Optional[str] = None,
        max_peers: int = MAX_PEERS,
    ):
        self.self_host = self_host
        self.self_port = self_port
        self.node_id = node_id
        self.max_peers = max_peers
        self._peers: Dict[str, Peer] = {}   # addr -> Peer
        self._lock = threading.RLock()
        self._running = False
        self._threads: List[threading.Thread] = []

        self._persist_path: Optional[Path] = None
        if data_dir:
            self._persist_path = Path(data_dir) / "peers.json"

        # Bootstrap from seeds
        seed_list = seeds if seeds is not None else DEFAULT_SEEDS
        for addr in seed_list:
            try:
                host, port_str = addr.rsplit(":", 1)
                self._add_peer(Peer(host=host, port=int(port_str)))
            except ValueError:
                logger.warning(f"[Discovery] Bad seed address: {addr}")

        self._load_persisted()

    # -------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        t1 = threading.Thread(target=self._ping_loop, daemon=True, name="p2p-ping")
        t2 = threading.Thread(target=self._exchange_loop, daemon=True, name="p2p-exchange")
        t1.start()
        t2.start()
        self._threads = [t1, t2]
        logger.info(f"[Discovery] Started on {self.self_host}:{self.self_port} "
                    f"with {len(self._peers)} known peers")

    def stop(self) -> None:
        self._running = False

    def add_peer(self, host: str, port: int, node_id: str = "") -> Peer:
        with self._lock:
            p = Peer(host=host, port=port, node_id=node_id)
            return self._add_peer(p)

    def get_peers(self, reachable_only: bool = True) -> List[Peer]:
        with self._lock:
            peers = list(self._peers.values())
            if reachable_only:
                peers = [p for p in peers if p.is_reachable()]
            return peers

    def announce_self(self) -> None:
        """Push our address to all known reachable peers."""
        for peer in self.get_peers():
            self._announce_to(peer)

    # -------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------

    def _self_addr(self) -> str:
        return f"{self.self_host}:{self.self_port}"

    def _add_peer(self, peer: Peer) -> Peer:
        if peer.addr == self._self_addr():
            return peer                          # don't add ourselves
        if len(self._peers) >= self.max_peers:
            return peer
        if peer.addr not in self._peers:
            self._peers[peer.addr] = peer
        return self._peers[peer.addr]

    def _ping_loop(self) -> None:
        while self._running:
            try:
                for peer in list(self._peers.values()):
                    if not self._running:
                        break
                    self._ping(peer)
                self._persist()
            except Exception as e:
                logger.debug(f"[Discovery] ping_loop error: {e}")
            time.sleep(PING_INTERVAL)

    def _exchange_loop(self) -> None:
        # Stagger first exchange so pings have time to run
        time.sleep(10)
        while self._running:
            try:
                reachable = self.get_peers(reachable_only=True)
                sample = random.sample(reachable, min(5, len(reachable)))
                for peer in sample:
                    self._exchange_peers(peer)
            except Exception as e:
                logger.debug(f"[Discovery] exchange_loop error: {e}")
            time.sleep(EXCHANGE_INTERVAL)

    def _ping(self, peer: Peer) -> bool:
        """GET /p2p/ping — returns True if alive."""
        if not peer.is_reachable():
            return False
        try:
            t0 = time.time()
            resp = requests.get(
                f"{peer.base_url}/p2p/ping",
                timeout=REQUEST_TIMEOUT,
                params={"from_host": self.self_host,
                        "from_port": self.self_port,
                        "node_id": self.node_id},
            )
            latency = (time.time() - t0) * 1000
            if resp.status_code == 200:
                data = resp.json()
                with self._lock:
                    peer.touch()
                    peer.latency_ms = latency
                    peer.chain_height = data.get("chain_height", 0)
                logger.debug(f"[Discovery] Ping OK {peer.addr} h={peer.chain_height} {latency:.0f}ms")
                return True
        except Exception as e:
            logger.debug(f"[Discovery] Ping failed {peer.addr}: {e}")
        with self._lock:
            peer.mark_failure()
        return False

    def _exchange_peers(self, peer: Peer) -> None:
        """GET /p2p/peers — merge returned peer list into our table."""
        try:
            resp = requests.get(
                f"{peer.base_url}/p2p/peers",
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code == 200:
                raw = resp.json().get("peers", [])
                added = 0
                with self._lock:
                    for d in raw:
                        p = Peer.from_dict(d)
                        if p.addr not in self._peers and p.addr != self._self_addr():
                            self._add_peer(p)
                            added += 1
                if added:
                    logger.info(f"[Discovery] +{added} peers from {peer.addr}")
        except Exception as e:
            logger.debug(f"[Discovery] Exchange failed {peer.addr}: {e}")

    def _announce_to(self, peer: Peer) -> None:
        """POST /p2p/announce — tell peer about ourselves."""
        try:
            requests.post(
                f"{peer.base_url}/p2p/announce",
                json={"host": self.self_host,
                      "port": self.self_port,
                      "node_id": self.node_id},
                timeout=REQUEST_TIMEOUT,
            )
        except Exception:
            pass

    def _persist(self) -> None:
        if not self._persist_path:
            return
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                data = [p.to_dict() for p in self._peers.values()]
            self._persist_path.write_text(json.dumps(data, indent=2))
        except Exception as e:
            logger.debug(f"[Discovery] Persist error: {e}")

    def _load_persisted(self) -> None:
        if not self._persist_path or not self._persist_path.exists():
            return
        try:
            data = json.loads(self._persist_path.read_text())
            count = 0
            for d in data:
                p = Peer.from_dict(d)
                self._add_peer(p)
                count += 1
            logger.info(f"[Discovery] Loaded {count} peers from disk")
        except Exception as e:
            logger.warning(f"[Discovery] Could not load peers: {e}")
