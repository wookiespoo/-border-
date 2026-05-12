"""
Phantom Bandwidth Ledger
Cryptographic proof of bandwidth — the foundation of BorderCoin.
Every byte forwarded is signed and verifiable.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class BandwidthReceipt:
    """
    A signed record of bandwidth provided by a relay to a client.
    These are the atomic unit of BorderCoin mining.
    """
    receipt_id: str
    relay_id: str
    client_id: str
    bytes_forwarded: int
    timestamp: float
    session_id: str
    signature: Optional[str] = None  # Ed25519 sig of receipt hash

    def to_dict(self) -> dict:
        return {
            "receipt_id": self.receipt_id,
            "relay_id": self.relay_id,
            "client_id": self.client_id,
            "bytes_forwarded": self.bytes_forwarded,
            "timestamp": self.timestamp,
            "session_id": self.session_id,
            "signature": self.signature,
        }

    def hash(self) -> str:
        """Deterministic hash of receipt content (for signing)."""
        content = f"{self.relay_id}:{self.client_id}:{self.bytes_forwarded}:{self.timestamp}:{self.session_id}"
        return hashlib.sha256(content.encode()).hexdigest()

    @classmethod
    def from_dict(cls, data: dict) -> "BandwidthReceipt":
        return cls(**data)


@dataclass
class BandwidthSummary:
    """Aggregated bandwidth stats for a relay node."""
    node_id: str
    total_bytes: int = 0
    total_receipts: int = 0
    unique_clients: int = 0
    period_start: float = field(default_factory=time.time)
    period_end: Optional[float] = None

    @property
    def border_coin_earned(self) -> float:
        """Estimated BorderCoin earned (1 PC per GB forwarded)."""
        return self.total_bytes / (1024 ** 3)

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "total_bytes": self.total_bytes,
            "total_bytes_human": _human_bytes(self.total_bytes),
            "total_receipts": self.total_receipts,
            "unique_clients": self.unique_clients,
            "border_coin_earned": round(self.border_coin_earned, 6),
            "period_start": self.period_start,
            "period_end": self.period_end,
        }


class BandwidthLedger:
    """
    Local ledger of bandwidth receipts for a relay node.
    Receipts accumulate here and can be submitted to the blockchain
    to claim BorderCoin rewards.
    """

    def __init__(
        self,
        node_id: str,
        persist_path: Optional[str] = None,
    ):
        self.node_id = node_id
        self._receipts: List[BandwidthReceipt] = []
        self._client_set: set = set()
        self._persist_path = Path(persist_path) if persist_path else None

        if self._persist_path and self._persist_path.exists():
            self._load()

    def record(
        self,
        client_id: str,
        bytes_forwarded: int,
        session_id: str,
    ) -> BandwidthReceipt:
        """Record a bandwidth event and return the signed receipt."""
        receipt = BandwidthReceipt(
            receipt_id=f"rcpt_{uuid.uuid4().hex[:12]}",
            relay_id=self.node_id,
            client_id=client_id,
            bytes_forwarded=bytes_forwarded,
            timestamp=time.time(),
            session_id=session_id,
        )

        # Sign the receipt (simplified — production uses Ed25519 private key)
        receipt.signature = self._sign(receipt.hash())

        self._receipts.append(receipt)
        self._client_set.add(client_id)

        if self._persist_path:
            self._save()

        return receipt

    def get_summary(self) -> BandwidthSummary:
        """Get aggregated stats for this node."""
        summary = BandwidthSummary(
            node_id=self.node_id,
            total_bytes=sum(r.bytes_forwarded for r in self._receipts),
            total_receipts=len(self._receipts),
            unique_clients=len(self._client_set),
        )
        return summary

    def get_pending_receipts(self) -> List[BandwidthReceipt]:
        """Get receipts not yet submitted to the blockchain."""
        return [r for r in self._receipts if not getattr(r, '_submitted', False)]

    def export_for_submission(self) -> dict:
        """
        Export receipts for blockchain submission.
        In production this would be signed by the node's Ed25519 key
        and broadcast to the BorderCoin network.
        """
        pending = self.get_pending_receipts()
        summary = self.get_summary()

        return {
            "node_id": self.node_id,
            "receipts": [r.to_dict() for r in pending],
            "summary": summary.to_dict(),
            "submission_hash": hashlib.sha256(
                json.dumps([r.receipt_id for r in pending]).encode()
            ).hexdigest(),
        }

    def _sign(self, content_hash: str) -> str:
        """
        Sign a receipt hash with the node's private key.
        Simplified: in production uses Ed25519.
        """
        # TODO: replace with actual Ed25519 signing
        return hashlib.sha256(f"{self.node_id}:{content_hash}".encode()).hexdigest()

    def _save(self) -> None:
        if not self._persist_path:
            return
        data = [r.to_dict() for r in self._receipts]
        self._persist_path.write_text(json.dumps(data, indent=2))

    def _load(self) -> None:
        if not self._persist_path or not self._persist_path.exists():
            return
        data = json.loads(self._persist_path.read_text())
        self._receipts = [BandwidthReceipt.from_dict(r) for r in data]
        self._client_set = {r.client_id for r in self._receipts}


def _human_bytes(n: int) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"
