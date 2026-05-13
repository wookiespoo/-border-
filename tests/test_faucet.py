"""
Tests for border.faucet — testnet BC faucet.
"""
import pytest
import time

from border.blockchain.chain import BorderChain
from border.blockchain.wallet import BorderWallet
from border.faucet import Faucet, DRIP_AMOUNT_BC, COOLDOWN_IP_SEC, COOLDOWN_ADDR_SEC


def _funded_faucet():
    """Return a Faucet backed by a chain where the faucet wallet has mined BC."""
    from tests.conftest import make_proof
    faucet_wallet = BorderWallet.create()
    chain = BorderChain()
    # Mine a block so faucet_wallet has coins
    chain.add_proof(make_proof(mb=2))
    block = chain.create_block(miner_address=faucet_wallet.address)
    ok, msg = chain.add_block(block)
    assert ok, msg
    return Faucet(chain=chain, wallet=faucet_wallet), chain, faucet_wallet


class TestFaucetDrip:
    def test_drip_sends_bc(self):
        faucet, chain, _ = _funded_faucet()
        recipient = BorderWallet.create()
        ok, msg = faucet.drip(recipient.address, "1.2.3.4")
        assert ok, msg
        # BC is in mempool — not yet confirmed, but drip succeeded
        assert "Sent" in msg

    def test_drip_bad_address_rejected(self):
        faucet, _, _ = _funded_faucet()
        ok, msg = faucet.drip("not_an_address", "1.2.3.4")
        assert not ok
        assert "Invalid" in msg

    def test_drip_ip_cooldown(self):
        faucet, chain, _ = _funded_faucet()
        r1 = BorderWallet.create()
        r2 = BorderWallet.create()
        ok1, _ = faucet.drip(r1.address, "10.0.0.1")
        assert ok1
        # Same IP, different address — still blocked
        ok2, msg2 = faucet.drip(r2.address, "10.0.0.1")
        assert not ok2
        assert "rate-limited" in msg2.lower()

    def test_drip_addr_cooldown(self):
        faucet, chain, _ = _funded_faucet()
        recipient = BorderWallet.create()
        ok1, _ = faucet.drip(recipient.address, "10.0.0.1")
        assert ok1
        # Same address, different IP — still blocked
        ok2, msg2 = faucet.drip(recipient.address, "10.0.0.2")
        assert not ok2
        assert "rate-limited" in msg2.lower()

    def test_drip_different_ip_and_addr_allowed(self):
        faucet, chain, _ = _funded_faucet()
        r1 = BorderWallet.create()
        r2 = BorderWallet.create()
        ok1, _ = faucet.drip(r1.address, "10.0.0.1")
        ok2, _ = faucet.drip(r2.address, "10.0.0.2")
        assert ok1
        assert ok2

    def test_history_recorded(self):
        faucet, chain, _ = _funded_faucet()
        recipient = BorderWallet.create()
        faucet.drip(recipient.address, "1.2.3.4")
        h = faucet.history()
        assert len(h) == 1
        assert h[0]["address"] == recipient.address
        assert h[0]["amount_bc"] == DRIP_AMOUNT_BC

    def test_info_keys_present(self):
        faucet, _, _ = _funded_faucet()
        info = faucet.info()
        assert "drip_amount_bc"     in info
        assert "faucet_balance_bc"  in info
        assert "faucet_address"     in info
        assert "chain_height"       in info
        assert info["drip_amount_bc"] == DRIP_AMOUNT_BC

    def test_empty_faucet_rejected(self):
        # Faucet wallet with no balance
        chain = BorderChain()
        empty_wallet = BorderWallet.create()
        faucet = Faucet(chain=chain, wallet=empty_wallet)
        recipient = BorderWallet.create()
        ok, msg = faucet.drip(recipient.address, "1.2.3.4")
        assert not ok
        assert "empty" in msg.lower()
