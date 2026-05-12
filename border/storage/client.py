"""
BorderStore — Storage Client
==============================
The client-side library for uploading/downloading files
to/from the BorderStore network.

The client:
  1. Splits the file into chunks
  2. Encrypts each chunk (keys never leave the client)
  3. Distributes chunks across multiple storage nodes
  4. Saves a FileManifest (keys + locations)
  5. Can reconstruct the file from any copy of the manifest

Usage:
    from border.storage import StorageClient

    client = StorageClient(
        owner_address="BC_...",
        node_endpoints=["http://node1:9999", "http://node2:9999"],
    )

    manifest = await client.upload("myfile.zip")
    manifest.save("myfile.manifest")

    # Later:
    manifest = FileManifest.load("myfile.manifest")
    data = await client.download(manifest)
    open("restored.zip", "wb").write(data)
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
import time
import uuid
from typing import Dict, List, Optional, Tuple

import httpx

from .chunk import Chunk, FileChunker, FileManifest, REPLICATION_FACTOR
from .proof import StorageChallenge, StorageProof

logger = logging.getLogger("border.storage.client")


class StorageClient:
    """
    Client for the BorderStore network.
    Handles upload, download, and challenge verification.
    """

    def __init__(
        self,
        owner_address:  str,
        node_endpoints: List[str],
        replication:    int   = REPLICATION_FACTOR,
        timeout:        float = 30.0,
    ):
        self.owner_address  = owner_address
        self.node_endpoints = node_endpoints
        self.replication    = min(replication, len(node_endpoints))
        self.timeout        = timeout
        self.chunker        = FileChunker()

    # ─────────────────────────────────────────────────────
    # Upload
    # ─────────────────────────────────────────────────────

    async def upload_bytes(self, data: bytes, filename: str) -> FileManifest:
        """Upload raw bytes to the network. Returns FileManifest."""
        file_id      = f"file_{uuid.uuid4().hex[:16]}"
        content_hash = hashlib.sha256(data).hexdigest()
        chunks       = self.chunker.split(data, file_id)

        logger.info(
            f"[StorageClient] Uploading {filename} | "
            f"{len(data)/(1024**2):.1f}MB | {len(chunks)} chunks"
        )

        node_map: Dict[str, List[str]] = {}
        nodes = await self._get_available_nodes()

        for chunk in chunks:
            placed = await self._distribute_chunk(chunk, nodes)
            node_map[chunk.chunk_id] = placed
            logger.debug(f"[StorageClient] Chunk {chunk.chunk_id[:12]}... → {len(placed)} node(s)")

        manifest = self.chunker.build_manifest(
            file_id=file_id,
            filename=filename,
            chunks=chunks,
            owner_address=self.owner_address,
            content_hash=content_hash,
            node_map=node_map,
        )

        placed_count = sum(1 for nodes in node_map.values() if nodes)
        logger.info(
            f"[StorageClient] Upload complete | "
            f"{placed_count}/{len(chunks)} chunks placed | "
            f"file_id={file_id}"
        )
        return manifest

    async def upload_file(self, path: str) -> FileManifest:
        """Upload a file from disk."""
        import pathlib
        p = pathlib.Path(path)
        data = p.read_bytes()
        return await self.upload_bytes(data, p.name)

    async def _distribute_chunk(self, chunk: Chunk, nodes: List[str]) -> List[str]:
        """Upload a chunk to `replication` nodes. Returns list of successful endpoints."""
        placed = []
        import json
        meta_header = json.dumps({
            "file_id":       chunk.file_id,
            "index":         chunk.index,
            "owner_address": self.owner_address,
        })

        for node in nodes:
            if len(placed) >= self.replication:
                break
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as http:
                    resp = await http.post(
                        f"{node}/storage/store/{chunk.chunk_id}",
                        content=chunk.ciphertext,
                        headers={"X-Border-Meta": meta_header},
                    )
                    if resp.status_code == 200:
                        placed.append(node)
            except Exception as e:
                logger.debug(f"[StorageClient] Node {node} failed: {e}")

        return placed

    # ─────────────────────────────────────────────────────
    # Download
    # ─────────────────────────────────────────────────────

    async def download(self, manifest: FileManifest) -> bytes:
        """Download and decrypt a file using its manifest."""
        logger.info(
            f"[StorageClient] Downloading {manifest.filename} | "
            f"{manifest.chunk_count} chunks"
        )

        chunk_data: List[Tuple[int, bytes]] = []

        for entry in manifest.chunk_entries:
            chunk_id = entry["chunk_id"]
            index    = entry["index"]
            key      = bytes.fromhex(entry["key"])
            nodes    = manifest.node_map.get(chunk_id, [])

            ciphertext = await self._fetch_chunk(chunk_id, nodes)
            if ciphertext is None:
                raise RuntimeError(f"Could not retrieve chunk {chunk_id[:16]}...")

            # Decrypt
            plaintext = bytes(a ^ b for a, b in zip(ciphertext, key))

            # Verify
            if hashlib.sha256(plaintext).hexdigest() != chunk_id:
                raise RuntimeError(f"Chunk {chunk_id[:16]}... failed integrity check!")

            chunk_data.append((index, plaintext))

        result = self.chunker.reassemble(chunk_data)

        # Verify full file
        if manifest.content_hash:
            actual = hashlib.sha256(result).hexdigest()
            if actual != manifest.content_hash:
                raise RuntimeError("File integrity check failed!")

        logger.info(f"[StorageClient] Download complete | {len(result)/(1024**2):.1f}MB ✓")
        return result

    async def _fetch_chunk(self, chunk_id: str, nodes: List[str]) -> Optional[bytes]:
        """Try to fetch a chunk from any of its stored nodes."""
        for node in nodes:
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as http:
                    resp = await http.get(f"{node}/storage/retrieve/{chunk_id}")
                    if resp.status_code == 200:
                        return resp.content
            except Exception as e:
                logger.debug(f"[StorageClient] Fetch failed from {node}: {e}")
        return None

    # ─────────────────────────────────────────────────────
    # Challenge verification
    # ─────────────────────────────────────────────────────

    async def challenge_node(
        self,
        node_endpoint: str,
        chunk_id:      str,
        manifest:      FileManifest,
    ) -> Tuple[bool, Optional[StorageProof]]:
        """
        Issue a storage challenge to a node.
        Returns (passed, proof) — proof can be submitted to chain.
        """
        challenge = StorageChallenge.issue(
            chunk_id=chunk_id,
            node_address=node_endpoint,
        )

        try:
            async with httpx.AsyncClient(timeout=10) as http:
                resp = await http.post(
                    f"{node_endpoint}/storage/challenge",
                    json=challenge.to_dict(),
                )
                if resp.status_code != 200:
                    return False, None

                data          = resp.json()
                response_hash = data.get("response_hash", "")

        except Exception as e:
            logger.warning(f"[StorageClient] Challenge failed for {node_endpoint}: {e}")
            return False, None

        # Verify: we need to fetch the ciphertext to compute expected hash
        ciphertext = await self._fetch_chunk(chunk_id, [node_endpoint])
        if ciphertext is None:
            return False, None

        expected = challenge.expected_response(ciphertext)
        passed   = (response_hash == expected)

        if passed:
            proof = StorageProof.from_challenge(
                challenge=challenge,
                node_address=node_endpoint,
                owner_address=self.owner_address,
                file_id=manifest.file_id,
                bytes_stored=len(ciphertext),
                response_hash=response_hash,
                expected_hash=expected,
            )
            return True, proof

        logger.warning(f"[StorageClient] Challenge FAILED for {node_endpoint} chunk {chunk_id[:12]}...")
        return False, None

    async def challenge_all(self, manifest: FileManifest) -> List[Tuple[bool, Optional[StorageProof]]]:
        """Challenge all nodes for all chunks in a manifest."""
        results = []
        for entry in manifest.chunk_entries:
            chunk_id = entry["chunk_id"]
            for node in manifest.node_map.get(chunk_id, []):
                passed, proof = await self.challenge_node(node, chunk_id, manifest)
                results.append((passed, proof))
        return results

    # ─────────────────────────────────────────────────────
    # Node discovery
    # ─────────────────────────────────────────────────────

    async def _get_available_nodes(self) -> List[str]:
        """Return nodes that are online and have capacity."""
        available = []
        for node in self.node_endpoints:
            try:
                async with httpx.AsyncClient(timeout=3) as http:
                    resp = await http.get(f"{node}/storage/health")
                    if resp.status_code == 200:
                        available.append(node)
            except Exception:
                pass

        if not available:
            # Fallback: try all nodes even if health check fails
            return self.node_endpoints

        return available
