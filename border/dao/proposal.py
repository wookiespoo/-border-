"""
BorderDAO — Proposals

A proposal is a change request submitted by any BC holder.
Types: PARAMETER change, TREASURY spend, PROTOCOL upgrade, CUSTOM.

Lifecycle: DRAFT → ACTIVE → PASSED / REJECTED → EXECUTED / EXPIRED
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class ProposalType(str, Enum):
    PARAMETER  = "parameter"   # change a protocol constant (fees, thresholds)
    TREASURY   = "treasury"    # spend from the DAO treasury
    PROTOCOL   = "protocol"    # upgrade / flag a new feature
    SLASH      = "slash"       # slash a misbehaving node's stake
    CUSTOM     = "custom"      # arbitrary governance action


class ProposalStatus(str, Enum):
    DRAFT    = "draft"
    ACTIVE   = "active"
    PASSED   = "passed"
    REJECTED = "rejected"
    EXECUTED = "executed"
    EXPIRED  = "expired"


# Voting periods (seconds)
VOTING_PERIOD      = 7 * 24 * 3600   # 7 days
MIN_QUORUM_PCT     = 0.10             # 10% of total supply must vote
PASS_THRESHOLD_PCT = 0.51             # simple majority
MIN_PROPOSAL_STAKE = 10.0             # BC needed to submit a proposal


@dataclass
class Proposal:
    proposal_id:     str
    proposal_type:   ProposalType
    proposer_did:    str             # BorderID DID of proposer
    title:           str
    description:     str
    payload:         Dict[str, Any]  # what changes if passed
    created_at:      float
    voting_ends_at:  float
    status:          ProposalStatus  = ProposalStatus.DRAFT
    executed_at:     Optional[float] = None
    execution_tx:    Optional[str]   = None

    # Tally (updated live)
    votes_yes:       float = 0.0
    votes_no:        float = 0.0
    votes_abstain:   float = 0.0

    @property
    def total_votes(self) -> float:
        return self.votes_yes + self.votes_no + self.votes_abstain

    @property
    def yes_pct(self) -> float:
        active = self.votes_yes + self.votes_no
        return self.votes_yes / active if active > 0 else 0.0

    @property
    def is_expired(self) -> bool:
        return time.time() > self.voting_ends_at

    def quorum_met(self, total_supply: float) -> bool:
        if total_supply <= 0:
            return False
        return self.total_votes / total_supply >= MIN_QUORUM_PCT

    def would_pass(self, total_supply: float) -> bool:
        return (self.quorum_met(total_supply)
                and self.yes_pct >= PASS_THRESHOLD_PCT)

    def hash(self) -> str:
        content = {
            "proposal_id":   self.proposal_id,
            "proposal_type": self.proposal_type,
            "proposer_did":  self.proposer_did,
            "title":         self.title,
            "payload":       self.payload,
            "created_at":    self.created_at,
        }
        return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()

    def to_dict(self) -> dict:
        return {
            "proposal_id":    self.proposal_id,
            "proposal_type":  self.proposal_type,
            "proposer_did":   self.proposer_did,
            "title":          self.title,
            "description":    self.description,
            "payload":        self.payload,
            "created_at":     self.created_at,
            "voting_ends_at": self.voting_ends_at,
            "status":         self.status,
            "executed_at":    self.executed_at,
            "execution_tx":   self.execution_tx,
            "votes_yes":      round(self.votes_yes, 8),
            "votes_no":       round(self.votes_no, 8),
            "votes_abstain":  round(self.votes_abstain, 8),
            "total_votes":    round(self.total_votes, 8),
            "yes_pct":        round(self.yes_pct, 4),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Proposal":
        p = cls(
            proposal_id    = d["proposal_id"],
            proposal_type  = ProposalType(d["proposal_type"]),
            proposer_did   = d["proposer_did"],
            title          = d["title"],
            description    = d.get("description", ""),
            payload        = d.get("payload", {}),
            created_at     = d["created_at"],
            voting_ends_at = d["voting_ends_at"],
            status         = ProposalStatus(d.get("status", "draft")),
            executed_at    = d.get("executed_at"),
            execution_tx   = d.get("execution_tx"),
            votes_yes      = d.get("votes_yes", 0.0),
            votes_no       = d.get("votes_no", 0.0),
            votes_abstain  = d.get("votes_abstain", 0.0),
        )
        return p

    @classmethod
    def create(cls, proposal_type: ProposalType, proposer_did: str,
               title: str, description: str, payload: Dict[str, Any],
               voting_period: float = VOTING_PERIOD) -> "Proposal":
        now = time.time()
        return cls(
            proposal_id    = uuid.uuid4().hex,
            proposal_type  = proposal_type,
            proposer_did   = proposer_did,
            title          = title,
            description    = description,
            payload        = payload,
            created_at     = now,
            voting_ends_at = now + voting_period,
        )

    def __repr__(self) -> str:
        return (f"<Proposal [{self.status}] {self.title[:40]} "
                f"yes={self.votes_yes:.1f} no={self.votes_no:.1f}>")
