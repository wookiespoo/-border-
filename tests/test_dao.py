"""
Tests for border.dao — governance, proposals, votes, treasury
"""
import pytest

from border.blockchain.wallet import BorderWallet
from border.identity.did import BorderDID
from border.dao.proposal import (
    Proposal, ProposalType, ProposalStatus, MIN_PROPOSAL_STAKE,
)
from border.dao.vote import Vote, VoteChoice
from border.dao.treasury import BorderTreasury
from border.dao.governance import GovernanceEngine


def make_wallet_and_did():
    wallet = BorderWallet.create()
    did = BorderDID.from_wallet(wallet)
    return wallet, did


def make_proposal(proposer_did="did:border:proposer"):
    return Proposal.create(
        proposal_type=ProposalType.PARAMETER,
        proposer_did=proposer_did,
        title="Lower minimum stake",
        description="Reduce min_stake_to_work",
        payload={"key": "min_stake_to_work", "value": 2.0},
    )


def make_vote(proposal_id, wallet, did, choice=VoteChoice.YES, weight=10.0):
    vote = Vote.create(
        proposal_id=proposal_id,
        voter_did=did.did,
        voter_address=wallet.address,
        choice=choice,
        weight=weight,
    )
    vote.sign(wallet)
    return vote


class TestProposal:
    def test_create_generates_id(self):
        p = make_proposal()
        assert len(p.proposal_id) > 0

    def test_initial_status_active(self):
        # Proposals created via Proposal.create() start as DRAFT or ACTIVE
        p = make_proposal()
        assert p.status in (ProposalStatus.DRAFT, ProposalStatus.ACTIVE)

    def test_not_expired_when_fresh(self):
        assert not make_proposal().is_expired

    def test_quorum_not_met_with_no_votes(self):
        p = make_proposal()
        assert not p.quorum_met(total_supply=1000.0)

    def test_quorum_met_with_enough_votes(self):
        p = make_proposal()
        p.votes_yes = 200.0
        assert p.quorum_met(total_supply=1000.0)

    def test_is_passing_with_majority(self):
        p = make_proposal()
        p.votes_yes = 200.0
        p.votes_no = 50.0
        assert p.would_pass(total_supply=1000.0)

    def test_not_passing_without_majority(self):
        p = make_proposal()
        p.votes_yes = 50.0
        p.votes_no = 200.0
        assert not p.would_pass(total_supply=1000.0)


class TestVote:
    def test_hash_deterministic(self):
        wallet, did = make_wallet_and_did()
        vote = make_vote("prop_001", wallet, did)
        assert vote.hash() == vote.hash()

    def test_sign_and_verify(self):
        wallet, did = make_wallet_and_did()
        vote = make_vote("prop_001", wallet, did)
        assert vote.verify_signature(wallet.public_key_b64)

    def test_wrong_key_fails(self):
        wallet, did = make_wallet_and_did()
        other = BorderWallet.create()
        vote = make_vote("prop_001", wallet, did)
        assert not vote.verify_signature(other.public_key_b64)

    def test_roundtrip(self):
        wallet, did = make_wallet_and_did()
        vote = make_vote("prop_001", wallet, did)
        v2 = Vote.from_dict(vote.to_dict())
        assert v2.vote_id == vote.vote_id
        assert v2.signature == vote.signature


class TestTreasury:
    def test_initial_balance_zero(self):
        assert BorderTreasury().balance == 0.0

    def test_collect_increases_balance(self):
        t = BorderTreasury()
        t.collect("tx_fee", 5.0, from_address="BC_" + "a" * 32)
        assert t.balance == 5.0

    def test_collect_tx_fee_applies_pct(self):
        t = BorderTreasury()
        collected = t.collect_tx_fee(tx_amount=100.0, from_address="BC_" + "a" * 32)
        assert collected == pytest.approx(1.0)

    def test_spend_reduces_balance(self):
        t = BorderTreasury()
        t.collect("tx_fee", 10.0, from_address="BC_" + "a" * 32)
        ok, _ = t.spend("prop_001", "BC_" + "b" * 32, 3.0)
        assert ok
        assert t.balance == pytest.approx(7.0)

    def test_spend_beyond_balance_rejected(self):
        t = BorderTreasury()
        t.collect("tx_fee", 1.0, from_address="BC_" + "a" * 32)
        ok, reason = t.spend("prop_001", "BC_" + "b" * 32, 999.0)
        assert not ok
        assert "Insufficient" in reason

    def test_stats_includes_balance(self):
        t = BorderTreasury()
        t.collect("compute", 5.0, from_address="BC_" + "a" * 32)
        s = t.stats
        assert s["balance"] == pytest.approx(5.0)

    def test_apply_parameter(self):
        t = BorderTreasury()
        t.apply_parameter("tx_fee_pct", 0.02)
        assert t.tx_fee_pct == pytest.approx(0.02)


class TestGovernanceEngine:
    def test_submit_with_sufficient_stake(self):
        gov = GovernanceEngine()
        wallet, did = make_wallet_and_did()
        p = make_proposal(proposer_did=did.did)
        ok, reason = gov.submit(p, proposer_balance=MIN_PROPOSAL_STAKE + 1)
        assert ok, reason

    def test_submit_insufficient_stake_rejected(self):
        gov = GovernanceEngine()
        p = make_proposal()
        ok, reason = gov.submit(p, proposer_balance=0.0)
        assert not ok
        assert "Insufficient" in reason

    def test_duplicate_proposal_rejected(self):
        gov = GovernanceEngine()
        p = make_proposal()
        gov.submit(p, proposer_balance=MIN_PROPOSAL_STAKE + 1)
        ok, reason = gov.submit(p, proposer_balance=MIN_PROPOSAL_STAKE + 1)
        assert not ok

    def test_cast_vote_signed(self):
        gov = GovernanceEngine()
        wallet, did = make_wallet_and_did()
        p = make_proposal(proposer_did=did.did)
        gov.submit(p, proposer_balance=MIN_PROPOSAL_STAKE + 1)
        vote = make_vote(p.proposal_id, wallet, did, weight=50.0)
        ok, reason = gov.cast_vote(vote, voter_public_key=wallet.public_key_b64)
        assert ok, reason

    def test_cast_vote_bad_signature_rejected(self):
        gov = GovernanceEngine()
        wallet, did = make_wallet_and_did()
        other = BorderWallet.create()
        p = make_proposal(proposer_did=did.did)
        gov.submit(p, proposer_balance=MIN_PROPOSAL_STAKE + 1)
        vote = make_vote(p.proposal_id, wallet, did, weight=50.0)
        ok, reason = gov.cast_vote(vote, voter_public_key=other.public_key_b64)
        assert not ok
        assert "signature" in reason.lower()

    def test_tally_passes_with_majority(self):
        gov = GovernanceEngine()
        wallet, did = make_wallet_and_did()
        p = make_proposal(proposer_did=did.did)
        gov.submit(p, proposer_balance=MIN_PROPOSAL_STAKE + 1)
        vote = make_vote(p.proposal_id, wallet, did, choice=VoteChoice.YES, weight=500.0)
        gov.cast_vote(vote, voter_public_key=wallet.public_key_b64)
        status, result = gov.tally(p.proposal_id, total_supply=1000.0)
        assert status == ProposalStatus.PASSED

    def test_tally_fails_without_quorum(self):
        gov = GovernanceEngine()
        wallet, did = make_wallet_and_did()
        p = make_proposal(proposer_did=did.did)
        gov.submit(p, proposer_balance=MIN_PROPOSAL_STAKE + 1)
        vote = make_vote(p.proposal_id, wallet, did, choice=VoteChoice.YES, weight=0.001)
        gov.cast_vote(vote, voter_public_key=wallet.public_key_b64)
        status, result = gov.tally(p.proposal_id, total_supply=1_000_000.0)
        assert status in (ProposalStatus.REJECTED, ProposalStatus.EXPIRED)

    def test_parameter_updated_after_execution(self):
        gov = GovernanceEngine()
        wallet, did = make_wallet_and_did()
        # Payload uses {"key": ..., "value": ...} format
        p = Proposal.create(
            proposal_type=ProposalType.PARAMETER,
            proposer_did=did.did,
            title="Bump compute reward",
            description="Raise bc_per_compute_hour",
            payload={"key": "bc_per_compute_hour", "value": 5.0},
        )
        gov.submit(p, proposer_balance=MIN_PROPOSAL_STAKE + 1)
        vote = make_vote(p.proposal_id, wallet, did, choice=VoteChoice.YES, weight=500.0)
        gov.cast_vote(vote, voter_public_key=wallet.public_key_b64)
        gov.tally(p.proposal_id, total_supply=1000.0)
        gov.execute(p.proposal_id)
        assert gov.parameters["bc_per_compute_hour"] == pytest.approx(5.0)
