"""
BorderID — Verifiable Claims & Attestations

A VerifiableClaim is a signed statement one DID makes about another
(or about itself). Claims form the trust graph of the Border network.

Claim types:
  NODE_TYPE   — "I am a RELAY / COMPUTE / STORAGE node"
  REGION      — "I operate in US / EU / APAC"
  CAPACITY    — "I have X GB storage / Y GPU-hours / Z Gbps bandwidth"
  STAKE       — "I have staked N BC"
  UPTIME      — "I have been online for N hours"
  PEER_TRUST  — "I vouch for this node" (cross-attestation)
  CUSTOM      — arbitrary key/value signed claim
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class ClaimType(str, Enum):
    NODE_TYPE  = "node_type"
    REGION     = "region"
    CAPACITY   = "capacity"
    STAKE      = "stake"
    UPTIME     = "uptime"
    PEER_TRUST = "peer_trust"
    CUSTOM     = "custom"


@dataclass
class VerifiableClaim:
    """
    A signed statement:
      issuer_did  — who is making the claim
      subject_did — who the claim is about (can be the same as issuer)
      claim_type  — category
      claim_data  — arbitrary payload dict
      issued_at   — unix timestamp
      expires_at  — None = never expires
      signature   — hex signature of claim_hash() by issuer wallet
    """
    claim_id:    str
    issuer_did:  str
    subject_did: str
    claim_type:  ClaimType
    claim_data:  Dict[str, Any]
    issued_at:   float
    expires_at:  Optional[float]  = None
    signature:   Optional[str]    = None

    # ── Hash ──────────────────────────────────────────────
    def claim_hash(self) -> str:
        content = {
            "claim_id":    self.claim_id,
            "issuer_did":  self.issuer_did,
            "subject_did": self.subject_did,
            "claim_type":  self.claim_type,
            "claim_data":  self.claim_data,
            "issued_at":   self.issued_at,
            "expires_at":  self.expires_at,
        }
        return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()

    # ── Validity ──────────────────────────────────────────
    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return time.time() > self.expires_at

    @property
    def is_self_attested(self) -> bool:
        return self.issuer_did == self.subject_did

    # ── Sign / verify ─────────────────────────────────────
    def sign(self, wallet) -> None:
        """Sign with the issuer's wallet. wallet.sign() must accept bytes."""
        self.signature = wallet.sign(self.claim_hash().encode())

    def verify_signature(self, public_key_hex: str) -> bool:
        """Verify issuer signature without a full wallet object."""
        if not self.signature:
            return False
        try:
            import ecdsa, binascii
            vk = ecdsa.VerifyingKey.from_string(
                binascii.unhexlify(public_key_hex),
                curve=ecdsa.SECP256k1,
            )
            return vk.verify(
                binascii.unhexlify(self.signature),
                self.claim_hash().encode(),
            )
        except Exception:
            return False

    # ── Serialisation ─────────────────────────────────────
    def to_dict(self) -> dict:
        return {
            "claim_id":    self.claim_id,
            "issuer_did":  self.issuer_did,
            "subject_did": self.subject_did,
            "claim_type":  self.claim_type,
            "claim_data":  self.claim_data,
            "issued_at":   self.issued_at,
            "expires_at":  self.expires_at,
            "signature":   self.signature,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "VerifiableClaim":
        return cls(
            claim_id    = d["claim_id"],
            issuer_did  = d["issuer_did"],
            subject_did = d["subject_did"],
            claim_type  = ClaimType(d["claim_type"]),
            claim_data  = d["claim_data"],
            issued_at   = d["issued_at"],
            expires_at  = d.get("expires_at"),
            signature   = d.get("signature"),
        )

    # ── Factories ─────────────────────────────────────────
    @classmethod
    def create(
        cls,
        issuer_did:  str,
        subject_did: str,
        claim_type:  ClaimType,
        claim_data:  Dict[str, Any],
        ttl_seconds: Optional[float] = None,
    ) -> "VerifiableClaim":
        now = time.time()
        return cls(
            claim_id    = uuid.uuid4().hex,
            issuer_did  = issuer_did,
            subject_did = subject_did,
            claim_type  = claim_type,
            claim_data  = claim_data,
            issued_at   = now,
            expires_at  = (now + ttl_seconds) if ttl_seconds else None,
        )

    # ── Convenience constructors ──────────────────────────
    @classmethod
    def node_type(cls, issuer_did: str, node_type: str) -> "VerifiableClaim":
        return cls.create(issuer_did, issuer_did, ClaimType.NODE_TYPE,
                          {"node_type": node_type})

    @classmethod
    def region(cls, issuer_did: str, region: str) -> "VerifiableClaim":
        return cls.create(issuer_did, issuer_did, ClaimType.REGION,
                          {"region": region})

    @classmethod
    def capacity(cls, issuer_did: str, **kwargs) -> "VerifiableClaim":
        return cls.create(issuer_did, issuer_did, ClaimType.CAPACITY, dict(kwargs))

    @classmethod
    def stake(cls, issuer_did: str, amount_bc: float) -> "VerifiableClaim":
        return cls.create(issuer_did, issuer_did, ClaimType.STAKE,
                          {"amount_bc": amount_bc})

    @classmethod
    def peer_trust(cls, issuer_did: str, subject_did: str,
                   trust_level: str = "trusted", note: str = "") -> "VerifiableClaim":
        return cls.create(issuer_did, subject_did, ClaimType.PEER_TRUST,
                          {"trust_level": trust_level, "note": note})

    def __repr__(self) -> str:
        exp = f" exp={self.expires_at:.0f}" if self.expires_at else ""
        sig = "✓" if self.signature else "unsigned"
        return (f"<Claim {self.claim_type} "
                f"issuer={self.issuer_did[:30]}... "
                f"subj={self.subject_did[:30]}...{exp} {sig}>")
