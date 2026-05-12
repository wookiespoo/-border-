"""
BorderCoin Wallet
Ed25519 keypair. Your identity on the BorderCoin network.
Your address = hash of your public key.
Nobody can fake it. Nobody can take it.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


class BorderWallet:
    """
    A BorderCoin wallet.

    Your address is derived from your public key — nobody assigned it to you,
    you generated it yourself. No bank. No signup. No permission needed.

    Usage:
        wallet = BorderWallet.create()
        wallet.save("my_wallet.json")

        # Later
        wallet = BorderWallet.load("my_wallet.json")
        print(wallet.address)  # PC_a3f8c21d...
    """

    ADDRESS_PREFIX = "BC_"

    def __init__(self, private_key: Ed25519PrivateKey):
        self._private_key = private_key
        self._public_key = private_key.public_key()

        pub_bytes = self._public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self._address = self.ADDRESS_PREFIX + hashlib.sha256(pub_bytes).hexdigest()[:32]
        self._public_key_b64 = base64.b64encode(pub_bytes).decode()

    @classmethod
    def create(cls) -> "BorderWallet":
        """Generate a new random wallet."""
        return cls(Ed25519PrivateKey.generate())

    @classmethod
    def load(cls, path: str) -> "BorderWallet":
        """Load wallet from JSON file."""
        data = json.loads(Path(path).read_text())
        priv_bytes = base64.b64decode(data["private_key"])
        private_key = Ed25519PrivateKey.from_private_bytes(priv_bytes)
        return cls(private_key)

    def save(self, path: str) -> None:
        """Save wallet to JSON file. Keep this file secret."""
        priv_bytes = self._private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        data = {
            "address": self._address,
            "public_key": self._public_key_b64,
            "private_key": base64.b64encode(priv_bytes).decode(),
            "warning": "KEEP THIS FILE SECRET. Your private key is inside.",
        }
        Path(path).write_text(json.dumps(data, indent=2))

    def sign(self, message: bytes) -> str:
        """Sign a message with your private key. Returns base64 signature."""
        sig = self._private_key.sign(message)
        return base64.b64encode(sig).decode()

    @staticmethod
    def verify(public_key_b64: str, message: bytes, signature_b64: str) -> bool:
        """Verify a signature against a public key."""
        try:
            pub_bytes = base64.b64decode(public_key_b64)
            pub_key = Ed25519PublicKey.from_public_bytes(pub_bytes)
            sig = base64.b64decode(signature_b64)
            pub_key.verify(sig, message)
            return True
        except Exception:
            return False

    @property
    def address(self) -> str:
        return self._address

    @property
    def public_key_b64(self) -> str:
        return self._public_key_b64

    def __repr__(self) -> str:
        return f"BorderWallet(address={self._address})"
