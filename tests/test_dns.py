"""
Tests for border.dns — registry and record system
"""
import pytest

from border.dns.record import DNSRecord, RecordType, validate_name, REGISTRATION_FEE_BC, TRANSFER_FEE_BC
from border.dns.registry import DNSRegistry
from border.blockchain.wallet import BorderWallet


def _register(registry, name, wallet, fee=REGISTRATION_FEE_BC):
    record = DNSRecord.create(name=name, record_type=RecordType.ADDRESS,
                              value=wallet.address, owner_address=wallet.address)
    msg = f"register:{name}:{wallet.address}".encode()
    sig = wallet.sign(msg)
    return registry.register(record=record, fee_paid=fee,
                             owner_public_key=wallet.public_key_b64, owner_signature=sig)


class TestValidateName:
    def test_valid_name(self):
        ok, _ = validate_name("alice.border")
        assert ok

    def test_too_short(self):
        ok, _ = validate_name("ab")
        assert not ok

    def test_invalid_chars(self):
        ok, _ = validate_name("alice!.border")
        assert not ok

    def test_plain_label_accepted(self):
        ok, _ = validate_name("alice")
        assert ok


class TestDNSRecord:
    def test_create(self):
        r = DNSRecord.create("alice.border", RecordType.ADDRESS, "BC_" + "a" * 32, "BC_" + "a" * 32)
        assert r.name == "alice.border"

    def test_roundtrip(self):
        r = DNSRecord.create("bob.border", RecordType.ADDRESS, "BC_" + "b" * 32, "BC_" + "b" * 32)
        r2 = DNSRecord.from_dict(r.to_dict())
        assert r2.name == r.name and r2.value == r.value


class TestDNSRegistry:
    def test_register_new_name(self):
        reg = DNSRegistry()
        wallet = BorderWallet.create()
        ok, reason = _register(reg, "alice.border", wallet)
        assert ok, reason

    def test_register_requires_fee(self):
        reg = DNSRegistry()
        wallet = BorderWallet.create()
        ok, reason = _register(reg, "alice.border", wallet, fee=0.0)
        assert not ok
        assert "fee" in reason.lower()

    def test_duplicate_same_owner_allowed(self):
        reg = DNSRegistry()
        wallet = BorderWallet.create()
        _register(reg, "alice.border", wallet)
        ok, _ = _register(reg, "alice.border", wallet)
        assert ok

    def test_duplicate_different_owner_rejected(self):
        reg = DNSRegistry()
        w1, w2 = BorderWallet.create(), BorderWallet.create()
        _register(reg, "taken.border", w1)
        ok, reason = _register(reg, "taken.border", w2)
        assert not ok
        assert "already registered" in reason.lower()

    def test_invalid_signature_rejected(self):
        reg = DNSRegistry()
        wallet = BorderWallet.create()
        record = DNSRecord.create("bad.border", RecordType.ADDRESS, wallet.address, wallet.address)
        ok, _ = reg.register(record=record, fee_paid=REGISTRATION_FEE_BC,
                             owner_public_key=wallet.public_key_b64, owner_signature="invalidsig==")
        assert not ok

    def test_resolve_after_register(self):
        reg = DNSRegistry()
        wallet = BorderWallet.create()
        _register(reg, "resolve-me.border", wallet)
        records = reg.resolve("resolve-me.border", RecordType.ADDRESS)
        assert len(records) == 1
        assert records[0].value == wallet.address

    def test_resolve_unknown_returns_empty(self):
        reg = DNSRegistry()
        assert reg.resolve("nobody.border", RecordType.ADDRESS) == []

    def test_transfer_by_owner(self):
        reg = DNSRegistry()
        w1, w2 = BorderWallet.create(), BorderWallet.create()
        _register(reg, "transfer.border", w1)
        msg = f"transfer:transfer.border:{w1.address}:{w2.address}".encode()
        sig = w1.sign(msg)
        ok, reason = reg.transfer(
            name="transfer.border",
            from_address=w1.address,
            to_address=w2.address,
            fee_paid=TRANSFER_FEE_BC,
            from_public_key=w1.public_key_b64,
            from_signature=sig,
        )
        assert ok, reason

    def test_transfer_by_non_owner_rejected(self):
        reg = DNSRegistry()
        w1, w_attacker = BorderWallet.create(), BorderWallet.create()
        _register(reg, "mine.border", w1)
        msg = f"transfer:mine.border:{w_attacker.address}:{w_attacker.address}".encode()
        sig = w_attacker.sign(msg)
        ok, _ = reg.transfer(
            name="mine.border",
            from_address=w_attacker.address,
            to_address=w_attacker.address,
            fee_paid=TRANSFER_FEE_BC,
            from_public_key=w_attacker.public_key_b64,
            from_signature=sig,
        )
        assert not ok

    def test_resolve_address_helper(self):
        reg = DNSRegistry()
        wallet = BorderWallet.create()
        _register(reg, "addr.border", wallet)
        assert reg.resolve_address("addr.border") == wallet.address
