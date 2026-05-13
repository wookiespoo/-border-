"""
BorderCoin Wallet
Ed25519 keypair. Your identity on the BorderCoin network.
Your address = hash of your public key.
Nobody can fake it. Nobody can take it.

Key storage:
  wallet.save(path)              -- unencrypted (dev/testing only)
  wallet.save(path, password)    -- ChaCha20-Poly1305 encrypted via scrypt KDF
  BorderWallet.load(path)        -- unencrypted load
  BorderWallet.load(path, password) -- decrypts with password

Encrypted wallet format (JSON):
  {
    "address":     "BC_...",
    "public_key":  "<base64>",
    "encrypted":   true,
    "kdf":         "scrypt",
    "salt":        "<hex 32B>",
    "nonce":       "<hex 12B>",
    "ciphertext":  "<base64>",   # ChaCha20-Poly1305 of raw private key bytes
    "warning":     "..."
  }
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

# scrypt parameters -- tuned for ~100ms on modern hardware
_SCRYPT_N = 2 ** 15   # 32768
_SCRYPT_R = 8
_SCRYPT_P = 1


def _derive_key(password: str, salt: bytes) -> bytes:
    """Derive a 32-byte key from password + salt using scrypt."""
    kdf = Scrypt(salt=salt, length=32, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P)
    return kdf.derive(password.encode())


class BorderWallet:
    """
    A BorderCoin wallet.

    Your address is derived from your public key -- nobody assigned it to you,
    you generated it yourself. No bank. No signup. No permission needed.

    Usage:
        wallet = BorderWallet.create()
        wallet.save("my_wallet.json", password="hunter2")   # encrypted

        # Later
        wallet = BorderWallet.load("my_wallet.json", password="hunter2")
        print(wallet.address)  # BC_a3f8c21d...
    """

    ADDRESS_PREFIX = "BC_"

    def __init__(self, private_key: Ed25519PrivateKey):
        self._private_key = private_key
        self._public_key  = private_key.public_key()

        pub_bytes = self._public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self._address        = self.ADDRESS_PREFIX + hashlib.sha256(pub_bytes).hexdigest()[:32]
        self._public_key_b64 = base64.b64encode(pub_bytes).decode()

    @classmethod
    def create(cls) -> "BorderWallet":
        """Generate a new random wallet."""
        return cls(Ed25519PrivateKey.generate())

    @classmethod
    def load(cls, path: str, password: Optional[str] = None) -> "BorderWallet":
        """
        Load wallet from JSON file.
        Pass password= if the wallet was saved with encryption.
        """
        data = json.loads(Path(path).read_text())

        if data.get("encrypted"):
            if password is None:
                raise ValueError("This wallet is encrypted -- provide a password to load it.")
            salt       = bytes.fromhex(data["salt"])
            nonce      = bytes.fromhex(data["nonce"])
            ciphertext = base64.b64decode(data["ciphertext"])
            key        = _derive_key(password, salt)
            cipher     = ChaCha20Poly1305(key)
            # AAD = address bytes -- binds decryption to this specific wallet
            priv_bytes = cipher.decrypt(nonce, ciphertext, data["address"].encode())
        else:
            priv_bytes = base64.b64decode(data["private_key"])

        private_key = Ed25519PrivateKey.from_private_bytes(priv_bytes)
        return cls(private_key)

    def save(self, path: str, password: Optional[str] = None) -> None:
        """
        Save wallet to JSON.
        If password is provided the private key is encrypted with ChaCha20-Poly1305
        derived via scrypt. Without a password it's stored in plaintext (dev only).
        """
        pub_bytes  = self._public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        priv_bytes = self._private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )

        if password:
            salt   = os.urandom(32)
            nonce  = os.urandom(12)
            key    = _derive_key(password, salt)
            cipher = ChaCha20Poly1305(key)
            ct     = cipher.encrypt(nonce, priv_bytes, self._address.encode())
            data   = {
                "address":    self._address,
                "public_key": self._public_key_b64,
                "encrypted":  True,
                "kdf":        "scrypt",
                "salt":       salt.hex(),
                "nonce":      nonce.hex(),
                "ciphertext": base64.b64encode(ct).decode(),
                "warning":    "This file is encrypted. Keep it safe.",
            }
        else:
            data = {
                "address":     self._address,
                "public_key":  self._public_key_b64,
                "private_key": base64.b64encode(priv_bytes).decode(),
                "encrypted":   False,
                "warning":     "UNENCRYPTED -- do not commit to version control!",
            }

        Path(path).write_text(json.dumps(data, indent=2))

    # -- Properties ------------------------------------------
    @property
    def address(self) -> str:
        return self._address

    @property
    def public_key_b64(self) -> str:
        return self._public_key_b64

    # -- Signing ---------------------------------------------
    def sign(self, data: bytes) -> str:
        """Sign arbitrary bytes. Returns base64-encoded Ed25519 signature."""
        sig = self._private_key.sign(data)
        return base64.b64encode(sig).decode()

    @staticmethod
    def verify(public_key_b64: str, data: bytes, signature_b64: str) -> bool:
        """Verify an Ed25519 signature. Returns True if valid."""
        try:
            pub_bytes = base64.b64decode(public_key_b64)
            pub_key   = Ed25519PublicKey.from_public_bytes(pub_bytes)
            sig_bytes = base64.b64decode(signature_b64)
            pub_key.verify(sig_bytes, data)
            return True
        except Exception:
            return False

    def __repr__(self) -> str:
        return f"<BorderWallet {self._address}>"

    # -- Mnemonic recovery -----------------------------------

    @classmethod
    def from_seed_bytes(cls, seed_bytes: bytes) -> "BorderWallet":
        """
        Construct a wallet deterministically from 32 raw seed bytes.
        Used internally by mnemonic_to_wallet().
        """
        if len(seed_bytes) != 32:
            raise ValueError(f"Expected 32 seed bytes, got {len(seed_bytes)}")
        private_key = Ed25519PrivateKey.from_private_bytes(seed_bytes)
        return cls(private_key)

    @classmethod
    def from_mnemonic(cls, phrase: str, passphrase: str = "") -> "BorderWallet":
        """
        Reconstruct a wallet from a BIP-39 mnemonic phrase.

        Args:
            phrase:     12 or 24-word BIP-39 mnemonic.
            passphrase: Optional BIP-39 passphrase (default: empty string).

        Returns:
            BorderWallet identical to the one originally generated from this phrase.

        Example:
            wallet = BorderWallet.create_with_mnemonic()
            phrase = wallet._mnemonic
            # ... later, on a new device:
            recovered = BorderWallet.from_mnemonic(phrase)
            assert recovered.address == wallet.address
        """
        from .mnemonic import mnemonic_to_wallet
        return mnemonic_to_wallet(phrase, passphrase)

    @classmethod
    def create_with_mnemonic(cls, strength: int = 128,
                             passphrase: str = "") -> "BorderWallet":
        """
        Generate a new wallet backed by a BIP-39 mnemonic.

        The mnemonic is stored on the returned wallet as ``wallet.mnemonic``.
        Write it down — it's the only way to recover this wallet.

        Args:
            strength:   128 (12 words, default) or 256 (24 words).
            passphrase: Optional BIP-39 passphrase.

        Returns:
            BorderWallet with a ``.mnemonic`` attribute set.
        """
        from .mnemonic import generate_mnemonic, mnemonic_to_wallet
        phrase = generate_mnemonic(strength=strength)
        wallet = mnemonic_to_wallet(phrase, passphrase)
        wallet._mnemonic = phrase          # attach for immediate display
        return wallet

    @property
    def mnemonic(self) -> Optional[str]:
        """
        The BIP-39 mnemonic for this wallet, if created via create_with_mnemonic().
        Returns None for wallets created with create() or loaded from file.
        Store this phrase in a safe place — it's your backup key.
        """
        return getattr(self, "_mnemonic", None)
