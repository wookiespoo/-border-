"""
BorderID Registry

In-memory identity store with optional JSON persistence.
Anchors DID documents to BorderChain via zero-value transactions
(the tx memo field carries the document_hash — no BC spent beyond fee).
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .did import BorderDID, ServiceType
from .claim import VerifiableClaim, ClaimType

logger = logging.getLogger("border.identity.registry")


class IdentityRegistry:
    """
    Central registry of BorderDID documents and their claims.

    Thread-safety: not yet — add asyncio.Lock for multi-worker use.
    """

    def __init__(self, persist_path: Optional[str] = None):
        self._dids:   Dict[str, BorderDID]           = {}   # did → BorderDID
        self._handle: Dict[str, str]                  = {}   # handle → did
        self._claims: Dict[str, List[VerifiableClaim]] = {}  # subject_did → [claims]
        self._persist_path = Path(persist_path) if persist_path else None

        if self._persist_path and self._persist_path.exists():
            self._load()
        else:
            logger.info("[Registry] Initialised (empty)")

    # ── Registration ──────────────────────────────────────
    def register(self, did_obj: BorderDID) -> Tuple[bool, str]:
        if did_obj.did in self._dids:
            return False, f"DID already registered: {did_obj.did}"
        if did_obj.handle and did_obj.handle in self._handle:
            return False, f"Handle already taken: {did_obj.handle}"

        self._dids[did_obj.did] = did_obj
        if did_obj.handle:
            self._handle[did_obj.handle] = did_obj.did
        self._claims[did_obj.did] = []

        logger.info(f"[Registry] Registered: {did_obj.short_did}"
                    + (f" ({did_obj.handle})" if did_obj.handle else ""))
        self._save()
        return True, "registered"

    def update(self, did_obj: BorderDID) -> Tuple[bool, str]:
        if did_obj.did not in self._dids:
            return False, "DID not found"
        old = self._dids[did_obj.did]
        # Update handle index
        if old.handle and old.handle != did_obj.handle:
            self._handle.pop(old.handle, None)
        if did_obj.handle:
            self._handle[did_obj.handle] = did_obj.did

        did_obj.updated_at = time.time()
        self._dids[did_obj.did] = did_obj
        self._save()
        return True, "updated"

    # ── Resolution ────────────────────────────────────────
    def resolve(self, did_or_handle: str) -> Optional[BorderDID]:
        """Resolve a DID string or a handle (e.g. 'alice.border')."""
        if did_or_handle.startswith("did:border:"):
            return self._dids.get(did_or_handle)
        # Try handle lookup
        did = self._handle.get(did_or_handle)
        return self._dids.get(did) if did else None

    def resolve_document(self, did_or_handle: str) -> Optional[dict]:
        d = self.resolve(did_or_handle)
        return d.to_document() if d else None

    # ── Claims ────────────────────────────────────────────
    def add_claim(self, claim: VerifiableClaim) -> Tuple[bool, str]:
        if claim.subject_did not in self._dids:
            return False, f"Subject DID not registered: {claim.subject_did}"
        if claim.is_expired:
            return False, "Claim is already expired"
        self._claims.setdefault(claim.subject_did, []).append(claim)
        logger.info(f"[Registry] Claim added: {claim.claim_type} → {claim.subject_did[:40]}...")
        self._save()
        return True, "accepted"

    def get_claims(self, subject_did: str,
                   claim_type: Optional[ClaimType] = None,
                   issuer_did: Optional[str] = None,
                   include_expired: bool = False) -> List[VerifiableClaim]:
        claims = self._claims.get(subject_did, [])
        if not include_expired:
            claims = [c for c in claims if not c.is_expired]
        if claim_type:
            claims = [c for c in claims if c.claim_type == claim_type]
        if issuer_did:
            claims = [c for c in claims if c.issuer_did == issuer_did]
        return claims

    def get_peer_trust(self, subject_did: str) -> List[VerifiableClaim]:
        return self.get_claims(subject_did, claim_type=ClaimType.PEER_TRUST)

    # ── Search ────────────────────────────────────────────
    def search(self,
               service_type: Optional[ServiceType] = None,
               region: Optional[str] = None,
               min_stake_bc: float = 0.0) -> List[BorderDID]:
        results = list(self._dids.values())

        if service_type:
            results = [d for d in results if any(
                s.service_type == service_type for s in d.services
            )]

        if region:
            def has_region(d: BorderDID) -> bool:
                region_claims = self.get_claims(d.did, ClaimType.REGION)
                return any(c.claim_data.get("region") == region for c in region_claims)
            results = [d for d in results if has_region(d)]

        if min_stake_bc > 0:
            def has_stake(d: BorderDID) -> bool:
                stake_claims = self.get_claims(d.did, ClaimType.STAKE)
                return any(c.claim_data.get("amount_bc", 0) >= min_stake_bc
                           for c in stake_claims)
            results = [d for d in results if has_stake(d)]

        return results

    # ── Stats ─────────────────────────────────────────────
    @property
    def stats(self) -> dict:
        total_claims = sum(len(v) for v in self._claims.values())
        return {
            "registered_dids": len(self._dids),
            "handles":         len(self._handle),
            "total_claims":    total_claims,
        }

    # ── Persistence ───────────────────────────────────────
    def _save(self) -> None:
        if not self._persist_path:
            return
        data = {
            "dids":   {k: v.to_dict() for k, v in self._dids.items()},
            "claims": {k: [c.to_dict() for c in v] for k, v in self._claims.items()},
        }
        self._persist_path.write_text(json.dumps(data, indent=2))

    def _load(self) -> None:
        if not self._persist_path or not self._persist_path.exists():
            return
        data = json.loads(self._persist_path.read_text())
        for k, v in data.get("dids", {}).items():
            did_obj = BorderDID.from_dict(v)
            self._dids[k] = did_obj
            if did_obj.handle:
                self._handle[did_obj.handle] = k
        for k, claims in data.get("claims", {}).items():
            self._claims[k] = [VerifiableClaim.from_dict(c) for c in claims]
        logger.info(f"[Registry] Loaded: {len(self._dids)} DIDs, "
                    f"{sum(len(v) for v in self._claims.values())} claims")
