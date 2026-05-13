"""
Tests for border.payments — payment channels, receipts, manager.
"""
import pytest

from border.blockchain.wallet import BorderWallet
from border.payments import (
    PaymentChannel, ChannelState,
    PaymentReceipt,
    ChannelManager,
)


# ─────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────

def make_wallets():
    sender   = BorderWallet.create()
    receiver = BorderWallet.create()
    return sender, receiver


def make_manager_and_channel(deposit=10.0, ttl=0.0):
    """ChannelManager with no chain (pure off-chain tests)."""
    mgr = ChannelManager(chain=None)
    sender, receiver = make_wallets()
    ok, reason, ch = mgr.open_channel(
        sender_wallet    = sender,
        receiver_address = receiver.address,
        deposit_amount   = deposit,
        ttl_seconds      = ttl,
    )
    assert ok, reason
    return mgr, ch, sender, receiver


# ─────────────────────────────────────────────────────────
# PaymentChannel
# ─────────────────────────────────────────────────────────

class TestPaymentChannel:
    def test_create_generates_id(self):
        s, r = make_wallets()
        ch = PaymentChannel.create(s.address, r.address, deposited=5.0)
        assert len(ch.channel_id) == 32

    def test_initial_state_open(self):
        s, r = make_wallets()
        ch = PaymentChannel.create(s.address, r.address, deposited=5.0)
        assert ch.state == ChannelState.OPEN

    def test_balance_sender_full_when_no_payments(self):
        s, r = make_wallets()
        ch = PaymentChannel.create(s.address, r.address, deposited=5.0)
        assert ch.balance_sender == pytest.approx(5.0)
        assert ch.balance_receiver == pytest.approx(0.0)

    def test_balance_splits_after_payment(self):
        s, r = make_wallets()
        ch = PaymentChannel.create(s.address, r.address, deposited=10.0)
        ch.cumulative_paid = 3.0
        assert ch.balance_receiver == pytest.approx(3.0)
        assert ch.balance_sender   == pytest.approx(7.0)

    def test_is_active_open_channel(self):
        s, r = make_wallets()
        ch = PaymentChannel.create(s.address, r.address, deposited=5.0)
        assert ch.is_active

    def test_is_not_active_when_closed(self):
        s, r = make_wallets()
        ch = PaymentChannel.create(s.address, r.address, deposited=5.0)
        ch.state = ChannelState.CLOSED
        assert not ch.is_active

    def test_roundtrip(self):
        s, r = make_wallets()
        ch = PaymentChannel.create(s.address, r.address, deposited=7.5)
        ch2 = PaymentChannel.from_dict(ch.to_dict())
        assert ch2.channel_id == ch.channel_id
        assert ch2.deposited  == ch.deposited
        assert ch2.state      == ch.state


# ─────────────────────────────────────────────────────────
# PaymentReceipt
# ─────────────────────────────────────────────────────────

class TestPaymentReceipt:
    def _make(self, amount=1.0, nonce=1):
        s, r = make_wallets()
        receipt = PaymentReceipt.create(
            channel_id       = "chan_abc123",
            nonce            = nonce,
            amount           = amount,
            sender_address   = s.address,
            receiver_address = r.address,
            memo             = "1 GB relayed",
        )
        return receipt, s, r

    def test_create_has_receipt_id(self):
        receipt, _, _ = self._make()
        assert len(receipt.receipt_id) == 32

    def test_signing_bytes_deterministic(self):
        receipt, _, _ = self._make()
        assert receipt.signing_bytes() == receipt.signing_bytes()

    def test_sign_and_verify_sender(self):
        receipt, sender, _ = self._make()
        receipt.sign_as_sender(sender)
        assert receipt.verify_sender(sender.public_key_b64)

    def test_wrong_key_fails_sender_verify(self):
        receipt, sender, _ = self._make()
        receipt.sign_as_sender(sender)
        other = BorderWallet.create()
        assert not receipt.verify_sender(other.public_key_b64)

    def test_ack_and_verify_receiver(self):
        receipt, sender, receiver = self._make()
        receipt.sign_as_sender(sender)
        receipt.ack_as_receiver(receiver)
        assert receipt.verify_ack(receiver.public_key_b64)

    def test_unsigned_sender_fails(self):
        receipt, sender, _ = self._make()
        assert not receipt.verify_sender(sender.public_key_b64)

    def test_tampered_amount_fails(self):
        receipt, sender, _ = self._make(amount=1.0)
        receipt.sign_as_sender(sender)
        receipt.amount = 99.0   # tamper
        assert not receipt.verify_sender(sender.public_key_b64)

    def test_roundtrip(self):
        receipt, sender, receiver = self._make()
        receipt.sign_as_sender(sender)
        receipt.ack_as_receiver(receiver)
        r2 = PaymentReceipt.from_dict(receipt.to_dict())
        assert r2.receipt_id       == receipt.receipt_id
        assert r2.sender_signature == receipt.sender_signature
        assert r2.ack_signature    == receipt.ack_signature


# ─────────────────────────────────────────────────────────
# ChannelManager — open
# ─────────────────────────────────────────────────────────

class TestChannelManagerOpen:
    def test_open_creates_channel(self):
        mgr, ch, _, _ = make_manager_and_channel()
        assert ch is not None
        assert ch.state == ChannelState.OPEN

    def test_open_zero_deposit_rejected(self):
        mgr = ChannelManager(chain=None)
        sender, receiver = make_wallets()
        ok, reason, ch = mgr.open_channel(sender, receiver.address, deposit_amount=0.0)
        assert not ok
        assert ch is None

    def test_channel_retrievable(self):
        mgr, ch, _, _ = make_manager_and_channel()
        assert mgr.get_channel(ch.channel_id) is ch

    def test_channels_for_sender(self):
        mgr, ch, sender, _ = make_manager_and_channel()
        result = mgr.channels_for(sender.address)
        assert ch in result

    def test_channels_for_receiver(self):
        mgr, ch, _, receiver = make_manager_and_channel()
        result = mgr.channels_for(receiver.address)
        assert ch in result


# ─────────────────────────────────────────────────────────
# ChannelManager — send / receive
# ─────────────────────────────────────────────────────────

class TestChannelManagerSend:
    def test_send_returns_receipt(self):
        mgr, ch, sender, _ = make_manager_and_channel()
        ok, reason, receipt = mgr.send(ch.channel_id, 0.5, sender)
        assert ok, reason
        assert receipt is not None

    def test_send_increments_nonce(self):
        mgr, ch, sender, _ = make_manager_and_channel()
        mgr.send(ch.channel_id, 0.1, sender)
        mgr.send(ch.channel_id, 0.1, sender)
        assert ch.nonce == 2

    def test_send_updates_cumulative(self):
        mgr, ch, sender, _ = make_manager_and_channel()
        mgr.send(ch.channel_id, 1.0, sender)
        mgr.send(ch.channel_id, 2.0, sender)
        assert ch.cumulative_paid == pytest.approx(3.0)

    def test_send_beyond_deposit_rejected(self):
        mgr, ch, sender, _ = make_manager_and_channel(deposit=5.0)
        ok, reason, _ = mgr.send(ch.channel_id, 6.0, sender)
        assert not ok
        assert "exceed" in reason

    def test_send_zero_increment_rejected(self):
        mgr, ch, sender, _ = make_manager_and_channel()
        ok, reason, _ = mgr.send(ch.channel_id, 0.0, sender)
        assert not ok

    def test_send_wrong_wallet_rejected(self):
        mgr, ch, _, receiver = make_manager_and_channel()
        ok, reason, _ = mgr.send(ch.channel_id, 0.1, receiver)
        assert not ok
        assert "sender" in reason.lower()

    def test_receive_validates_and_acks(self):
        mgr, ch, sender, receiver = make_manager_and_channel()
        _, _, receipt = mgr.send(ch.channel_id, 1.0, sender)
        ok, reason = mgr.receive(receipt, receiver, sender.public_key_b64)
        assert ok, reason
        assert receipt.ack_signature != ""

    def test_receive_bad_signature_rejected(self):
        mgr, ch, sender, receiver = make_manager_and_channel()
        _, _, receipt = mgr.send(ch.channel_id, 1.0, sender)
        other = BorderWallet.create()
        ok, reason = mgr.receive(receipt, receiver, other.public_key_b64)
        assert not ok

    def test_receive_stale_nonce_rejected(self):
        mgr, ch, sender, receiver = make_manager_and_channel()
        _, _, r1 = mgr.send(ch.channel_id, 0.5, sender)
        mgr.receive(r1, receiver, sender.public_key_b64)
        # Force nonce backward
        r1.nonce = 0
        ok, reason = mgr.receive(r1, receiver, sender.public_key_b64)
        assert not ok

    def test_latest_receipt_tracks_highest(self):
        mgr, ch, sender, _ = make_manager_and_channel()
        mgr.send(ch.channel_id, 0.1, sender)
        _, _, r2 = mgr.send(ch.channel_id, 0.2, sender)
        assert mgr.latest_receipt(ch.channel_id) is r2

    def test_receipt_history_grows(self):
        mgr, ch, sender, _ = make_manager_and_channel()
        for _ in range(5):
            mgr.send(ch.channel_id, 0.1, sender)
        assert len(mgr.receipt_history(ch.channel_id)) == 5


# ─────────────────────────────────────────────────────────
# ChannelManager — close / settle
# ─────────────────────────────────────────────────────────

class TestChannelManagerClose:
    def test_close_marks_closing(self):
        mgr, ch, sender, _ = make_manager_and_channel()
        ok, reason = mgr.close(ch.channel_id, sender)
        assert ok, reason
        assert ch.state == ChannelState.CLOSING

    def test_close_by_receiver_allowed(self):
        mgr, ch, _, receiver = make_manager_and_channel()
        ok, reason = mgr.close(ch.channel_id, receiver)
        assert ok, reason

    def test_close_non_party_rejected(self):
        mgr, ch, _, _ = make_manager_and_channel()
        stranger = BorderWallet.create()
        ok, reason = mgr.close(ch.channel_id, stranger)
        assert not ok

    def test_double_close_rejected(self):
        mgr, ch, sender, _ = make_manager_and_channel()
        mgr.close(ch.channel_id, sender)
        ok, _ = mgr.close(ch.channel_id, sender)
        assert not ok

    def test_settle_closes_channel(self):
        mgr, ch, sender, _ = make_manager_and_channel()
        mgr.send(ch.channel_id, 2.0, sender)
        mgr.close(ch.channel_id, sender)
        ok, reason = mgr.settle(ch.channel_id, sender)
        assert ok, reason
        assert ch.state == ChannelState.CLOSED

    def test_settle_by_non_sender_rejected(self):
        mgr, ch, _, receiver = make_manager_and_channel()
        mgr.close(ch.channel_id, receiver)
        ok, reason = mgr.settle(ch.channel_id, receiver)
        assert not ok
        assert "sender" in reason.lower()

    def test_stats_structure(self):
        mgr, ch, sender, _ = make_manager_and_channel(deposit=10.0)
        s = mgr.stats
        assert "total_channels"  in s
        assert "open_channels"   in s
        assert "total_deposited" in s
        assert s["open_channels"] == 1
        assert s["total_deposited"] == pytest.approx(10.0)
