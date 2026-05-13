"""
Shared fixtures for Border test suite.

All tests run with MIN_BYTES_PER_BLOCK = MIN_DIFFICULTY = 0 so we can mine
blocks without real bandwidth proofs.
"""
import sys, os, time, uuid
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import border.blockchain.block as _bm
from border.blockchain.economics import MIN_DIFFICULTY
from border.blockchain.block import BandwidthProof
from border.blockchain.chain import BorderChain
from border.blockchain.wallet import BorderWallet
from border.blockchain.transaction import Transaction


def _patch_difficulty(monkeypatch):
    """Lower difficulty so empty blocks can be mined in tests."""
    monkeypatch.setattr(_bm, "MIN_BYTES_PER_BLOCK", 0)


def make_proof(mb: float = 2.0) -> BandwidthProof:
    """Create a properly signed BandwidthProof for testing."""
    w = BorderWallet.create()
    proof = BandwidthProof(
        receipt_id      = f"r_{uuid.uuid4().hex[:12]}",
        relay_address   = w.address,
        client_id       = "test_client",
        bytes_forwarded = int(mb * 1024 * 1024),
        timestamp       = time.time(),
        session_id      = f"s_{uuid.uuid4().hex[:12]}",
        relay_signature = "",
        relay_public_key= w.public_key_b64,
    )
    proof.relay_signature = w.sign(proof.hash().encode())
    return proof


def make_funded_chain() -> tuple[BorderChain, BorderWallet]:
    """Return a (chain, funded_wallet) pair with block #1 already mined."""
    chain = BorderChain()
    wallet = BorderWallet.create()
    chain.add_proof(make_proof(mb=2))
    blk = chain.create_block(miner_address=wallet.address)
    ok, _ = chain.add_block(blk)
    assert ok
    return chain, wallet


def make_tx(wallet: BorderWallet, to: str, amount: float, fee: float = 0.0001) -> Transaction:
    tx = Transaction.create(
        from_address = wallet.address,
        to_address   = to,
        amount       = amount,
        public_key   = wallet.public_key_b64,
        fee          = fee,
    )
    tx.signature = wallet.sign(tx.signing_data())
    return tx
