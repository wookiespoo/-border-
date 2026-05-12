"""
BorderStore — Storage Proof + Challenge System
================================================
Proof of Storage: a node proves it still holds a chunk by
responding to a random challenge it could not have pre-computed.

Challenge: issue a random nonce for a chunk_id
Response:  node returns SHA256(ciphertext + nonce)
Verify:    challenger re-computes SHA256(known_ciphertext + nonce)

If the hashes match, the node has the data. Simple, cheap, verifiable.
Every passed challenge = a StorageProof submitted to the chain = BC earned.
"""

from __future__ import annotations

import hashlib
import secrets
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from .chunk import BC_PER_GB_PER_DAY, BC_PER_CHALLENGE


# ─────────────────────────────────────────────────────────
# Storage Challenge
# ─────────────────────────────────────────────────────────

@dataclass
class StorageChallenge:
    """
    A random challenge issued to a storage node.
    The node must prove it holds chunk_id by responding
    with SHA256(its_ciphertext + nonce).
    """
    challenge_id: str
    chunk_id:     str
    node_address: str
    nonce:        str        # random 32-byte hex
    issued_at:    float      = field(default_factory=time.time)
    expires_at:   float      = 0.0
    TIMEOUT       = 30       # seconds to respond

    def __post_init__(self):
        if self.expires_at == 0.0:
            self.expires_at = self.issued_at + self.TIMEOUT

    @classmethod
    def issue(cls, chunk_id: str, node_address: str) -> "StorageChallenge":
        return cls(
            challenge_id=f"chal_{uuid.uuid4().hex[:12]}",
            chunk_id=chunk_id,
            node_address=node_address,
            nonce=secrets.token_hex(32),
        )

    def expected_response(self, ciphertext: bytes) -> str:
        """What the correct response hash should be."""
        return hashlib.sha256(ciphertext + self.nonce.encode()).hexdigest()

    def is_expired(self) -> bool:
        return time.time() > self.expires_at

    def to_dict(self) -> dict:
        return {
            "challenge_id": self.challenge_id,
            "chunk_id":     self.chunk_id,
            "node_address": self.node_address,
            "nonce":        self.nonce,
            "issued_at":    self.issued_at,
            "expires_at":   self.expires_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "StorageChallenge":
        return cls(**d)


# ─────────────────────────────────────────────────────────
# Storage Proof
# ─────────────────────────────────────────────────────────

@dataclass
class StorageProof:
    """
    Proof that a node has been storing a chunk for a given duration.
    Submitted to the BorderChain to claim BC rewards.

    Two types:
      CHALLENGE  — node responded correctly to a random challenge
      DURATION   — node claims it stored a chunk for N seconds (periodic)
    """
    proof_id:        str
    proof_type:      str        # "CHALLENGE" or "DURATION"
    node_address:    str        # BC wallet of storage node
    owner_address:   str        # BC wallet of file owner
    chunk_id:        str
    file_id:         str
    bytes_stored:    int        # chunk size in bytes
    duration_seconds: float     # how long stored (for DURATION proofs)
    challenge_id:    Optional[str]  = None  # for CHALLENGE proofs
    challenge_nonce: Optional[str]  = None
    response_hash:   Optional[str]  = None  # node's challenge response
    expected_hash:   Optional[str]  = None  # what the correct response was
    timestamp:       float           = field(default_factory=time.time)
    node_signature:  str             = ""

    @classmethod
    def from_challenge(
        cls,
        challenge:    "StorageChallenge",
        node_address: str,
        owner_address:str,
        file_id:      str,
        bytes_stored: int,
        response_hash:str,
        expected_hash:str,
    ) -> "StorageProof":
        return cls(
            proof_id=f"sproof_{uuid.uuid4().hex[:12]}",
            proof_type="CHALLENGE",
            node_address=node_address,
            owner_address=owner_address,
            chunk_id=challenge.chunk_id,
            file_id=file_id,
            bytes_stored=bytes_stored,
            duration_seconds=0.0,
            challenge_id=challenge.challenge_id,
            challenge_nonce=challenge.nonce,
            response_hash=response_hash,
            expected_hash=expected_hash,
        )

    @classmethod
    def from_duration(
        cls,
        node_address:    str,
        owner_address:   str,
        chunk_id:        str,
        file_id:         str,
        bytes_stored:    int,
        duration_seconds: float,
    ) -> "StorageProof":
        return cls(
            proof_id=f"sproof_{uuid.uuid4().hex[:12]}",
            proof_type="DURATION",
            node_address=node_address,
            owner_address=owner_address,
            chunk_id=chunk_id,
            file_id=file_id,
            bytes_stored=bytes_stored,
            duration_seconds=duration_seconds,
        )

    def is_valid(self) -> bool:
        """Verify this proof is internally consistent."""
        if self.proof_type == "CHALLENGE":
            return (
                self.response_hash is not None
                and self.expected_hash is not None
                and self.response_hash == self.expected_hash
            )
        elif self.proof_type == "DURATION":
            return self.duration_seconds > 0 and self.bytes_stored > 0
        return False

    def reward_bc(self) -> float:
        """
        BC earned for this proof.
        CHALLENGE: flat per-challenge reward
        DURATION:  proportional to bytes × time stored
        """
        if self.proof_type == "CHALLENGE":
            return BC_PER_CHALLENGE
        elif self.proof_type == "DURATION":
            gb   = self.bytes_stored / (1024 ** 3)
            days = self.duration_seconds / 86400
            return round(gb * days * BC_PER_GB_PER_DAY, 8)
        return 0.0

    def hash(self) -> str:
        content = (
            f"{self.proof_id}:{self.node_address}:{self.chunk_id}:"
            f"{self.bytes_stored}:{self.timestamp}"
        )
        return hashlib.sha256(content.encode()).hexdigest()

    def to_dict(self) -> dict:
        return {
            "proof_id":         self.proof_id,
            "proof_type":       self.proof_type,
            "node_address":     self.node_address,
            "owner_address":    self.owner_address,
            "chunk_id":         self.chunk_id,
            "file_id":          self.file_id,
            "bytes_stored":     self.bytes_stored,
            "duration_seconds": self.duration_seconds,
            "challenge_id":     self.challenge_id,
            "challenge_nonce":  self.challenge_nonce,
            "response_hash":    self.response_hash,
            "expected_hash":    self.expected_hash,
            "timestamp":        self.timestamp,
            "node_signature":   self.node_signature,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "StorageProof":
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__})
