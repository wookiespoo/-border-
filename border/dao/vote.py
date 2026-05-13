"""
BorderDAO — Votes

Votes are weighted by the voter's BC balance at time of voting.
One address = one vote position (can change until voting closes).
Signed by voter's wallet to prevent spoofing.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class VoteChoice(str, Enum):
    YES     = "yes"
    NO      = "no"
    ABSTAIN = "abstain"


@dataclass
class Vote:
    vote_id:       str
    proposal_id:   str
    voter_did:     str
    voter_address: str
    choice:        VoteChoice
    weight:        float          # BC balance at time of vote
    timestamp:     float          = field(default_factory=time.time)
    signature:     Optional[str]  = None
    reason:        str            = ""  # optional on-chain comment

    def hash(self) -> str:
        content = (f"{self.vote_id}:{self.proposal_id}:"
                   f"{self.voter_address}:{self.choice}:{self.weight}:{self.timestamp}")
        return hashlib.sha256(content.encode()).hexdigest()

    def sign(self, wallet) -> None:
        self.signature = wallet.sign(self.hash().encode())

    def verify_signature(self, public_key_b64: str) -> bool:
        """Verify the voter's Ed25519 signature over hash()."""
        if not self.signature:
            return False
        try:
            import base64
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
            pub_bytes = base64.b64decode(public_key_b64)
            pub_key   = Ed25519PublicKey.from_public_bytes(pub_bytes)
            sig_bytes = base64.b64decode(self.signature)
            pub_key.verify(sig_bytes, self.hash().encode())
            return True
        except Exception:
            return False

    def to_dict(self) -> dict:
        return {
            "vote_id":       self.vote_id,
            "proposal_id":   self.proposal_id,
            "voter_did":     self.voter_did,
            "voter_address": self.voter_address,
            "choice":        self.choice,
            "weight":        round(self.weight, 8),
            "timestamp":     self.timestamp,
            "signature":     self.signature,
            "reason":        self.reason,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Vote":
        return cls(
            vote_id       = d["vote_id"],
            proposal_id   = d["proposal_id"],
            voter_did     = d["voter_did"],
            voter_address = d["voter_address"],
            choice        = VoteChoice(d["choice"]),
            weight        = d["weight"],
            timestamp     = d.get("timestamp", time.time()),
            signature     = d.get("signature"),
            reason        = d.get("reason", ""),
        )

    @classmethod
    def create(cls, proposal_id: str, voter_did: str, voter_address: str,
               choice: VoteChoice, weight: float, reason: str = "") -> "Vote":
        return cls(
            vote_id       = uuid.uuid4().hex,
            proposal_id   = proposal_id,
            voter_did     = voter_did,
            voter_address = voter_address,
            choice        = choice,
            weight        = weight,
            reason        = reason,
        )

    def __repr__(self) -> str:
        return (f"<Vote {self.choice} w={self.weight:.2f}BC "
                f"by {self.voter_address[:16]}...>")
