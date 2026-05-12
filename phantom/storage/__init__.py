"""
BorderStore — Decentralised Encrypted Storage
Every GB stored = passive BorderCoin income.

Usage:
    # Start a storage node
    from phantom.storage import serve_storage
    serve_storage(wallet_path="wallet.json", capacity_gb=500, port=9999)

    # Upload a file
    from phantom.storage import StorageClient
    client = StorageClient(owner_address="BC_...", node_endpoints=["http://localhost:9999"])
    manifest = await client.upload_file("myfile.zip")
    manifest.save("myfile.manifest")

    # Download
    from phantom.storage import FileManifest
    manifest = FileManifest.load("myfile.manifest")
    data = await client.download(manifest)
"""

from .chunk import Chunk, FileChunker, FileManifest, CHUNK_SIZE, REPLICATION_FACTOR, BC_PER_GB_PER_DAY, BC_PER_CHALLENGE
from .proof import StorageChallenge, StorageProof
from .node import BorderStorageNode, serve_storage
from .client import StorageClient

__all__ = [
    "Chunk", "FileChunker", "FileManifest",
    "StorageChallenge", "StorageProof",
    "BorderStorageNode", "StorageClient", "serve_storage",
    "CHUNK_SIZE", "REPLICATION_FACTOR", "BC_PER_GB_PER_DAY", "BC_PER_CHALLENGE",
]
