"""
BorderDAO — Community Governance for the Border Protocol.

BC holders vote. Protocol evolves. No corporation decides.

Proposal types: PARAMETER · TREASURY · PROTOCOL · SLASH · CUSTOM
Voting: stake-weighted, 7-day window, 10% quorum, simple majority

Usage:
    from border.dao import GovernanceEngine, Proposal, ProposalType, Vote, VoteChoice

    gov = GovernanceEngine()

    p = Proposal.create(
        proposal_type=ProposalType.PARAMETER,
        proposer_did="did:border:BC_...",
        title="Raise block reward to 2 BC",
        description="Incentivise more relay nodes",
        payload={"key": "block_reward", "value": 2.0},
    )
    gov.submit(p, proposer_balance=100.0)

    vote = Vote.create(p.proposal_id, voter_did, voter_address,
                       VoteChoice.YES, weight=500.0)
    vote.sign(wallet)
    gov.cast_vote(vote)

    gov.tally(p.proposal_id, total_supply=10000.0)
    gov.execute(p.proposal_id)
"""

from .proposal   import (Proposal, ProposalType, ProposalStatus,
                          VOTING_PERIOD, MIN_QUORUM_PCT, PASS_THRESHOLD_PCT,
                          MIN_PROPOSAL_STAKE)
from .vote       import Vote, VoteChoice
from .treasury   import BorderTreasury
from .governance import GovernanceEngine
from .node       import BorderDAONode, serve_dao

__all__ = [
    "Proposal", "ProposalType", "ProposalStatus",
    "VOTING_PERIOD", "MIN_QUORUM_PCT", "PASS_THRESHOLD_PCT", "MIN_PROPOSAL_STAKE",
    "Vote", "VoteChoice",
    "BorderTreasury",
    "GovernanceEngine",
    "BorderDAONode", "serve_dao",
]
