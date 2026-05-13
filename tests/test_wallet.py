"""Tests for BorderWallet — key generation, signing, verification, save/load."""
import os, tempfile
import pytest
from border.blockchain.wallet import BorderWallet
from border.blockchain.transaction import Transaction


class TestWalletCreation:
    def test_create_generates_address(self):
        w = BorderWallet.create()
        assert w.address.startswith("BC_")
        assert len(w.address) > 10

    def test_two_wallets_differ(self):
        w1 = BorderWallet.create()
        w2 = BorderWallet.create()
        assert w1.address != w2.address
        assert w1.public_key_b64 != w2.public_key_b64

    def test_public_key_is_string(self):
        w = BorderWallet.create()
        assert isinstance(w.public_key_b64, str)
        assert len(w.public_key_b64) > 20


class TestSignAndVerify:
    def test_sign_returns_string(self):
        w = BorderWallet.create()
        sig = w.sign(b"hello")
        assert isinstance(sig, str)

    def test_verify_own_signature(self):
        w = BorderWallet.create()
        data = b"test message"
        sig = w.sign(data)
        assert BorderWallet.verify(w.public_key_b64, data, sig) is True

    def test_verify_wrong_key_fails(self):
        w1 = BorderWallet.create()
        w2 = BorderWallet.create()
        sig = w1.sign(b"data")
        assert BorderWallet.verify(w2.public_key_b64, b"data", sig) is False

    def test_verify_tampered_data_fails(self):
        w = BorderWallet.create()
        sig = w.sign(b"original")
        assert BorderWallet.verify(w.public_key_b64, b"tampered", sig) is False

    def test_transaction_sign_verify(self):
        w = BorderWallet.create()
        tx = Transaction.create(w.address, "BC_other_000", 1.0, w.public_key_b64)
        tx.signature = w.sign(tx.signing_data())
        assert tx.verify() is True

    def test_transaction_tampered_fails(self):
        w = BorderWallet.create()
        tx = Transaction.create(w.address, "BC_other_000", 1.0, w.public_key_b64)
        tx.signature = w.sign(tx.signing_data())
        tx.amount = 999.0   # tamper
        assert tx.verify() is False


class TestWalletPersistence:
    def test_save_and_load(self):
        w = BorderWallet.create()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "wallet.json")
            w.save(path, password="test123")
            w2 = BorderWallet.load(path, password="test123")
            assert w2.address == w.address
            assert w2.public_key_b64 == w.public_key_b64

    def test_wrong_password_fails(self):
        w = BorderWallet.create()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "wallet.json")
            w.save(path, password="correct")
            with pytest.raises(Exception):
                BorderWallet.load(path, password="wrong")

    def test_signature_consistent_after_load(self):
        w = BorderWallet.create()
        data = b"signing test"
        sig_before = w.sign(data)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "wallet.json")
            w.save(path, password="pw")
            w2 = BorderWallet.load(path, password="pw")
            sig_after = w2.sign(data)
        # Both signatures should verify under the same public key
        assert BorderWallet.verify(w.public_key_b64, data, sig_before)
        assert BorderWallet.verify(w.public_key_b64, data, sig_after)
