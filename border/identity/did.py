"""
BorderID — Decentralised Identity
did:border:<wallet_address>
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


DID_METHOD = "border"


class ServiceType(str, Enum):
    RELAY    = "BorderRelay"
    COMPUTE  = "BorderCompute"
    STORAGE  = "BorderStorage"
    IDENTITY = "BorderID"
    WALLET   = "BorderWallet"


@dataclass
class ServiceEndpoint:
    service_id:   str
    service_type: ServiceType
    endpoint:     str
    description:  str = ""

    def to_dict(self) -> dict:
        return {
            "id":              self.service_id,
            "type":            self.service_type,
            "serviceEndpoint": self.endpoint,
            "description":     self.description,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ServiceEndpoint":
        return cls(
            service_id   = d["id"],
            service_type = ServiceType(d["type"]),
            endpoint     = d["serviceEndpoint"],
            description  = d.get("description", ""),
        )


@dataclass
class BorderDID:
    """
    did:border:<wallet_address>
    Self-sovereign identifier anchored to a wallet keypair.
    No central authority. No registration fee.
    """
    wallet_address: str
    public_key_hex: str
    created_at:     float                  = field(default_factory=time.time)
    updated_at:     float                  = field(default_factory=time.time)
    handle:         Optional[str]          = None
    services:       List[ServiceEndpoint]  = field(default_factory=list)
    metadata:       Dict[str, str]         = field(default_factory=dict)

    @property
    def did(self) -> str:
        return f"did:{DID_METHOD}:{self.wallet_address}"

    @property
    def short_did(self) -> str:
        return f"did:border:{self.wallet_address[:20]}..."

    def document_hash(self) -> str:
        doc = {
            "did":        self.did,
            "public_key": self.public_key_hex,
            "created_at": self.created_at,
            "handle":     self.handle,
            "services":   sorted([s.service_id for s in self.services]),
        }
        return hashlib.sha256(json.dumps(doc, sort_keys=True).encode()).hexdigest()

    def add_service(self, service_type: ServiceType, endpoint: str, description: str = "") -> ServiceEndpoint:
        svc = ServiceEndpoint(
            service_id   = f"{self.did}#service-{len(self.services)+1}",
            service_type = service_type,
            endpoint     = endpoint,
            description  = description,
        )
        self.services.append(svc)
        self.updated_at = time.time()
        return svc

    def get_services(self, service_type: ServiceType) -> List[ServiceEndpoint]:
        return [s for s in self.services if s.service_type == service_type]

    def to_document(self) -> dict:
        return {
            "@context": ["https://www.w3.org/ns/did/v1", "https://border.network/did/v1"],
            "id":       self.did,
            "handle":   self.handle,
            "verificationMethod": [{
                "id":           f"{self.did}#key-1",
                "type":         "EcdsaSecp256k1VerificationKey2019",
                "controller":   self.did,
                "publicKeyHex": self.public_key_hex,
            }],
            "authentication": [f"{self.did}#key-1"],
            "service":        [s.to_dict() for s in self.services],
            "created":        self.created_at,
            "updated":        self.updated_at,
            "metadata":       self.metadata,
        }

    def to_dict(self) -> dict:
        return {
            "wallet_address": self.wallet_address,
            "public_key_hex": self.public_key_hex,
            "created_at":     self.created_at,
            "updated_at":     self.updated_at,
            "handle":         self.handle,
            "services":       [s.to_dict() for s in self.services],
            "metadata":       self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "BorderDID":
        obj = cls(
            wallet_address = d["wallet_address"],
            public_key_hex = d["public_key_hex"],
            created_at     = d.get("created_at", time.time()),
            updated_at     = d.get("updated_at", time.time()),
            handle         = d.get("handle"),
            metadata       = d.get("metadata", {}),
        )
        obj.services = [ServiceEndpoint.from_dict(s) for s in d.get("services", [])]
        return obj

    @classmethod
    def from_wallet(cls, wallet, handle: Optional[str] = None) -> "BorderDID":
        try:
            pub_hex = wallet.public_key.to_string().hex()
        except Exception:
            pub_hex = wallet.address
        return cls(
            wallet_address = wallet.address,
            public_key_hex = pub_hex,
            handle         = handle,
        )

    def __repr__(self) -> str:
        h = f" ({self.handle})" if self.handle else ""
        return f"<BorderDID {self.short_did}{h} services={len(self.services)}>"
