"""Tests for BorderChain core — mining, validation, balance cache, double-spend."""
import time, uuid
import pytest
from tests.conftest import make_proof, make_funded_chain, make_tx
from border.blockchain.chain import BorderChain
from border.blockchain.wallet import BorderWallet
from border.blockchain.block import Block, BandwidthProof
from border.blockchain.transaction import Transaction
from border.blockchain.economics import MIN_DIFFICULTY


class TestGenesis:
    def test_genesis_is_first_block(self):
        chain = BorderChain()
        assert chain.height == 0
        assert chain.latest_block.index == 0

    def test_genesis_deterministic(self):
        """Two fresh chains must have identical genesis hashes."""
        c1 = BorderChain()
        c2 = BorderChain()
        assert c1.latest_block.block_hash == c2.latest_block.block_hash

    def test_genesis_chain_valid(self):
        chain = BorderChain()
        ok, msg = chain.validate_chain()
        assert ok, msg


class TestMining:
    def test_mine_single_block(self):
        chain, wallet = make_funded_chain()
        assert chain.height == 1
        assert chain.get_balance(wallet.address) > 0

    def test_mine_multiple_blocks(self):
        chain = BorderChain()
        wallet = BorderWallet.create()
        for _ in range(3):
            chain.add_proof(make_proof(mb=2))
            blk = chain.create_block(miner_address=wallet.address)
            assert blk is not None
            ok, _ = chain.add_block(blk)
            assert ok
        assert chain.height == 3

    def test_miner_receives_reward(self):
        chain, wallet = make_funded_chain()
        assert chain.get_balance(wallet.address) >= 50.0

    def test_block_hash_sealed(self):
        chain, _ = make_funded_chain()
        blk = chain.latest_block
        assert blk.block_hash == blk.compute_hash()

    def test_no_block_without_bandwidth(self):
        chain = BorderChain()
        wallet = BorderWallet.create()
        # No proofs added — should return None
        blk = chain.create_block(miner_address=wallet.address)
        assert blk is None

    def test_reject_tampered_block(self):
        chain, wallet = make_funded_chain()
        chain.add_proof(make_proof(mb=2))
        blk = chain.create_block(miner_address=wallet.address)
        blk.miner_address = "tampered_address"
        ok, reason = chain.add_block(blk)
        assert not ok
        assert "hash" in reason.lower() or "difficulty" in reason.lower()


class TestTransactions:
    def test_valid_tx_accepted(self):
        chain, alice = make_funded_chain()
        bob = BorderWallet.create()
        tx = make_tx(alice, bob.address, 1.0)
        assert chain.add_transaction(tx) is True

    def test_tx_below_fee_floor_rejected(self):
        chain, alice = make_funded_chain()
        bob = BorderWallet.create()
        tx = make_tx(alice, bob.address, 1.0, fee=0.0)
        assert chain.add_transaction(tx) is False

    def test_tx_insufficient_funds_rejected(self):
        chain = BorderChain()
        alice = BorderWallet.create()
        bob   = BorderWallet.create()
        tx = make_tx(alice, bob.address, 1000.0)
        assert chain.add_transaction(tx) is False

    def test_double_spend_blocked(self):
        chain, alice = make_funded_chain()
        bob = BorderWallet.create()
        balance = chain.get_balance(alice.address)
        tx1 = make_tx(alice, bob.address, balance - 0.001, fee=0.0001)
        tx2 = make_tx(alice, bob.address, balance - 0.001, fee=0.0001)
        r1 = chain.add_transaction(tx1)
        r2 = chain.add_transaction(tx2)
        assert r1 is True
        assert r2 is False  # blocked: would overdraft

    def test_tx_confirmed_in_block(self):
        chain, alice = make_funded_chain()
        bob = BorderWallet.create()
        tx = make_tx(alice, bob.address, 5.0)
        chain.add_transaction(tx)
        chain.add_proof(make_proof(mb=2))
        blk = chain.create_block(miner_address=alice.address)
        ok, _ = chain.add_block(blk)
        assert ok
        assert chain.get_balance(bob.address) == 5.0

    def test_mempool_cleared_after_confirmation(self):
        chain, alice = make_funded_chain()
        bob = BorderWallet.create()
        tx = make_tx(alice, bob.address, 1.0)
        chain.add_transaction(tx)
        assert len(chain._mempool) == 1
        chain.add_proof(make_proof(mb=2))
        blk = chain.create_block(miner_address=alice.address)
        chain.add_block(blk)
        assert len(chain._mempool) == 0


class TestBalanceCache:
    def test_initial_zero_balance(self):
        chain = BorderChain()
        assert chain.get_balance("nonexistent") == 0.0

    def test_mined_balance_cached(self):
        chain, wallet = make_funded_chain()
        b = chain.get_balance(wallet.address)
        assert b > 0
        # Call twice — must be same (O(1) cache, not recomputed)
        assert chain.get_balance(wallet.address) == b

    def test_balance_updates_after_send(self):
        chain, alice = make_funded_chain()
        bob = BorderWallet.create()
        bal_before = chain.get_balance(alice.address)
        tx = make_tx(alice, bob.address, 10.0)
        chain.add_transaction(tx)
        chain.add_proof(make_proof(mb=2))
        blk = chain.create_block(miner_address=alice.address)
        chain.add_block(blk)
        assert chain.get_balance(bob.address) == 10.0
        # Alice lost amount + fee (10.0 + 0.0001) but also gained new coinbase
        assert chain.get_balance(alice.address) > 0


class TestChainValidation:
    def test_multi_block_chain_valid(self):
        chain = BorderChain()
        wallet = BorderWallet.create()
        for _ in range(4):
            chain.add_proof(make_proof(mb=2))
            blk = chain.create_block(miner_address=wallet.address)
            chain.add_block(blk)
        ok, msg = chain.validate_chain()
        assert ok, msg

    def test_serialise_roundtrip(self):
        chain, wallet = make_funded_chain()
        d = chain.latest_block.to_dict()
        blk2 = Block.from_dict(d)
        assert blk2.block_hash == chain.latest_block.block_hash
        assert blk2.difficulty == chain.latest_block.difficulty


class TestDifficultyIntegration:
    def test_difficulty_stamped_on_block(self):
        chain, _ = make_funded_chain()
        blk = chain.latest_block
        assert blk.difficulty == MIN_DIFFICULTY

    def test_difficulty_carried_forward(self):
        chain = BorderChain()
        wallet = BorderWallet.create()
        for _ in range(3):
            chain.add_proof(make_proof(mb=2))
            blk = chain.create_block(miner_address=wallet.address)
            chain.add_block(blk)
        # No retarget yet — all blocks should share genesis difficulty
        for blk in chain._chain[1:]:
            assert blk.difficulty == MIN_DIFFICULTY

    def test_wrong_difficulty_rejected(self):
        chain, wallet = make_funded_chain()
        chain.add_proof(make_proof(mb=2))
        blk = chain.create_block(miner_address=wallet.address)
        # Tamper difficulty
        blk.difficulty = MIN_DIFFICULTY * 99
        blk.block_hash = blk.compute_hash()
        ok, reason = chain.add_block(blk)
        assert not ok
        assert "difficulty" in reason.lower()
