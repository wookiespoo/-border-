"""
BorderStore -- Chunk + Encryption Layer
========================================
Files are split into fixed-size chunks, encrypted, and content-addressed.
The chunk_id IS the SHA256 of the plaintext -- immutable, verifiable.
Encryption keys stay with the client. Storage nodes never see plaintext.

Encryption: ChaCha20-Poly1305 (AEAD)
  - 32-byte random key per chunk
  - 12-byte random nonce per chunk
  - Built-in authentication tag -- bit-flip by a malicious node is detected
    before any decrypted bytes are returned to the caller.
  - Key stored in FileManifest as hex (client-side only, never uploaded).

Flow:
  Client splits file -> encrypts each chunk -> distributes to storage nodes
  Storage nodes store ciphertext (nonce + tag + ciphertext) only
  Client downloads ciphertext -> decrypts+authenticates locally -> reassembles
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

NONCE_SIZE = 12   # ChaCha20-Poly1305 nonce length in bytes


# ─────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────

CHUNK_SIZE         = 4 * 1024 * 1024  # 4MB per chunk
REPLICATION_FACTOR = 3                # store each chunk on 3 nodes
BC_PER_GB_PER_DAY  = 0.01            # BC earned per GB stored per day
BC_PER_CHALLENGE   = 0.0001          # BC earned per passed storage challenge


# ─────────────────────────────────────────────────────────
# Chunk
# ─────────────────────────────────────────────────────────

@dataclass
class Chunk:
    """
    A single encrypted chunk of a file.
    chunk_id  = SHA256 of plaintext (content address -- immutable).
    ciphertext = nonce (12B) + ChaCha20-Poly1305 ciphertext+tag stored on nodes.
    key        = 32-byte random key kept in FileManifest (client-side only).
    """
    chunk_id:   str       # SHA256 of plaintext
    file_id:    str       # which file this belongs to
    index:      int       # position in the file
    plaintext:  bytes     # original data (never sent to nodes)
    ciphertext: bytes     # nonce || encrypted data (stored on nodes)
    size:       int       # plaintext size in bytes
    key:        bytes     # 32-byte ChaCha20-Poly1305 key (client only)

    @classmethod
    def from_plaintext(cls, file_id: str, index: int, data: bytes) -> "Chunk":
        """Create a chunk from raw data. Generates key, nonce, and AEAD-encrypts."""
        chunk_id = hashlib.sha256(data).hexdigest()
        key      = secrets.token_bytes(32)
        nonce    = os.urandom(NONCE_SIZE)
        cipher   = ChaCha20Poly1305(key)
        # Authenticated additional data = chunk_id bytes (binds ciphertext to content address)
        ct = cipher.encrypt(nonce, data, chunk_id.encode())
        return cls(
            chunk_id   = chunk_id,
            file_id    = file_id,
            index      = index,
            plaintext  = data,
            ciphertext = nonce + ct,   # prepend nonce for storage
            size       = len(data),
            key        = key,
        )

    def decrypt(self, ciphertext: bytes) -> bytes:
        """
        Decrypt and authenticate ciphertext returned by a storage node.
        Raises cryptography.exceptions.InvalidTag if the node tampered with data.
        """
        nonce  = ciphertext[:NONCE_SIZE]
        ct     = ciphertext[NONCE_SIZE:]
        cipher = ChaCha20Poly1305(self.key)
        return cipher.decrypt(nonce, ct, self.chunk_id.encode())

    def verify(self, ciphertext: bytes) -> bool:
        """
        Verify a node returned authentic, unmodified data.
        Uses AEAD authentication -- any bit-flip is detected.
        """
        try:
            plaintext = self.decrypt(ciphertext)
            return hashlib.sha256(plaintext).hexdigest() == self.chunk_id
        except Exception:
            return False

    def to_manifest_entry(self) -> dict:
        """Serialisable entry for FileManifest (no plaintext -- key stored for client)."""
        return {
            "chunk_id": self.chunk_id,
            "index":    self.index,
            "size":     self.size,
            "key":      self.key.hex(),
        }


# ─────────────────────────────────────────────────────────
# File Manifest
# ─────────────────────────────────────────────────────────

@dataclass
class FileManifest:
    """
    The client's record of an uploaded file.
    Contains encryption keys and chunk locations.
    NEVER share this -- it has the keys.
    """
    file_id:       str
    filename:      str
    total_size:    int
    chunk_count:   int
    chunk_size:    int
    chunk_entries: List[dict]           # [{chunk_id, index, size, key}]
    node_map:      Dict[str, List[str]] # chunk_id -> [node_endpoint, ...]
    owner_address: str
    uploaded_at:   float = field(default_factory=time.time)
    content_hash:  str   = ""           # SHA256 of full file

    @property
    def size_gb(self) -> float:
        return self.total_size / (1024 ** 3)

    def get_key(self, chunk_id: str) -> Optional[bytes]:
        for entry in self.chunk_entries:
            if entry["chunk_id"] == chunk_id:
                return bytes.fromhex(entry["key"])
        return None

    def to_dict(self) -> dict:
        return {
            "file_id":       self.file_id,
            "filename":      self.filename,
            "total_size":    self.total_size,
            "chunk_count":   self.chunk_count,
            "chunk_size":    self.chunk_size,
            "chunk_entries": self.chunk_entries,
            "node_map":      self.node_map,
            "owner_address": self.owner_address,
            "uploaded_at":   self.uploaded_at,
            "content_hash":  self.content_hash,
        }

    def save(self, path: str) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2))

    @classmethod
    def load(cls, path: str) -> "FileManifest":
        data = json.loads(Path(path).read_text())
        return cls(**data)

    @classmethod
    def from_dict(cls, d: dict) -> "FileManifest":
        return cls(**d)


# ─────────────────────────────────────────────────────────
# Chunker
# ─────────────────────────────────────────────────────────

class FileChunker:
    """Splits a file into encrypted chunks ready for distribution."""

    def __init__(self, chunk_size: int = CHUNK_SIZE):
        self.chunk_size = chunk_size

    def split(self, data: bytes, file_id: str) -> List[Chunk]:
        """Split raw bytes into encrypted chunks."""
        chunks = []
        for i in range(0, len(data), self.chunk_size):
            piece = data[i:i + self.chunk_size]
            chunk = Chunk.from_plaintext(file_id=file_id, index=len(chunks), data=piece)
            chunks.append(chunk)
        return chunks

    def split_file(self, path: str, file_id: str) -> Tuple[List[Chunk], str]:
        """Split a file on disk into chunks. Returns (chunks, content_hash)."""
        data         = Path(path).read_bytes()
        content_hash = hashlib.sha256(data).hexdigest()
        chunks       = self.split(data, file_id)
        return chunks, content_hash

    def reassemble(self, chunks: List[Chunk]) -> bytes:
        """Reassemble plaintext from a list of chunks (sorted by index)."""
        return b"".join(c.plaintext for c in sorted(chunks, key=lambda c: c.index))

    def build_manifest(
        self,
        file_id:       str,
        filename:      str,
        chunks:        List[Chunk],
        owner_address: str,
        content_hash:  str,
        node_map:      Dict[str, List[str]],
    ) -> FileManifest:
        """Build a FileManifest from a list of already-encrypted chunks."""
        return FileManifest(
            file_id       = file_id,
            filename      = filename,
            total_size    = sum(c.size for c in chunks),
            chunk_count   = len(chunks),
            chunk_size    = self.chunk_size,
            chunk_entries = [c.to_manifest_entry() for c in chunks],
            node_map      = node_map,
            owner_address = owner_address,
            content_hash  = content_hash,
        )
