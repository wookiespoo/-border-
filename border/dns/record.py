"""
BorderDNS — Record types

A Border name (e.g. alice.border) maps to one or more records.
Records are content-addressed and signed by the owner wallet.

Record types:
  ADDRESS — maps name → BC wallet address (like DNS A record)
  DID     — maps name → did:border:<address>
  CNAME   — maps name → another border name (alias)
  TXT     — arbitrary metadata (key=value pairs)
  SRV     — service endpoint (type, host, port)
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


BORDER_TLD      = "border"       # all names end in .border
REGISTRATION_FEE_BC  = 1.0       # BC to register a name
TRANSFER_FEE_BC      = 0.01      # BC to transfer a name
MIN_NAME_LENGTH      = 3
MAX_NAME_LENGTH      = 63
NAME_TTL_DEFAULT     = 365 * 24 * 3600   # 1 year default TTL


class RecordType(str, Enum):
    ADDRESS = "address"   # → BC wallet address
    DID     = "did"       # → did:border:<address>
    CNAME   = "cname"     # → another .border name
    TXT     = "txt"       # → arbitrary key/value metadata
    SRV     = "srv"       # → service endpoint


@dataclass
class DNSRecord:
    record_id:    str
    name:         str              # e.g. "alice.border"
    record_type:  RecordType
    value:        str              # the resolved value
    owner_address: str             # who controls this record
    created_at:   float
    updated_at:   float
    ttl:          float            = NAME_TTL_DEFAULT
    metadata:     Dict[str, Any]   = field(default_factory=dict)
    signature:    Optional[str]    = None

    @property
    def label(self) -> str:
        """The part before .border"""
        return self.name.replace(f".{BORDER_TLD}", "")

    @property
    def is_expired(self) -> bool:
        return time.time() > (self.created_at + self.ttl)

    def content_hash(self) -> str:
        content = {
            "name":          self.name,
            "record_type":   self.record_type,
            "value":         self.value,
            "owner_address": self.owner_address,
            "created_at":    self.created_at,
        }
        return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()

    def sign(self, wallet) -> None:
        self.signature = wallet.sign(self.content_hash().encode())

    def to_dict(self) -> dict:
        return {
            "record_id":     self.record_id,
            "name":          self.name,
            "record_type":   self.record_type,
            "value":         self.value,
            "owner_address": self.owner_address,
            "created_at":    self.created_at,
            "updated_at":    self.updated_at,
            "ttl":           self.ttl,
            "metadata":      self.metadata,
            "signature":     self.signature,
            "content_hash":  self.content_hash(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DNSRecord":
        return cls(
            record_id     = d["record_id"],
            name          = d["name"],
            record_type   = RecordType(d["record_type"]),
            value         = d["value"],
            owner_address = d["owner_address"],
            created_at    = d["created_at"],
            updated_at    = d.get("updated_at", d["created_at"]),
            ttl           = d.get("ttl", NAME_TTL_DEFAULT),
            metadata      = d.get("metadata", {}),
            signature     = d.get("signature"),
        )

    @classmethod
    def create(cls, name: str, record_type: RecordType, value: str,
               owner_address: str, ttl: float = NAME_TTL_DEFAULT,
               metadata: Optional[Dict] = None) -> "DNSRecord":
        if not name.endswith(f".{BORDER_TLD}"):
            name = f"{name}.{BORDER_TLD}"
        now = time.time()
        return cls(
            record_id     = uuid.uuid4().hex[:16],
            name          = name.lower(),
            record_type   = record_type,
            value         = value,
            owner_address = owner_address,
            created_at    = now,
            updated_at    = now,
            ttl           = ttl,
            metadata      = metadata or {},
        )

    def __repr__(self) -> str:
        return f"<DNSRecord {self.name} {self.record_type} → {self.value[:40]}>"


def validate_name(name: str) -> tuple[bool, str]:
    """Returns (valid, reason). Strips .border suffix before checking."""
    label = name.lower().replace(f".{BORDER_TLD}", "").strip(".")
    if len(label) < MIN_NAME_LENGTH:
        return False, f"Name too short (min {MIN_NAME_LENGTH} chars)"
    if len(label) > MAX_NAME_LENGTH:
        return False, f"Name too long (max {MAX_NAME_LENGTH} chars)"
    if not all(c.isalnum() or c == "-" for c in label):
        return False, "Only alphanumeric and hyphens allowed"
    if label.startswith("-") or label.endswith("-"):
        return False, "Cannot start or end with hyphen"
    return True, "valid"
