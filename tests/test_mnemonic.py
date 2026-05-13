"""Tests for BIP-39 mnemonic seed phrase generation and wallet recovery."""
import pytest
from border.blockchain.mnemonic import (
    generate_mnemonic, validate_mnemonic, mnemonic_to_seed,
    seed_to_private_key_bytes, mnemonic_to_wallet,
    PBKDF2_ITERATIONS,
)
from border.blockchain.wallet import BorderWallet


class TestGenerate:
    def test_12_words(self):
        m = generate_mnemonic(128)
        assert len(m.split()) == 12

    def test_24_words(self):
        m = generate_mnemonic(256)
        assert len(m.split()) == 24

    def test_invalid_strength_raises(self):
        with pytest.raises(ValueError):
            generate_mnemonic(strength=64)

    def test_two_mnemonics_differ(self):
        assert generate_mnemonic() != generate_mnemonic()

    def test_generated_is_valid(self):
        for _ in range(5):
            m = generate_mnemonic()
            assert validate_mnemonic(m)


class TestValidate:
    def test_valid_12_word(self):
        m = generate_mnemonic(128)
        assert validate_mnemonic(m) is True

    def test_valid_24_word(self):
        m = generate_mnemonic(256)
        assert validate_mnemonic(m) is True

    def test_garbage_invalid(self):
        assert validate_mnemonic("foo bar baz") is False

    def test_wrong_word_count_invalid(self):
        assert validate_mnemonic("word " * 5) is False

    def test_real_but_wrong_checksum(self):
        # Swap last two words — breaks checksum
        m = generate_mnemonic(128)
        words = m.split()
        words[-1], words[-2] = words[-2], words[-1]
        assert validate_mnemonic(" ".join(words)) is False


class TestMnemonicToSeed:
    def test_seed_is_64_bytes(self):
        m = generate_mnemonic()
        assert len(mnemonic_to_seed(m)) == 64

    def test_deterministic(self):
        m = generate_mnemonic()
        assert mnemonic_to_seed(m) == mnemonic_to_seed(m)

    def test_passphrase_changes_seed(self):
        m = generate_mnemonic()
        s1 = mnemonic_to_seed(m, passphrase="")
        s2 = mnemonic_to_seed(m, passphrase="secret")
        assert s1 != s2

    def test_different_mnemonics_different_seeds(self):
        m1 = generate_mnemonic()
        m2 = generate_mnemonic()
        assert mnemonic_to_seed(m1) != mnemonic_to_seed(m2)

    def test_invalid_mnemonic_raises(self):
        with pytest.raises(ValueError):
            mnemonic_to_seed("not a valid mnemonic phrase at all here")


class TestSeedToKey:
    def test_key_is_32_bytes(self):
        seed = mnemonic_to_seed(generate_mnemonic())
        assert len(seed_to_private_key_bytes(seed)) == 32

    def test_deterministic(self):
        seed = mnemonic_to_seed(generate_mnemonic())
        k1 = seed_to_private_key_bytes(seed)
        k2 = seed_to_private_key_bytes(seed)
        assert k1 == k2


class TestWalletRecovery:
    def test_same_mnemonic_same_address(self):
        m = generate_mnemonic()
        w1 = mnemonic_to_wallet(m)
        w2 = mnemonic_to_wallet(m)
        assert w1.address == w2.address
        assert w1.public_key_b64 == w2.public_key_b64

    def test_different_passphrase_different_address(self):
        m = generate_mnemonic()
        w1 = mnemonic_to_wallet(m, passphrase="")
        w2 = mnemonic_to_wallet(m, passphrase="secret")
        assert w1.address != w2.address

    def test_recovered_wallet_can_sign(self):
        m = generate_mnemonic()
        w = mnemonic_to_wallet(m)
        data = b"test signing"
        sig = w.sign(data)
        assert BorderWallet.verify(w.public_key_b64, data, sig) is True

    def test_original_and_recovered_agree_on_signature(self):
        """Signature from original must verify under recovered public key."""
        m = generate_mnemonic()
        original  = mnemonic_to_wallet(m)
        recovered = mnemonic_to_wallet(m)
        data = b"cross-verify"
        sig = original.sign(data)
        assert BorderWallet.verify(recovered.public_key_b64, data, sig) is True

    def test_create_with_mnemonic_stores_phrase(self):
        w = BorderWallet.create_with_mnemonic()
        assert w.mnemonic is not None
        assert validate_mnemonic(w.mnemonic)

    def test_create_with_mnemonic_recovery(self):
        w = BorderWallet.create_with_mnemonic()
        recovered = BorderWallet.from_mnemonic(w.mnemonic)
        assert recovered.address == w.address

    def test_random_wallet_has_no_mnemonic(self):
        w = BorderWallet.create()
        assert w.mnemonic is None

    def test_24_word_wallet(self):
        w = BorderWallet.create_with_mnemonic(strength=256)
        assert len(w.mnemonic.split()) == 24
        recovered = BorderWallet.from_mnemonic(w.mnemonic)
        assert recovered.address == w.address
