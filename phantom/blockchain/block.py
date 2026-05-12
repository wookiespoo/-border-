"""
BorderCoin Block
The core unit of the BorderCoin blockchain.

Unlike Bitcoin where miners burn electricity on meaningless math,
BorderCoin blocks are EARNED by forwarding real internet traffic
to people in censored regions.

The "work" in Proof of Bandwidth = actual bandwidth provided.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import List, Optional

from .transaction import Transaction


# Minimum bandwidth required per block (100MB)
MIN_BYTES_PER_BLOCK = 100 * 1024 * 1024

# Block reward for producing a valid block
BLOCK_REWARD = 1.0  # BorderCoin

# BorderCoin earned per GB forwarded
BC_PER_GB = 1.0


@dataclass
class BandwidthProof:
    """
    A verified record of bandwidth forwarded by a relay node.
    These replace the nonce/hash difficulty in traditional PoW.
    The "work" is real — people got real internet access.
    """
    receipt_id: str
    relay_address: str      # relay's BorderCoin address
    client_id: str
    bytes_forwarded: int
    timestamp: float
    session_id: str
    relay_signature: str    # relay signed this
    client_signature: Optional[str] = None  # client countersigned (stronger proof)

    def hash(self) -> str:
        content = f"{self.relay_address}:{self.client_id}:{self.bytes_forwarded}:{self.timestamp}"
        return hashlib.sha256(content.encode()).hexdigest()

    def to_dict(self) -> dict:
        return {
            "receipt_id": self.receipt_id,
            "relay_address": self.relay_address,
            "client_id": self.client_id,
            "bytes_forwarded": self.bytes_forwarded,
            "timestamp": self.timestamp,
            "session_id": self.session_id,
            "relay_signature": self.relay_signature,
            "client_signature": self.client_signature,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "BandwidthProof":
        return cls(**d)

    @property
    def border_coin_value(self) -> float:
        """How much BorderCoin this proof is worth."""
        return (self.bytes_forwarded / (1024 ** 3)) * BC_PER_GB


@dataclass
class Block:
    """
    A block in the BorderCoin blockchain.

    Validity requires:
    - Previous hash matches the chain
    - All transactions are valid and signed
    - Bandwidth proofs sum to >= MIN_BYTES_PER_BLOCK
    - No double-spend of receipts
    - Block hash is correct
    """
    index: int
    timestamp: float
    previous_hash: str
    miner_address: str
    bandwidth_proofs: List[BandwidthProof] = field(default_factory=list)
    transactions: List[Transaction] = field(default_factory=list)
    block_hash: str = ""

    # Genesis block hash
    GENESIS_HASH = "0" * 64

    @classmethod
    def genesis(cls) -> "Block":
        """The first block. Hard-coded. The beginning of everything."""
        block = cls(
            index=0,
            timestamp=1748649600.0,  # Border genesis timestamp
            previous_hash=cls.GENESIS_HASH,
            miner_address="BC_GENESIS_00000000000000000000000000000000",
            bandwidth_proofs=[],
            transactions=[
                Transaction.coinbase(
                    to_address="BC_GENESIS_00000000000000000000000000000000",
                    reward=0.0,
                )
            ],
        )
        block.block_hash = block.compute_hash()
        return block

    def compute_hash(self) -> str:
        """Compute the block's hash from its contents."""
        content = {
            "index": self.index,
            "timestamp": self.timestamp,
            "previous_hash": self.previous_hash,
            "miner_address": self.miner_address,
            "proofs": sorted([p.receipt_id for p in self.bandwidth_proofs]),
            "transactions": [tx.hash() for tx in self.transactions],
        }
        return hashlib.sha256(
            json.dumps(content, sort_keys=True).encode()
        ).hexdigest()

    def finalize(self) -> None:
        """Add coinbase reward and compute final hash."""
        # Add block reward to miner
        total_bandwidth_pc = sum(p.border_coin_value for p in self.bandwidth_proofs)
        total_fees = sum(tx.fee for tx in self.transactions if tx.from_address != Transaction.COINBASE_ADDRESS)
        total_reward = BLOCK_REWARD + total_bandwidth_pc + total_fees

        self.transactions.insert(0, Transaction.coinbase(
            to_address=self.miner_address,
            reward=round(total_reward, 8),
        ))
        self.block_hash = self.compute_hash()

    @property
    def total_bytes(self) -> int:
        return sum(p.bytes_forwarded for p in self.bandwidth_proofs)

    @property
    def total_bandwidth_pc(self) -> float:
        return sum(p.border_coin_value for p in self.bandwidth_proofs)

    @property
    def is_genesis(self) -> bool:
        return self.index == 0

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "timestamp": self.timestamp,
            "previous_hash": self.previous_hash,
            "miner_address": self.miner_address,
            "bandwidth_proofs": [p.to_dict() for p in self.bandwidth_proofs],
            "transactions": [tx.to_dict() for tx in self.transactions],
            "block_hash": self.block_hash,
            "total_bytes": self.total_bytes,
            "total_bandwidth_pc": self.total_bandwidth_pc,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Block":
        block = cls(
            index=d["index"],
            timestamp=d["timestamp"],
            previous_hash=d["previous_hash"],
            miner_address=d["miner_address"],
            bandwidth_proofs=[BandwidthProof.from_dict(p) for p in d.get("bandwidth_proofs", [])],
            transactions=[Transaction.from_dict(tx) for tx in d.get("transactions", [])],
            block_hash=d.get("block_hash", ""),
        )
        return block
