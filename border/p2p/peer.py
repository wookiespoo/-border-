"""
Border P2P - Peer data model.
Tracks connection state, last-seen, and capabilities for a single remote peer.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class PeerState(Enum):
    UNKNOWN = "unknown"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    BANNED = "banned"


@dataclass
class Peer:
    host: str
    port: int
    node_id: str = ""                 # hex pubkey fingerprint
    chain_height: int = 0             # last reported tip
    version: str = "0.1.0"
    state: PeerState = PeerState.UNKNOWN
    last_seen: float = field(default_factory=time.time)
    last_ping: float = 0.0
    fail_count: int = 0
    latency_ms: float = 0.0

    # --- helpers ---

    @property
    def addr(self) -> str:
        return f"{self.host}:{self.port}"

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def touch(self) -> None:
        self.last_seen = time.time()
        self.state = PeerState.CONNECTED
        self.fail_count = 0

    def mark_failure(self, ban_after: int = 5) -> None:
        self.fail_count += 1
        if self.fail_count >= ban_after:
            self.state = PeerState.BANNED
        else:
            self.state = PeerState.DISCONNECTED

    def is_reachable(self) -> bool:
        return self.state not in (PeerState.BANNED,)

    def to_dict(self) -> dict:
        return {
            "host": self.host,
            "port": self.port,
            "node_id": self.node_id,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Peer":
        return cls(
            host=d["host"],
            port=int(d["port"]),
            node_id=d.get("node_id", ""),
            version=d.get("version", "0.1.0"),
        )

    def __hash__(self):
        return hash(self.addr)

    def __eq__(self, other):
        return isinstance(other, Peer) and self.addr == other.addr

    def __repr__(self):
        return f"<Peer {self.addr} h={self.chain_height} {self.state.value}>"
