"""
Phantom Obfuscation Layer
Makes Phantom traffic indistinguishable from normal HTTPS web traffic.
"""

from __future__ import annotations

import base64
import json
import os
import struct
import time
import uuid
from dataclasses import dataclass
from typing import Optional, Tuple

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


# Cover domains — traffic appears to come from/go to these
COVER_DOMAINS = [
    "api.analytics-gateway.com",
    "cdn.metrics-collector.net",
    "data.telemetry-service.io",
    "events.tracking-hub.com",
    "sync.data-pipeline.net",
]

# Padding sizes — random selection makes size fingerprinting hard
PADDING_SIZES = [64, 128, 256, 512, 1024, 2048]

# User agents to cycle through
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
]


@dataclass
class BorderSession:
    """A single encrypted session between a client and relay."""
    session_id: str
    our_private_key: X25519PrivateKey
    our_public_key_bytes: bytes
    shared_key: Optional[bytes] = None
    cipher: Optional[ChaCha20Poly1305] = None

    @classmethod
    def create(cls) -> "BorderSession":
        private_key = X25519PrivateKey.generate()
        public_key_bytes = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return cls(
            session_id=f"sess_{uuid.uuid4().hex[:12]}",
            our_private_key=private_key,
            our_public_key_bytes=public_key_bytes,
        )

    def complete_handshake(self, their_public_key_bytes: bytes) -> None:
        """Complete ECDH key exchange and derive session key."""
        their_public_key = X25519PublicKey.from_public_bytes(their_public_key_bytes)
        raw_shared = self.our_private_key.exchange(their_public_key)

        # Derive 32-byte session key via HKDF
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=b"phantom-session-v1",
        )
        self.shared_key = hkdf.derive(raw_shared)
        self.cipher = ChaCha20Poly1305(self.shared_key)

    def encrypt(self, plaintext: bytes) -> Tuple[bytes, bytes]:
        """Encrypt plaintext. Returns (nonce, ciphertext)."""
        if not self.cipher:
            raise RuntimeError("Session not established — complete handshake first")
        nonce = os.urandom(12)
        ciphertext = self.cipher.encrypt(nonce, plaintext, None)
        return nonce, ciphertext

    def decrypt(self, nonce: bytes, ciphertext: bytes) -> bytes:
        """Decrypt ciphertext."""
        if not self.cipher:
            raise RuntimeError("Session not established — complete handshake first")
        return self.cipher.decrypt(nonce, ciphertext, None)


class BorderObfuscator:
    """
    Wraps Phantom protocol messages in HTTPS-mimicry clothing.
    Traffic appears to be normal JSON API calls.
    """

    def __init__(self):
        self._cover_domain_index = 0

    def wrap_request(
        self,
        payload: dict,
        session: BorderSession,
    ) -> dict:
        """
        Wrap a Phantom request payload into an HTTPS-mimicry envelope.
        Returns a dict that looks like a normal JSON API request body.
        """
        # Serialize payload
        raw = json.dumps(payload).encode()

        # Encrypt
        nonce, ciphertext = session.encrypt(raw)

        # Build inner envelope with our public key for key exchange
        inner = {
            "pk": base64.b64encode(session.our_public_key_bytes).decode(),
            "n": base64.b64encode(nonce).decode(),
            "d": base64.b64encode(ciphertext).decode(),
        }
        inner_bytes = json.dumps(inner).encode()

        # Add random padding to defeat size fingerprinting
        padding_size = _random_choice(PADDING_SIZES)
        padding = base64.b64encode(os.urandom(padding_size)).decode()

        # Final envelope looks like a normal analytics/tracking API call
        return {
            "session": base64.b64encode(inner_bytes).decode(),
            "ts": int(time.time() * 1000),
            "v": "1",
            "mid": str(uuid.uuid4()),
            "_pad": padding,
        }

    def unwrap_request(
        self,
        envelope: dict,
        session: BorderSession,
    ) -> dict:
        """Unwrap an incoming request envelope."""
        inner_bytes = base64.b64decode(envelope["session"])
        inner = json.loads(inner_bytes)

        their_pubkey = base64.b64decode(inner["pk"])
        nonce = base64.b64decode(inner["n"])
        ciphertext = base64.b64decode(inner["d"])

        # Complete key exchange if not done
        if not session.shared_key:
            session.complete_handshake(their_pubkey)

        plaintext = session.decrypt(nonce, ciphertext)
        return json.loads(plaintext)

    def wrap_response(
        self,
        payload: dict,
        session: BorderSession,
    ) -> dict:
        """Wrap a response to look like a normal API response."""
        raw = json.dumps(payload).encode()
        nonce, ciphertext = session.encrypt(raw)

        padding_size = _random_choice(PADDING_SIZES)

        return {
            "status": "ok",
            "data": base64.b64encode(ciphertext).decode(),
            "meta": base64.b64encode(nonce).decode(),
            "rid": str(uuid.uuid4()),
            "t": int(time.time() * 1000),
            "_x": base64.b64encode(os.urandom(padding_size)).decode(),
        }

    def unwrap_response(
        self,
        envelope: dict,
        session: BorderSession,
    ) -> dict:
        """Unwrap a response envelope."""
        nonce = base64.b64decode(envelope["meta"])
        ciphertext = base64.b64decode(envelope["data"])
        plaintext = session.decrypt(nonce, ciphertext)
        return json.loads(plaintext)

    def get_cover_headers(self) -> dict:
        """Generate HTTP headers that mimic a normal browser request."""
        domain = _random_choice(COVER_DOMAINS)
        return {
            "Content-Type": "application/json",
            "User-Agent": _random_choice(USER_AGENTS),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Origin": f"https://{domain}",
            "Referer": f"https://{domain}/app",
            "X-Request-ID": str(uuid.uuid4()),
            "Cache-Control": "no-cache",
        }


def _random_choice(lst: list):
    """Pick a random element from a list."""
    idx = struct.unpack("I", os.urandom(4))[0] % len(lst)
    return lst[idx]
