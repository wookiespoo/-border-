"""
BorderCoin Block
Proof of Bandwidth + Proof of Compute + Proof of Storage.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import List, Optional

from .transaction import Transaction

MIN_BYTES_PER_BLOCK  = 100 * 1024 * 1024
BLOCK_REWARD         = 1.0
BC_PER_GB            = 1.0
BC_PER_COMPUTE_HOUR  = 2.0
BC_PER_GB_PER_DAY    = 0.01


@dataclass
class BandwidthProof:
    receipt_id:       str
    relay_address:    str
    client_id:        str
    bytes_forwarded:  int
    timestamp:        float
    session_id:       str
    relay_signature:  str
    client_signature: Optional[str] = None

    def hash(self) -> str:
        content = f"{self.relay_address}:{self.client_id}:{self.bytes_forwarded}:{self.timestamp}"
        return hashlib.sha256(content.encode()).hexdigest()

    def to_dict(self) -> dict:
        return {"receipt_id":self.receipt_id,"relay_address":self.relay_address,
                "client_id":self.client_id,"bytes_forwarded":self.bytes_forwarded,
                "timestamp":self.timestamp,"session_id":self.session_id,
                "relay_signature":self.relay_signature,"client_signature":self.client_signature}

    @classmethod
    def from_dict(cls, d: dict) -> "BandwidthProof":
        return cls(**d)

    @property
    def border_coin_value(self) -> float:
        return (self.bytes_forwarded / (1024 ** 3)) * BC_PER_GB


@dataclass
class ComputeProofRecord:
    proof_id:        str
    job_id:          str
    worker_address:  str
    client_address:  str
    compute_seconds: float
    bytes_processed: int
    input_hash:      str
    output_hash:     str
    timestamp:       float
    price_bc:        float = 0.0

    @property
    def compute_reward_bc(self) -> float:
        hours = self.compute_seconds / 3600
        gb    = self.bytes_processed / (1024 ** 3)
        return round(max(hours * BC_PER_COMPUTE_HOUR + gb * 0.1, self.price_bc), 8)

    def hash(self) -> str:
        content = f"{self.proof_id}:{self.worker_address}:{self.input_hash}:{self.output_hash}"
        return hashlib.sha256(content.encode()).hexdigest()

    def to_dict(self) -> dict:
        return {"proof_id":self.proof_id,"job_id":self.job_id,"worker_address":self.worker_address,
                "client_address":self.client_address,"compute_seconds":self.compute_seconds,
                "bytes_processed":self.bytes_processed,"input_hash":self.input_hash,
                "output_hash":self.output_hash,"timestamp":self.timestamp,"price_bc":self.price_bc}

    @classmethod
    def from_dict(cls, d: dict) -> "ComputeProofRecord":
        fields = {"proof_id","job_id","worker_address","client_address",
                  "compute_seconds","bytes_processed","input_hash","output_hash","timestamp","price_bc"}
        return cls(**{k: d[k] for k in fields if k in d})


@dataclass
class StorageProofRecord:
    proof_id:         str
    proof_type:       str
    node_address:     str
    owner_address:    str
    chunk_id:         str
    file_id:          str
    bytes_stored:     int
    duration_seconds: float
    timestamp:        float
    reward_bc:        float = 0.0
    # Ed25519 signature + public key so the chain can verify authenticity
    node_signature:   str   = ""
    node_public_key:  str   = ""

    def hash(self) -> str:
        """Canonical hash over fields that a storage node commits to."""
        content = json.dumps({
            "proof_id":         self.proof_id,
            "node_address":     self.node_address,
            "chunk_id":         self.chunk_id,
            "bytes_stored":     self.bytes_stored,
            "duration_seconds": self.duration_seconds,
            "timestamp":        self.timestamp,
        }, sort_keys=True)
        return hashlib.sha256(content.encode()).hexdigest()

    def verify_signature(self) -> bool:
        """Return True if node_signature is a valid Ed25519 sig over hash()."""
        if not self.node_signature or not self.node_public_key:
            return False
        from .wallet import BorderWallet
        return BorderWallet.verify(
            self.node_public_key,
            self.hash().encode(),
            self.node_signature,
        )

    def to_dict(self) -> dict:
        return {"proof_id":self.proof_id,"proof_type":self.proof_type,"node_address":self.node_address,
                "owner_address":self.owner_address,"chunk_id":self.chunk_id,"file_id":self.file_id,
                "bytes_stored":self.bytes_stored,"duration_seconds":self.duration_seconds,
                "timestamp":self.timestamp,"reward_bc":self.reward_bc,
                "node_signature":self.node_signature,"node_public_key":self.node_public_key}

    @classmethod
    def from_dict(cls, d: dict) -> "StorageProofRecord":
        fields = {"proof_id","proof_type","node_address","owner_address","chunk_id",
                  "file_id","bytes_stored","duration_seconds","timestamp","reward_bc",
                  "node_signature","node_public_key"}
        return cls(**{k: d[k] for k in fields if k in d})


@dataclass
class Block:
    index:            int
    timestamp:        float
    previous_hash:    str
    miner_address:    str
    bandwidth_proofs: List[BandwidthProof]    = field(default_factory=list)
    transactions:     List[Transaction]        = field(default_factory=list)
    compute_proofs:   List[ComputeProofRecord] = field(default_factory=list)
    storage_proofs:   List[StorageProofRecord] = field(default_factory=list)
    block_hash:       str                      = ""

    GENESIS_HASH = "0" * 64

    @classmethod
    def genesis(cls) -> "Block":
        block = cls(
            index=0, timestamp=1748649600.0,
            previous_hash=cls.GENESIS_HASH,
            miner_address="BC_GENESIS_00000000000000000000000000000000",
            bandwidth_proofs=[],
            transactions=[Transaction.coinbase(
                to_address="BC_GENESIS_00000000000000000000000000000000", reward=0.0)],
        )
        block.block_hash = block.compute_hash()
        return block

    def compute_hash(self) -> str:
        content = {
            "index":          self.index,
            "timestamp":      self.timestamp,
            "previous_hash":  self.previous_hash,
            "miner_address":  self.miner_address,
            "proofs":         sorted([p.receipt_id for p in self.bandwidth_proofs]),
            "compute_proofs": sorted([p.proof_id for p in self.compute_proofs]),
            "storage_proofs": sorted([p.proof_id for p in self.storage_proofs]),
            "transactions":   [tx.hash() for tx in self.transactions],
        }
        return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()

    def finalize(self) -> None:
        total_bw      = sum(p.border_coin_value for p in self.bandwidth_proofs)
        total_compute = sum(p.compute_reward_bc for p in self.compute_proofs)
        total_storage = sum(p.reward_bc for p in self.storage_proofs)
        total_fees    = sum(tx.fee for tx in self.transactions
                           if tx.from_address != Transaction.COINBASE_ADDRESS)
        total_reward  = BLOCK_REWARD + total_bw + total_compute + total_storage + total_fees
        self.transactions.insert(0, Transaction.coinbase(
            to_address=self.miner_address, reward=round(total_reward, 8)))
        self.block_hash = self.compute_hash()

    @property
    def total_bytes(self) -> int:
        return sum(p.bytes_forwarded for p in self.bandwidth_proofs)

    @property
    def total_bandwidth_pc(self) -> float:
        return sum(p.border_coin_value for p in self.bandwidth_proofs)

    @property
    def total_compute_bc(self) -> float:
        return sum(p.compute_reward_bc for p in self.compute_proofs)

    @property
    def total_storage_bc(self) -> float:
        return sum(p.reward_bc for p in self.storage_proofs)

    @property
    def is_genesis(self) -> bool:
        return self.index == 0

    def to_dict(self) -> dict:
        return {
            "index":           self.index,
            "timestamp":       self.timestamp,
            "previous_hash":   self.previous_hash,
            "miner_address":   self.miner_address,
            "bandwidth_proofs":[p.to_dict() for p in self.bandwidth_proofs],
            "compute_proofs":  [p.to_dict() for p in self.compute_proofs],
            "storage_proofs":  [p.to_dict() for p in self.storage_proofs],
            "transactions":    [tx.to_dict() for tx in self.transactions],
            "block_hash":      self.block_hash,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Block":
        block = cls(
            index            = d["index"],
            timestamp        = d["timestamp"],
            previous_hash    = d["previous_hash"],
            miner_address    = d["miner_address"],
            bandwidth_proofs = [BandwidthProof.from_dict(p) for p in d.get("bandwidth_proofs", [])],
            transactions     = [Transaction.from_dict(tx) for tx in d.get("transactions", [])],
            compute_proofs   = [ComputeProofRecord.from_dict(p) for p in d.get("compute_proofs", [])],
            storage_proofs   = [StorageProofRecord.from_dict(p) for p in d.get("storage_proofs", [])],
            block_hash       = d.get("block_hash", ""),
        )
        return block
