"""
border.blockchain.mnemonic — BIP-39 mnemonic seed phrase support for BorderWallet

Generates a 12 or 24-word BIP-39 mnemonic, derives a deterministic 32-byte
seed via PBKDF2-HMAC-SHA512 (BIP-39 spec), and then stretches it further
through HKDF-SHA256 into an Ed25519 private key.

Public API
----------
    generate_mnemonic(strength=128)          -> str  (12 words)
    generate_mnemonic(strength=256)          -> str  (24 words)
    mnemonic_to_seed(mnemonic, passphrase="")-> bytes (64-byte BIP-39 seed)
    seed_to_private_key_bytes(seed)          -> bytes (32 bytes, Ed25519 scalar)
    mnemonic_to_wallet(mnemonic, passphrase="") -> BorderWallet

Security notes
--------------
  * PBKDF2 uses 2048 iterations (BIP-39 spec minimum); callers may increase.
  * HKDF-SHA256 info string is "border-ed25519-key-v1" for domain separation.
  * The mnemonic itself is validated against the BIP-39 English word list
    before deriving any key material.
"""

from __future__ import annotations

import hashlib
import hmac

from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

try:
    from mnemonic import Mnemonic as _Mnemonic
    _mnemo = _Mnemonic("english")
except ImportError as e:
    raise ImportError(
        "The 'mnemonic' package is required for BIP-39 support.  "
        "Install with:  pip install mnemonic"
    ) from e


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PBKDF2_ITERATIONS = 2048
HKDF_INFO         = b"border-ed25519-key-v1"


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def generate_mnemonic(strength: int = 128) -> str:
    """
    Generate a random BIP-39 mnemonic phrase.

    Args:
        strength: Entropy bits.  128 = 12 words (default), 256 = 24 words.
                  Must be one of {128, 160, 192, 224, 256}.

    Returns:
        Space-separated mnemonic string (English).
    """
    if strength not in {128, 160, 192, 224, 256}:
        raise ValueError(f"strength must be 128/160/192/224/256, got {strength}")
    return _mnemo.generate(strength=strength)


def validate_mnemonic(phrase: str) -> bool:
    """Return True if the phrase is a valid BIP-39 English mnemonic."""
    return _mnemo.check(phrase.strip())


def mnemonic_to_seed(phrase: str, passphrase: str = "") -> bytes:
    """
    Convert a BIP-39 mnemonic to a 64-byte seed (BIP-39 spec).

    Uses PBKDF2-HMAC-SHA512 with salt = "mnemonic" + passphrase.

    Raises:
        ValueError: If phrase is not a valid BIP-39 mnemonic.
    """
    phrase = phrase.strip()
    if not validate_mnemonic(phrase):
        raise ValueError("Invalid BIP-39 mnemonic phrase")

    salt = ("mnemonic" + passphrase).encode("utf-8")
    seed = hashlib.pbkdf2_hmac(
        "sha512",
        phrase.encode("utf-8"),
        salt,
        PBKDF2_ITERATIONS,
    )
    return seed          # 64 bytes


def seed_to_private_key_bytes(seed: bytes) -> bytes:
    """
    Derive a 32-byte Ed25519 private key scalar from a BIP-39 seed.

    Uses HKDF-SHA256 with info = HKDF_INFO for domain separation.
    Input key material is the first 32 bytes of the 64-byte BIP-39 seed.

    Returns:
        32-byte raw private key suitable for Ed25519SigningKey construction.
    """
    hkdf = HKDF(
        algorithm=SHA256(),
        length=32,
        salt=None,           # HKDF uses a zero-filled salt when None
        info=HKDF_INFO,
    )
    return hkdf.derive(seed[:32])


def mnemonic_to_wallet(phrase: str, passphrase: str = "") -> "BorderWallet":
    """
    Reconstruct a BorderWallet deterministically from a mnemonic phrase.

    Args:
        phrase:     BIP-39 mnemonic (12 or 24 words).
        passphrase: Optional BIP-39 passphrase (default: empty string).

    Returns:
        BorderWallet whose address and keys are fully determined by the phrase.
    """
    from .wallet import BorderWallet

    seed         = mnemonic_to_seed(phrase, passphrase)
    key_bytes    = seed_to_private_key_bytes(seed)
    return BorderWallet.from_seed_bytes(key_bytes)
