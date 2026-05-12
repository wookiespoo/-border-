"""
BorderDAO — Governance Engine

Manages the full proposal lifecycle:
  submit → activate → vote → tally → execute / expire

Voting power = BC balance at time of vote (snapshot from chain).
"""

from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional, Tuple

from .proposal import (Proposal, ProposalType, ProposalStatus,
                       MIN_PROPOSAL_STAKE, PASS_THRESHOLD_PCT, MIN_QUORUM_PCT)
from .vote     import Vote, VoteChoice
from .treasury import BorderTreasury

logger = logging.getLogger("border.dao.governance")


class GovernanceEngine:
    def __init__(self, treasury: Optional[BorderTreasury] = None):
        self.treasury   = treasury or BorderTreasury()
        self._proposals: Dict[str, Proposal] = {}
        self._votes:     Dict[str, Dict[str, Vote]] = {}  # proposal_id → {voter_address → Vote}

        # Live protocol parameters (modified by PASSED PARAMETER proposals)
        self.parameters: Dict[str, float] = {
            "min_bytes_per_block":   100 * 1024 * 1024,
            "block_reward":          1.0,
            "bc_per_gb":             1.0,
            "bc_per_compute_hour":   2.0,
            "bc_per_gb_per_day":     0.01,
            "min_stake_to_work":     5.0,
            "min_stake_to_store":    2.0,
            "min_stake_to_infer":    5.0,
            "min_stake_to_render":   10.0,
            "tx_fee_pct":            0.01,
        }

    # ── Submit ────────────────────────────────────────────
    def submit(self, proposal: Proposal,
               proposer_balance: float) -> Tuple[bool, str]:
        if proposer_balance < MIN_PROPOSAL_STAKE:
            return False, f"Insufficient stake: need {MIN_PROPOSAL_STAKE} BC to submit"
        if proposal.proposal_id in self._proposals:
            return False, "Proposal already exists"

        proposal.status = ProposalStatus.ACTIVE
        self._proposals[proposal.proposal_id] = proposal
        self._votes[proposal.proposal_id] = {}

        logger.info(f"[DAO] Proposal submitted: [{proposal.proposal_type}] {proposal.title[:50]}")
        return True, "submitted"

    # ── Vote ──────────────────────────────────────────────
    def cast_vote(self, vote: Vote) -> Tuple[bool, str]:
        proposal = self._proposals.get(vote.proposal_id)
        if proposal is None:
            return False, "Proposal not found"
        if proposal.status != ProposalStatus.ACTIVE:
            return False, f"Voting closed — proposal is {proposal.status}"
        if proposal.is_expired:
            self._finalise(proposal)
            return False, "Voting period has ended"
        if vote.weight <= 0:
            return False, "Zero voting weight — need BC balance"

        existing = self._votes[vote.proposal_id].get(vote.voter_address)
        if existing:
            # Revoke old vote
            self._apply_weight(proposal, existing.choice, -existing.weight)
            logger.info(f"[DAO] Vote changed by {vote.voter_address[:16]}...")

        self._votes[vote.proposal_id][vote.voter_address] = vote
        self._apply_weight(proposal, vote.choice, vote.weight)

        logger.info(f"[DAO] Vote cast: {vote.choice} w={vote.weight:.4f} BC | "
                    f"proposal={proposal.title[:30]}")
        return True, "vote_cast"

    def _apply_weight(self, proposal: Proposal,
                      choice: VoteChoice, weight: float) -> None:
        if choice == VoteChoice.YES:
            proposal.votes_yes     += weight
        elif choice == VoteChoice.NO:
            proposal.votes_no      += weight
        else:
            proposal.votes_abstain += weight

    # ── Tally / Execute ───────────────────────────────────
    def tally(self, proposal_id: str,
              total_supply: float) -> Tuple[ProposalStatus, str]:
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            return ProposalStatus.DRAFT, "not_found"

        if proposal.status not in (ProposalStatus.ACTIVE,):
            return proposal.status, "already_finalised"

        self._finalise(proposal, total_supply)
        return proposal.status, proposal.status

    def _finalise(self, proposal: Proposal,
                  total_supply: float = 0.0) -> None:
        if not proposal.quorum_met(total_supply):
            proposal.status = ProposalStatus.REJECTED
            logger.info(f"[DAO] Proposal REJECTED (no quorum): {proposal.title[:40]}")
            return

        if proposal.would_pass(total_supply):
            proposal.status = ProposalStatus.PASSED
            logger.info(f"[DAO] Proposal PASSED: {proposal.title[:40]} "
                        f"({proposal.yes_pct*100:.1f}% yes, "
                        f"{proposal.total_votes:.2f} BC voted)")
        else:
            proposal.status = ProposalStatus.REJECTED
            logger.info(f"[DAO] Proposal REJECTED: {proposal.title[:40]} "
                        f"({proposal.yes_pct*100:.1f}% yes)")

    def execute(self, proposal_id: str) -> Tuple[bool, str]:
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            return False, "not_found"
        if proposal.status != ProposalStatus.PASSED:
            return False, f"Cannot execute — status={proposal.status}"

        result = self._execute_payload(proposal)
        if result[0]:
            proposal.status     = ProposalStatus.EXECUTED
            proposal.executed_at = time.time()
        return result

    def _execute_payload(self, proposal: Proposal) -> Tuple[bool, str]:
        ptype   = proposal.proposal_type
        payload = proposal.payload

        if ptype == ProposalType.PARAMETER:
            key = payload.get("key")
            val = payload.get("value")
            if key and val is not None:
                self.parameters[key] = val
                self.treasury.apply_parameter(key, val)
                logger.info(f"[DAO] Parameter set: {key} = {val}")
                return True, f"parameter_updated:{key}={val}"
            return False, "invalid_parameter_payload"

        if ptype == ProposalType.TREASURY:
            recipient = payload.get("recipient")
            amount    = payload.get("amount_bc", 0.0)
            note      = payload.get("note", "")
            if recipient and amount > 0:
                ok, msg = self.treasury.spend(proposal.proposal_id, recipient, amount, note)
                return ok, msg
            return False, "invalid_treasury_payload"

        if ptype == ProposalType.PROTOCOL:
            flag = payload.get("feature_flag")
            if flag:
                self.parameters[f"feature_{flag}"] = 1.0
                logger.info(f"[DAO] Feature enabled: {flag}")
                return True, f"feature_enabled:{flag}"
            return False, "invalid_protocol_payload"

        if ptype == ProposalType.SLASH:
            target  = payload.get("target_address")
            amount  = payload.get("slash_amount_bc", 0.0)
            logger.info(f"[DAO] Slash executed: {target[:20]}... -{amount} BC")
            return True, f"slashed:{target}:{amount}"

        # CUSTOM — log and mark executed
        logger.info(f"[DAO] Custom proposal executed: {proposal.title}")
        return True, "custom_executed"

    # ── Queries ───────────────────────────────────────────
    def get_proposal(self, proposal_id: str) -> Optional[Proposal]:
        return self._proposals.get(proposal_id)

    def active_proposals(self) -> List[Proposal]:
        return [p for p in self._proposals.values()
                if p.status == ProposalStatus.ACTIVE and not p.is_expired]

    def all_proposals(self) -> List[Proposal]:
        return list(self._proposals.values())

    def get_votes(self, proposal_id: str) -> List[Vote]:
        return list(self._votes.get(proposal_id, {}).values())

    def voter_history(self, voter_address: str) -> List[Vote]:
        result = []
        for votes in self._votes.values():
            if voter_address in votes:
                result.append(votes[voter_address])
        return result

    # ── Stats ─────────────────────────────────────────────
    @property
    def stats(self) -> dict:
        by_status: Dict[str, int] = {}
        for p in self._proposals.values():
            by_status[p.status] = by_status.get(p.status, 0) + 1
        return {
            "total_proposals":  len(self._proposals),
            "by_status":        by_status,
            "active":           len(self.active_proposals()),
            "parameters":       self.parameters,
            "treasury":         self.treasury.stats,
        }
