"""
Tests for border.identity — DID, verifiable claims, reputation
"""
import pytest

from border.blockchain.wallet import BorderWallet
from border.identity.did import BorderDID, ServiceType
from border.identity.claim import VerifiableClaim, ClaimType
from border.identity.registry import IdentityRegistry
from border.identity.reputation import ReputationEngine


class TestBorderDID:
    def test_did_format(self):
        wallet = BorderWallet.create()
        did = BorderDID.from_wallet(wallet)
        assert did.did.startswith("did:border:")
        assert wallet.address in did.did

    def test_did_document_contains_key(self):
        wallet = BorderWallet.create()
        did = BorderDID.from_wallet(wallet)
        doc = did.to_document()
        assert "verificationMethod" in doc or "publicKey" in doc or "public_key" in str(doc)

    def test_roundtrip(self):
        wallet = BorderWallet.create()
        did = BorderDID.from_wallet(wallet)
        did2 = BorderDID.from_dict(did.to_dict())
        assert did2.did == did.did
        assert did2.wallet_address == did.wallet_address

    def test_add_service_endpoint(self):
        wallet = BorderWallet.create()
        did = BorderDID.from_wallet(wallet)
        ep = did.add_service(ServiceType.RELAY, "http://relay.example.com:8080")
        assert ep is not None
        assert len(did.services) == 1

    def test_two_wallets_different_dids(self):
        w1, w2 = BorderWallet.create(), BorderWallet.create()
        assert BorderDID.from_wallet(w1).did != BorderDID.from_wallet(w2).did


class TestVerifiableClaim:
    def _make_claim(self, issuer_wallet=None, subject_did=None):
        issuer_wallet = issuer_wallet or BorderWallet.create()
        issuer_did = BorderDID.from_wallet(issuer_wallet)
        subject_did = subject_did or issuer_did.did
        claim = VerifiableClaim.create(
            issuer_did=issuer_did.did,
            subject_did=subject_did,
            claim_type=ClaimType.NODE_TYPE,
            claim_data={"node_type": "RELAY"},
        )
        return claim, issuer_wallet

    def test_claim_hash_deterministic(self):
        claim, _ = self._make_claim()
        assert claim.claim_hash() == claim.claim_hash()

    def test_sign_and_verify(self):
        claim, wallet = self._make_claim()
        claim.sign(wallet)
        assert claim.verify_signature(wallet.public_key_b64)

    def test_tampered_claim_fails_verification(self):
        claim, wallet = self._make_claim()
        claim.sign(wallet)
        claim.claim_data["node_type"] = "HACKER"
        assert not claim.verify_signature(wallet.public_key_b64)

    def test_wrong_key_fails(self):
        claim, wallet = self._make_claim()
        claim.sign(wallet)
        assert not claim.verify_signature(BorderWallet.create().public_key_b64)

    def test_unsigned_fails(self):
        claim, wallet = self._make_claim()
        assert not claim.verify_signature(wallet.public_key_b64)

    def test_roundtrip(self):
        claim, wallet = self._make_claim()
        claim.sign(wallet)
        c2 = VerifiableClaim.from_dict(claim.to_dict())
        assert c2.claim_id == claim.claim_id
        assert c2.signature == claim.signature

    def test_cross_attestation(self):
        issuer = BorderWallet.create()
        subject_did = BorderDID.from_wallet(BorderWallet.create()).did
        claim, _ = self._make_claim(issuer_wallet=issuer, subject_did=subject_did)
        claim.sign(issuer)
        assert claim.verify_signature(issuer.public_key_b64)
        assert claim.subject_did == subject_did


class TestIdentityRegistry:
    def test_register_and_lookup(self):
        reg = IdentityRegistry()
        wallet = BorderWallet.create()
        did = BorderDID.from_wallet(wallet)
        ok, _ = reg.register(did)
        assert ok
        found = reg.resolve(did.did)
        assert found is not None
        assert found.wallet_address == wallet.address

    def test_lookup_unknown_returns_none(self):
        reg = IdentityRegistry()
        assert reg.resolve("did:border:nobody") is None

    def test_add_claim(self):
        reg = IdentityRegistry()
        wallet = BorderWallet.create()
        did = BorderDID.from_wallet(wallet)
        reg.register(did)
        claim = VerifiableClaim.create(
            issuer_did=did.did, subject_did=did.did,
            claim_type=ClaimType.REGION, claim_data={"region": "US"},
        )
        claim.sign(wallet)
        ok, _ = reg.add_claim(claim)
        assert ok
        claims = reg.get_claims(did.did)
        assert len(claims) >= 1


class TestReputationEngine:
    def _make_reg_and_did(self):
        reg = IdentityRegistry()
        wallet = BorderWallet.create()
        did = BorderDID.from_wallet(wallet)
        reg.register(did)
        return reg, did, wallet

    def test_initial_score_is_zero(self):
        reg, did, _ = self._make_reg_and_did()
        engine = ReputationEngine(registry=reg)
        assert engine.score(did.did).score == 0.0

    def test_score_increases_after_bandwidth(self):
        reg, did, _ = self._make_reg_and_did()
        engine = ReputationEngine(registry=reg)
        engine.record_bandwidth_proof(did.did, bytes_forwarded=1024 * 1024 * 1024)
        assert engine.score(did.did).score > 0.0

    def test_score_increases_after_compute(self):
        reg, did, _ = self._make_reg_and_did()
        engine = ReputationEngine(registry=reg)
        engine.record_compute_proof(did.did, compute_seconds=3600.0)
        assert engine.score(did.did).score > 0.0
