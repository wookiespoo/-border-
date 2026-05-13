"""
Tests for BorderChain staking / slashing
"""
import pytest

from border.blockchain.chain import BorderChain
from border.blockchain.wallet import BorderWallet
from border.blockchain.block import BandwidthProof, ComputeProofRecord
import time


# ─────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────

def make_funded_chain(initial_bc=100.0):
    """Chain with a funded wallet ready for staking tests."""
    wallet = BorderWallet.create()
    chain = BorderChain()
    # Mine a block to give the wallet some BC
    proof = BandwidthProof(
        receipt_id="receipt_stake_test",
        relay_address=wallet.address,
        client_id="client_001",
        bytes_forwarded=200 * 1024 * 1024,  # 200 MB > MIN_DIFFICULTY
        timestamp=time.time(),
        session_id="sess_001",
        relay_signature="sig_001",
    )
    chain.add_proof(proof)
    block = chain.create_block(miner_address=wallet.address)
    chain.add_block(block)
    return chain, wallet


# ─────────────────────────────────────────────────────────
# stake()
# ─────────────────────────────────────────────────────────

class TestStake:
    def test_stake_reduces_available_balance(self):
        chain, wallet = make_funded_chain()
        balance_before = chain.get_balance(wallet.address)
        ok, reason = chain.stake(wallet.address, amount=5.0, role="compute")
        assert ok, reason
        # Staked amount is locked — get_staked reflects it
        assert chain.get_staked(wallet.address) == pytest.approx(5.0)
        # Confirmed balance unchanged; staked portion just becomes unavailable
        assert chain.get_balance(wallet.address) == pytest.approx(balance_before)

    def test_stake_below_minimum_rejected(self):
        chain, wallet = make_funded_chain()
        ok, reason = chain.stake(wallet.address, amount=0.001, role="infer")
        assert not ok
        assert "too low" in reason.lower()

    def test_stake_unknown_role_rejected(self):
        chain, wallet = make_funded_chain()
        ok, reason = chain.stake(wallet.address, amount=1.0, role="wizard")
        assert not ok
        assert "Unknown role" in reason

    def test_stake_topup_accumulates(self):
        chain, wallet = make_funded_chain()
        chain.stake(wallet.address, amount=2.0, role="storage")
        chain.stake(wallet.address, amount=3.0, role="storage")
        assert chain.get_staked(wallet.address) == pytest.approx(5.0)

    def test_stake_insufficient_balance_rejected(self):
        chain = BorderChain()
        wallet = BorderWallet.create()
        # Wallet has no BC
        ok, reason = chain.stake(wallet.address, amount=1.0, role="relay")
        assert not ok
        assert "Insufficient" in reason

    def test_stake_info_includes_role(self):
        chain, wallet = make_funded_chain()
        chain.stake(wallet.address, amount=2.0, role="storage")
        info = chain.get_stake_info(wallet.address)
        assert info is not None
        assert info["role"] == "storage"
        assert info["amount"] == pytest.approx(2.0)

    def test_has_minimum_stake_true(self):
        chain, wallet = make_funded_chain()
        chain.stake(wallet.address, amount=5.0, role="infer")
        assert chain.has_minimum_stake(wallet.address, "infer")

    def test_has_minimum_stake_false_unstaked(self):
        chain, wallet = make_funded_chain()
        assert not chain.has_minimum_stake(wallet.address, "compute")

    def test_total_staked_aggregates(self):
        chain, w1 = make_funded_chain()
        _, w2 = make_funded_chain()
        # Give w2 a balance by adding it to the same chain isn't straightforward;
        # just test w1 staking is reflected in total_staked
        chain.stake(w1.address, amount=3.0, role="relay")
        assert chain.total_staked == pytest.approx(3.0)


# ─────────────────────────────────────────────────────────
# unstake()
# ─────────────────────────────────────────────────────────

class TestUnstake:
    def test_unstake_removes_stake(self):
        chain, wallet = make_funded_chain()
        chain.stake(wallet.address, amount=2.0, role="relay")
        ok, reason = chain.unstake(wallet.address)
        assert ok, reason
        assert chain.get_staked(wallet.address) == 0.0

    def test_unstake_no_stake_fails(self):
        chain = BorderChain()
        wallet = BorderWallet.create()
        ok, reason = chain.unstake(wallet.address)
        assert not ok
        assert "No stake" in reason


# ─────────────────────────────────────────────────────────
# slash()
# ─────────────────────────────────────────────────────────

class TestSlash:
    def test_slash_reduces_stake(self):
        chain, wallet = make_funded_chain()
        chain.stake(wallet.address, amount=5.0, role="compute")
        ok, msg = chain.slash(wallet.address, amount=2.0, reason="missed_job")
        assert ok, msg
        assert chain.get_staked(wallet.address) == pytest.approx(3.0)

    def test_slash_full_stake_removes_entry(self):
        chain, wallet = make_funded_chain()
        chain.stake(wallet.address, amount=5.0, role="compute")
        ok, msg = chain.slash(wallet.address, amount=5.0, reason="fraud")
        assert ok
        assert chain.get_stake_info(wallet.address) is None

    def test_slash_capped_at_full_stake(self):
        chain, wallet = make_funded_chain()
        chain.stake(wallet.address, amount=3.0, role="relay")
        ok, msg = chain.slash(wallet.address, amount=999.0, reason="overkill")
        assert ok
        assert chain.get_staked(wallet.address) == 0.0  # capped, not negative

    def test_slash_no_stake_fails(self):
        chain = BorderChain()
        wallet = BorderWallet.create()
        ok, reason = chain.slash(wallet.address, amount=1.0, reason="test")
        assert not ok
        assert "no stake" in reason.lower()

    def test_slash_recorded_in_log(self):
        chain, wallet = make_funded_chain()
        chain.stake(wallet.address, amount=5.0, role="compute")
        chain.slash(wallet.address, amount=1.0, reason="bad_proof")
        log = chain.slash_log
        assert len(log) == 1
        assert log[0]["reason"] == "bad_proof"
        assert log[0]["amount"] == pytest.approx(1.0)

    def test_slash_log_accumulates(self):
        chain, wallet = make_funded_chain()
        chain.stake(wallet.address, amount=5.0, role="compute")
        chain.slash(wallet.address, amount=0.5, reason="miss_1")
        chain.slash(wallet.address, amount=0.5, reason="miss_2")
        assert len(chain.slash_log) == 2


# ─────────────────────────────────────────────────────────
# compute proof stake-gating
# ─────────────────────────────────────────────────────────

class TestComputeProofStakeGating:
    def _make_compute_proof(self, worker_address: str) -> ComputeProofRecord:
        return ComputeProofRecord(
            proof_id=f"cp_{worker_address[:8]}",
            job_id="job_001",
            worker_address=worker_address,
            client_address="BC_client_" + "c" * 32,
            compute_seconds=60.0,
            bytes_processed=1024 * 1024,
            input_hash="abc" * 20,
            output_hash="def" * 20,
            timestamp=time.time(),
            price_bc=0.1,
        )

    def test_proof_rejected_without_stake(self):
        chain = BorderChain()
        wallet = BorderWallet.create()
        proof = self._make_compute_proof(wallet.address)
        assert not chain.add_compute_proof(proof)

    def test_proof_accepted_with_sufficient_stake(self):
        chain, wallet = make_funded_chain()
        chain.stake(wallet.address, amount=chain.STAKE_MINIMUMS["compute"], role="compute")
        proof = self._make_compute_proof(wallet.address)
        assert chain.add_compute_proof(proof)

    def test_proof_rejected_after_full_slash(self):
        chain, wallet = make_funded_chain()
        chain.stake(wallet.address, amount=chain.STAKE_MINIMUMS["compute"], role="compute")
        chain.slash(wallet.address, amount=999.0, reason="fraud")  # wipe stake
        proof = self._make_compute_proof(wallet.address)
        assert not chain.add_compute_proof(proof)

    def test_stats_includes_staking_fields(self):
        chain, wallet = make_funded_chain()
        chain.stake(wallet.address, amount=2.0, role="relay")
        s = chain.stats
        assert "total_staked_bc" in s
        assert s["total_staked_bc"] == pytest.approx(2.0)
        assert "active_stakers" in s
        assert s["active_stakers"] == 1
