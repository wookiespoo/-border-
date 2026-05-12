"""
BorderID — Reputation Engine

Score = weighted sum of:
  - Proofs submitted to chain (bandwidth, compute, storage)
  - BC staked (skin in the game)
  - Uptime claim
  - Peer trust attestations from other registered nodes
  - Penalties: failed challenges, disputed jobs

Score is in range [0, ∞) but typically 0–1000 for active nodes.
Workers/nodes with score ≥ MIN_SCORE get priority routing.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List, Optional

from .claim import ClaimType, VerifiableClaim

if TYPE_CHECKING:
    from .registry import IdentityRegistry


# ── Weights ───────────────────────────────────────────────
W_BANDWIDTH_MB   = 0.001   # per MB forwarded (from chain proofs)
W_COMPUTE_HOUR   = 5.0     # per GPU-hour
W_STORAGE_GB_DAY = 0.5     # per GB-day stored
W_STAKE_BC       = 0.2     # per BC staked
W_UPTIME_HOUR    = 0.1     # per hour uptime claimed
W_PEER_TRUST     = 10.0    # per unique peer vouching for node
W_PENALTY        = -20.0   # per dispute / failed proof

MIN_SCORE_FOR_PRIORITY = 50.0  # nodes below this get deprioritised


@dataclass
class ReputationScore:
    did:             str
    score:           float = 0.0
    bandwidth_score: float = 0.0
    compute_score:   float = 0.0
    storage_score:   float = 0.0
    stake_score:     float = 0.0
    uptime_score:    float = 0.0
    peer_score:      float = 0.0
    penalty_score:   float = 0.0
    computed_at:     float = field(default_factory=time.time)

    @property
    def is_trusted(self) -> bool:
        return self.score >= MIN_SCORE_FOR_PRIORITY

    @property
    def tier(self) -> str:
        if self.score >= 500:  return "platinum"
        if self.score >= 200:  return "gold"
        if self.score >= 50:   return "silver"
        if self.score >= 10:   return "bronze"
        return "new"

    def to_dict(self) -> dict:
        return {
            "did":             self.did,
            "score":           round(self.score, 4),
            "tier":            self.tier,
            "is_trusted":      self.is_trusted,
            "breakdown": {
                "bandwidth":   round(self.bandwidth_score, 4),
                "compute":     round(self.compute_score, 4),
                "storage":     round(self.storage_score, 4),
                "stake":       round(self.stake_score, 4),
                "uptime":      round(self.uptime_score, 4),
                "peer_trust":  round(self.peer_score, 4),
                "penalties":   round(self.penalty_score, 4),
            },
            "computed_at": self.computed_at,
        }


class ReputationEngine:
    """
    Computes reputation scores for registered DIDs.

    Chain proof counts are injected from outside (passed as dicts)
    since the engine doesn't hold a chain reference directly.
    This keeps identity decoupled from consensus.
    """

    def __init__(self, registry: "IdentityRegistry"):
        self._registry = registry
        self._proof_counts: Dict[str, dict] = {}  # did → proof stats
        self._penalties:    Dict[str, int]  = {}  # did → penalty count

    # ── External proof injection ──────────────────────────
    def record_bandwidth_proof(self, did: str, bytes_forwarded: int) -> None:
        s = self._proof_counts.setdefault(did, _empty_stats())
        s["bandwidth_bytes"] += bytes_forwarded

    def record_compute_proof(self, did: str, compute_seconds: float) -> None:
        s = self._proof_counts.setdefault(did, _empty_stats())
        s["compute_seconds"] += compute_seconds

    def record_storage_proof(self, did: str, bytes_stored: int, duration_seconds: float) -> None:
        s = self._proof_counts.setdefault(did, _empty_stats())
        s["storage_byte_seconds"] += bytes_stored * duration_seconds

    def record_penalty(self, did: str, count: int = 1) -> None:
        self._penalties[did] = self._penalties.get(did, 0) + count

    # ── Score computation ─────────────────────────────────
    def score(self, did: str) -> ReputationScore:
        did_obj = self._registry.resolve(did)
        if did_obj is None:
            return ReputationScore(did=did, score=0.0)

        claims = self._registry.get_claims(did)
        stats  = self._proof_counts.get(did, _empty_stats())

        # Bandwidth (MB)
        mb = stats["bandwidth_bytes"] / (1024 * 1024)
        bw_score = mb * W_BANDWIDTH_MB

        # Compute (hours)
        hours = stats["compute_seconds"] / 3600
        cp_score = hours * W_COMPUTE_HOUR

        # Storage (GB-days)
        gb_days = stats["storage_byte_seconds"] / (1024**3 * 86400)
        st_score = gb_days * W_STORAGE_GB_DAY

        # Stake
        stake_bc = _latest_claim_value(claims, ClaimType.STAKE, "amount_bc", 0.0)
        stake_score = float(stake_bc) * W_STAKE_BC

        # Uptime (hours)
        uptime_hours = _latest_claim_value(claims, ClaimType.UPTIME, "hours", 0.0)
        up_score = float(uptime_hours) * W_UPTIME_HOUR

        # Peer trust — unique issuers vouching for this node
        peer_claims = self._registry.get_peer_trust(did)
        unique_peers = len({c.issuer_did for c in peer_claims})
        peer_score = unique_peers * W_PEER_TRUST

        # Penalties
        penalty_count = self._penalties.get(did, 0)
        pen_score = penalty_count * W_PENALTY

        total = bw_score + cp_score + st_score + stake_score + up_score + peer_score + pen_score
        total = max(0.0, total)

        return ReputationScore(
            did             = did,
            score           = total,
            bandwidth_score = bw_score,
            compute_score   = cp_score,
            storage_score   = st_score,
            stake_score     = stake_score,
            uptime_score    = up_score,
            peer_score      = peer_score,
            penalty_score   = pen_score,
        )

    def leaderboard(self, top_n: int = 10) -> List[ReputationScore]:
        scores = [self.score(did) for did in self._registry._dids]
        scores.sort(key=lambda s: s.score, reverse=True)
        return scores[:top_n]


# ── Helpers ───────────────────────────────────────────────
def _empty_stats() -> dict:
    return {"bandwidth_bytes": 0, "compute_seconds": 0.0, "storage_byte_seconds": 0.0}


def _latest_claim_value(claims: list, claim_type, key: str, default):
    relevant = [c for c in claims if c.claim_type == claim_type]
    if not relevant:
        return default
    latest = max(relevant, key=lambda c: c.issued_at)
    return latest.claim_data.get(key, default)
