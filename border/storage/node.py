"""
BorderStore -- Storage Node
============================
Run this on any machine with spare disk space.
Store encrypted chunks for clients, respond to challenges, earn BC.

Every byte stored = passive BorderCoin income.
You never see the plaintext -- clients encrypt before uploading.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from pathlib import Path
from typing import Dict, List, Optional, Set

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response, JSONResponse

from .chunk import BC_PER_GB_PER_DAY, CHUNK_SIZE
from .proof import StorageChallenge, StorageProof

logger = logging.getLogger("border.storage.node")


class BorderStorageNode:
    """
    A Border storage node.

    Accepts encrypted chunks from clients, stores them on disk,
    responds to storage challenges (proving possession),
    and submits StorageProofs to the blockchain to earn BorderCoin.

    The node NEVER sees plaintext -- all data is pre-encrypted by clients.
    """

    def __init__(
        self,
        node_id:             str,
        wallet,
        storage_path:        str   = "./border_storage",
        capacity_gb:         float = 100.0,
        price_bc_per_gb_day: float = BC_PER_GB_PER_DAY,
        chain_endpoint:      Optional[str] = None,
        endpoint:            str   = "http://localhost:9999",
        region:              str   = "UNKNOWN",
    ):
        self.node_id      = node_id
        self.wallet       = wallet
        self.storage_path = Path(storage_path)
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.capacity_gb         = capacity_gb
        self.price_bc_per_gb_day = price_bc_per_gb_day
        self.chain_endpoint      = chain_endpoint
        self.endpoint            = endpoint
        self.region              = region

        self._chunks_stored:     Set[str]                     = set()
        self._chunk_metadata:    Dict[str, dict]              = {}
        self._active_challenges: Dict[str, StorageChallenge]  = {}
        self._proofs_submitted:  Set[str]                     = set()
        self._start_time         = time.time()
        self._bytes_stored       = 0
        self._bc_earned          = 0.0
        self._challenges_passed  = 0

        self._load_index()
        logger.info(
            f"[StorageNode] {node_id} | "
            f"capacity={capacity_gb}GB | "
            f"chunks={len(self._chunks_stored)} | "
            f"chain={chain_endpoint or 'none'}"
        )

    def _chunk_path(self, chunk_id: str) -> Path:
        return self.storage_path / chunk_id[:2] / chunk_id[2:4] / chunk_id

    def store_chunk(self, chunk_id: str, ciphertext: bytes, metadata: dict) -> bool:
        if chunk_id in self._chunks_stored:
            return True
        path = self._chunk_path(chunk_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(ciphertext)
        self._chunks_stored.add(chunk_id)
        self._chunk_metadata[chunk_id] = {
            **metadata,
            "stored_at": time.time(),
            "size":      len(ciphertext),
        }
        self._bytes_stored += len(ciphertext)
        self._save_index()
        logger.info(f"[StorageNode] Stored chunk {chunk_id[:16]}... ({len(ciphertext)} bytes)")
        return True

    def retrieve_chunk(self, chunk_id: str) -> Optional[bytes]:
        if chunk_id not in self._chunks_stored:
            return None
        path = self._chunk_path(chunk_id)
        if not path.exists():
            return None
        return path.read_bytes()

    def delete_chunk(self, chunk_id: str) -> bool:
        if chunk_id not in self._chunks_stored:
            return False
        path = self._chunk_path(chunk_id)
        if path.exists():
            size = path.stat().st_size
            path.unlink()
            self._bytes_stored = max(0, self._bytes_stored - size)
        self._chunks_stored.discard(chunk_id)
        self._chunk_metadata.pop(chunk_id, None)
        self._save_index()
        return True

    def respond_to_challenge(self, challenge: StorageChallenge) -> Optional[str]:
        ciphertext = self.retrieve_chunk(challenge.chunk_id)
        if ciphertext is None:
            return None
        return hashlib.sha256(ciphertext + challenge.nonce.encode()).hexdigest()

    def build_proof_from_challenge(
        self,
        challenge:     StorageChallenge,
        owner_address: str,
        file_id:       str,
    ) -> Optional[StorageProof]:
        ciphertext = self.retrieve_chunk(challenge.chunk_id)
        if ciphertext is None:
            return None
        response_hash = self.respond_to_challenge(challenge)
        expected_hash = challenge.expected_response(ciphertext)
        proof = StorageProof.from_challenge(
            challenge     = challenge,
            node_address  = self.wallet.address,
            owner_address = owner_address,
            file_id       = file_id,
            bytes_stored  = len(ciphertext),
            response_hash = response_hash,
            expected_hash = expected_hash,
        )
        proof.node_public_key = self.wallet.public_key_b64
        proof.node_signature  = self.wallet.sign(proof.hash().encode())
        return proof

    def build_duration_proof(self, chunk_id: str, owner_address: str, file_id: str) -> Optional[StorageProof]:
        if chunk_id not in self._chunks_stored:
            return None
        meta      = self._chunk_metadata.get(chunk_id, {})
        stored_at = meta.get("stored_at", time.time())
        duration  = time.time() - stored_at
        size      = meta.get("size", 0)
        proof = StorageProof.from_duration(
            node_address     = self.wallet.address,
            owner_address    = owner_address,
            chunk_id         = chunk_id,
            file_id          = file_id,
            bytes_stored     = size,
            duration_seconds = duration,
        )
        proof.node_public_key = self.wallet.public_key_b64
        proof.node_signature  = self.wallet.sign(proof.hash().encode())
        return proof

    async def _submit_proof_to_chain(self, proof: StorageProof) -> None:
        if not self.chain_endpoint or proof.proof_id in self._proofs_submitted:
            return
        try:
            async with httpx.AsyncClient(timeout=5) as http:
                resp = await http.post(
                    f"{self.chain_endpoint}/storage/proof",
                    json=proof.to_dict(),
                )
                if resp.status_code == 200 and resp.json().get("accepted"):
                    self._proofs_submitted.add(proof.proof_id)
                    bc = proof.reward_bc()
                    self._bc_earned += bc
                    logger.info(f"[StorageNode->Chain] Proof accepted +{bc:.8f} BC")
        except Exception as e:
            logger.debug(f"[StorageNode->Chain] Chain unreachable: {e}")

    def create_app(self) -> FastAPI:
        app = FastAPI(
            title       = "BorderStore Node",
            description = "Decentralised encrypted storage -- earn BC per GB stored",
            docs_url    = "/storage/docs",
            redoc_url   = None,
        )

        @app.post("/storage/store/{chunk_id}")
        async def store_chunk(chunk_id: str, request: Request):
            ciphertext = await request.body()
            if not ciphertext:
                raise HTTPException(400, "Empty chunk")
            meta_header = request.headers.get("X-Border-Meta", "{}")
            import json
            try:
                meta = json.loads(meta_header)
            except Exception:
                meta = {}
            ok = self.store_chunk(chunk_id, ciphertext, meta)
            if not ok:
                raise HTTPException(500, "Failed to store chunk")
            return {"stored": True, "chunk_id": chunk_id, "size": len(ciphertext)}

        @app.get("/storage/retrieve/{chunk_id}")
        async def retrieve_chunk(chunk_id: str):
            data = self.retrieve_chunk(chunk_id)
            if data is None:
                raise HTTPException(404, f"Chunk {chunk_id} not found")
            return Response(content=data, media_type="application/octet-stream")

        @app.post("/storage/challenge")
        async def handle_challenge(body: dict):
            try:
                challenge = StorageChallenge.from_dict(body)
            except Exception as e:
                raise HTTPException(400, f"Invalid challenge: {e}")
            if challenge.is_expired():
                raise HTTPException(400, "Challenge expired")
            response = self.respond_to_challenge(challenge)
            if response is None:
                raise HTTPException(404, f"Chunk {challenge.chunk_id} not found")
            meta       = self._chunk_metadata.get(challenge.chunk_id, {})
            owner      = meta.get("owner_address", "")
            file_id    = meta.get("file_id", "")
            ciphertext = self.retrieve_chunk(challenge.chunk_id)
            expected   = challenge.expected_response(ciphertext)
            proof = StorageProof.from_challenge(
                challenge     = challenge,
                node_address  = self.wallet.address,
                owner_address = owner,
                file_id       = file_id,
                bytes_stored  = len(ciphertext),
                response_hash = response,
                expected_hash = expected,
            )
            proof.node_public_key = self.wallet.public_key_b64
            proof.node_signature  = self.wallet.sign(proof.hash().encode())
            if self.chain_endpoint:
                asyncio.create_task(self._submit_proof_to_chain(proof))
            self._challenges_passed += 1
            return {
                "response_hash": response,
                "proof_id":      proof.proof_id,
                "passed":        response == expected,
            }

        @app.delete("/storage/chunk/{chunk_id}")
        async def delete_chunk(chunk_id: str):
            ok = self.delete_chunk(chunk_id)
            return {"deleted": ok, "chunk_id": chunk_id}

        @app.get("/storage/chunks")
        async def list_chunks():
            return {
                "chunks": list(self._chunks_stored),
                "count":  len(self._chunks_stored),
                "bytes":  self._bytes_stored,
            }

        @app.get("/storage/status")
        async def status():
            return {
                "node_id":           self.node_id,
                "wallet":            self.wallet.address,
                "region":            self.region,
                "capacity_gb":       self.capacity_gb,
                "used_bytes":        self._bytes_stored,
                "used_gb":           round(self._bytes_stored / (1024**3), 4),
                "chunks_stored":     len(self._chunks_stored),
                "challenges_passed": self._challenges_passed,
                "bc_earned":         round(self._bc_earned, 6),
                "uptime":            round(time.time() - self._start_time, 1),
                "price_bc_per_gb_day": self.price_bc_per_gb_day,
            }

        @app.get("/.border/storage")
        async def node_card():
            return {
                "border":            "0.1",
                "type":              "STORAGE",
                "node_id":           self.node_id,
                "endpoint":          self.endpoint,
                "region":            self.region,
                "capacity_gb":       self.capacity_gb,
                "used_gb":           round(self._bytes_stored / (1024**3), 4),
                "price_bc_per_gb_day": self.price_bc_per_gb_day,
                "wallet":            self.wallet.address,
            }

        @app.get("/storage/health")
        async def health():
            return {"status": "ok", "ts": int(time.time() * 1000)}

        return app

    def _index_path(self) -> Path:
        return self.storage_path / "index.json"

    def _save_index(self) -> None:
        import json
        data = {
            "node_id":  self.node_id,
            "chunks":   list(self._chunks_stored),
            "metadata": self._chunk_metadata,
            "bytes":    self._bytes_stored,
        }
        self._index_path().write_text(json.dumps(data))

    def _load_index(self) -> None:
        import json
        path = self._index_path()
        if not path.exists():
            return
        data = json.loads(path.read_text())
        self._chunks_stored  = set(data.get("chunks", []))
        self._chunk_metadata = data.get("metadata", {})
        self._bytes_stored   = data.get("bytes", 0)


def serve_storage(
    node_id:        Optional[str]  = None,
    host:           str            = "0.0.0.0",
    port:           int            = 9999,
    storage_path:   str            = "./border_storage",
    capacity_gb:    float          = 100.0,
    wallet_path:    Optional[str]  = None,
    chain_endpoint: Optional[str]  = None,
    region:         str            = "UNKNOWN",
) -> None:
    """Start a BorderStore node."""
    import uvicorn
    import hashlib as _h

    if not node_id:
        node_id = _h.sha256(f"snode-{time.time()}".encode()).hexdigest()[:16]

    wallet = None
    if wallet_path:
        try:
            from border.blockchain import BorderWallet
            wallet = BorderWallet.load(wallet_path)
        except Exception as e:
            logger.warning(f"Could not load wallet: {e}")

    if not wallet:
        from border.blockchain import BorderWallet
        wallet = BorderWallet.create()
        logger.info(f"[StorageNode] Created new wallet: {wallet.address}")

    node = BorderStorageNode(
        node_id        = node_id,
        wallet         = wallet,
        storage_path   = storage_path,
        capacity_gb    = capacity_gb,
        chain_endpoint = chain_endpoint,
        endpoint       = f"http://{host}:{port}",
        region         = region,
    )
    app = node.create_app()

    print(f"\nBorderStore Node")
    print(f"   Node ID  : {node_id}")
    print(f"   Wallet   : {wallet.address}")
    print(f"   Capacity : {capacity_gb}GB")
    print(f"   Storage  : {storage_path}")
    print(f"   Chain    : {chain_endpoint or 'not connected'}")
    print(f"   Earning  : {BC_PER_GB_PER_DAY} BC/GB/day (passive income)\n")

    uvicorn.run(app, host=host, port=port, log_level="warning")
