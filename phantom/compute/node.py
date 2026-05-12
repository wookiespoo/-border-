"""
BorderCompute — Compute Node (FastAPI)
=======================================
The HTTP server that runs on a BorderCompute market node.

Routes:
  POST /compute/job            — Submit a compute job
  GET  /compute/job/{job_id}   — Get job status + result
  POST /compute/proof          — Worker submits completion proof
  POST /compute/worker         — Register as a GPU worker
  POST /compute/worker/{id}/heartbeat  — Keep worker alive
  GET  /compute/market         — List pending jobs (for workers)
  GET  /compute/workers        — List online workers
  GET  /compute/stats          — Network stats
  GET  /compute/proofs         — All proofs (for blockchain submission)

The compute node integrates with BorderChain:
  Every accepted ComputeProof is forwarded to the local BorderChain node,
  which mints BorderCoin for the worker automatically.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse

from .job import ComputeJob, ComputeProof, JobType, JobStatus
from .market import ComputeMarket
from .worker import BorderWorker

logger = logging.getLogger("border.compute.node")


class BorderComputeNode:
    """
    A BorderCompute market node.

    Run one of these alongside a BorderChain node.
    Workers register here, clients submit jobs here,
    proofs flow through here to the blockchain.

    Usage:
        from phantom.blockchain import BorderWallet
        from phantom.compute import BorderComputeNode

        wallet = BorderWallet.load("wallet.json")
        node   = BorderComputeNode(
            wallet=wallet,
            chain_endpoint="http://localhost:7777",
        )
        app = node.create_app()
        uvicorn.run(app, host="0.0.0.0", port=8888)
    """

    def __init__(
        self,
        node_id:        Optional[str] = None,
        chain_endpoint: Optional[str] = None,
        wallet=None,
    ):
        import hashlib
        self.node_id        = node_id or hashlib.sha256(f"cnode-{time.time()}".encode()).hexdigest()[:16]
        self.chain_endpoint = chain_endpoint
        self.wallet         = wallet
        self.market         = ComputeMarket()
        self._start_time    = time.time()
        self._submitted_proof_ids: set = set()

        if chain_endpoint:
            logger.info(f"[ComputeNode] Chain endpoint: {chain_endpoint}")
        else:
            logger.warning("[ComputeNode] No chain_endpoint — proofs won't auto-submit to blockchain")

    # ─────────────────────────────────────────────────────
    # Blockchain integration
    # ─────────────────────────────────────────────────────

    async def _submit_proof_to_chain(self, proof: ComputeProof) -> None:
        """Fire-and-forget: forward a compute proof to the BorderChain node."""
        if not self.chain_endpoint:
            return
        if proof.proof_id in self._submitted_proof_ids:
            return
        try:
            async with httpx.AsyncClient(timeout=5) as http:
                resp = await http.post(
                    f"{self.chain_endpoint}/compute/proof",
                    json=proof.to_dict(),
                )
                if resp.status_code == 200 and resp.json().get("accepted"):
                    self._submitted_proof_ids.add(proof.proof_id)
                    bc = proof.compute_reward_bc()
                    logger.info(
                        f"[ComputeNode→Chain] Proof accepted ✓ "
                        f"job={proof.job_id} +{bc:.6f} BC → {proof.worker_address[:16]}..."
                    )
                else:
                    logger.debug(f"[ComputeNode→Chain] Proof rejected: {resp.text}")
        except Exception as e:
            logger.debug(f"[ComputeNode→Chain] Chain unreachable: {e}")

    # ─────────────────────────────────────────────────────
    # App
    # ─────────────────────────────────────────────────────

    def create_app(self) -> FastAPI:
        app = FastAPI(
            title="BorderCompute Node",
            description="Decentralised GPU compute — earn BorderCoin for every job",
            docs_url="/compute/docs",
            redoc_url=None,
        )

        # ── Job submission ────────────────────────────────

        @app.post("/compute/job")
        async def submit_job(body: dict, background_tasks: BackgroundTasks):
            """Client submits a compute job."""
            try:
                job = ComputeJob.from_dict(body)
            except Exception as e:
                raise HTTPException(400, f"Invalid job format: {e}")

            accepted, reason = self.market.submit_job(job)
            if not accepted:
                raise HTTPException(400, reason)

            return {
                "accepted":      True,
                "job_id":        job.job_id,
                "status":        job.status,
                "worker":        job.worker_address,
                "estimated_bc":  job.max_price_bc,
            }

        # ── Job status ────────────────────────────────────

        @app.get("/compute/job/{job_id}")
        async def get_job(job_id: str):
            job = self.market.get_job(job_id)
            if not job:
                raise HTTPException(404, "Job not found")
            result = job.to_dict()
            proof  = self.market.get_proof(job_id)
            if proof:
                result["proof"]    = proof.to_dict()
                result["bc_earned"] = proof.compute_reward_bc()
            return result

        # ── Proof submission (worker → market) ────────────

        @app.post("/compute/proof")
        async def submit_proof(body: dict, background_tasks: BackgroundTasks):
            """Worker submits a compute proof after completing a job."""
            try:
                proof = ComputeProof.from_dict(body)
            except Exception as e:
                raise HTTPException(400, f"Invalid proof format: {e}")

            accepted, reason, bc_earned = self.market.submit_proof(proof)
            if not accepted:
                raise HTTPException(400, reason)

            # Forward proof to blockchain (non-blocking)
            background_tasks.add_task(self._submit_proof_to_chain, proof)

            return {
                "accepted":  True,
                "proof_id":  proof.proof_id,
                "bc_earned": bc_earned,
                "job_id":    proof.job_id,
            }

        # ── Worker registration ───────────────────────────

        @app.post("/compute/worker")
        async def register_worker(body: dict):
            """GPU node registers itself as a worker."""
            try:
                worker = BorderWorker.from_dict(body)
            except Exception as e:
                raise HTTPException(400, f"Invalid worker format: {e}")

            is_new = self.market.registry.register(worker)

            # Immediately try to assign any pending jobs
            self.market.retry_pending_jobs()

            return {
                "registered": True,
                "worker_id":  worker.worker_id,
                "is_new":     is_new,
                "gpu_count":  worker.gpu_count,
                "vram_gb":    worker.total_vram_gb,
            }

        # ── Worker heartbeat ──────────────────────────────

        @app.post("/compute/worker/{worker_id}/heartbeat")
        async def worker_heartbeat(worker_id: str):
            ok = self.market.registry.heartbeat(worker_id)
            if not ok:
                raise HTTPException(404, "Worker not found — re-register")

            # Try assigning pending jobs when a worker checks in
            self.market.retry_pending_jobs()
            return {"ok": True, "ts": int(time.time())}

        # ── Market (workers poll this for available jobs) ─

        @app.get("/compute/market")
        async def get_market():
            """Workers poll this to find available jobs."""
            pending = self.market.pending_jobs()
            return {
                "pending_jobs": [j.to_dict() for j in pending],
                "count":        len(pending),
            }

        # ── Workers list ──────────────────────────────────

        @app.get("/compute/workers")
        async def list_workers():
            workers = self.market.registry.all_online()
            return {
                "workers": [w.to_dict() for w in workers],
                "count":   len(workers),
            }

        # ── All proofs (for blockchain batch submission) ──

        @app.get("/compute/proofs")
        async def list_proofs():
            proofs = self.market.all_proofs()
            return {
                "proofs": [p.to_dict() for p in proofs],
                "count":  len(proofs),
            }

        # ── Stats ─────────────────────────────────────────

        @app.get("/compute/stats")
        async def stats():
            return {
                "node_id":      self.node_id,
                "uptime":       round(time.time() - self._start_time, 1),
                "chain":        self.chain_endpoint,
                **self.market.stats,
            }

        # ── Health ────────────────────────────────────────

        @app.get("/compute/health")
        async def health():
            return {"status": "ok", "ts": int(time.time() * 1000)}

        # ── Background cleanup ────────────────────────────

        @app.on_event("startup")
        async def start_background_tasks():
            asyncio.create_task(self._cleanup_loop())

        return app

    async def _cleanup_loop(self) -> None:
        """Periodically expire stale jobs and retry pending ones."""
        while True:
            await asyncio.sleep(30)
            expired = self.market.expire_stale_jobs()
            if expired:
                logger.info(f"[ComputeNode] Expired {expired} stale jobs")
            assigned = self.market.retry_pending_jobs()
            if assigned:
                logger.info(f"[ComputeNode] Assigned {assigned} pending jobs")


def serve_compute(
    node_id:        Optional[str] = None,
    host:           str           = "0.0.0.0",
    port:           int           = 8888,
    chain_endpoint: Optional[str] = None,
    wallet_path:    Optional[str] = None,
) -> None:
    """
    Start a BorderCompute market node.

    Args:
        node_id:        Unique node identifier
        host:           Bind address
        port:           Bind port
        chain_endpoint: URL of local BorderChain node (e.g. http://localhost:7777)
        wallet_path:    Path to BorderWallet JSON (optional)
    """
    import uvicorn

    wallet = None
    if wallet_path:
        try:
            from phantom.blockchain import BorderWallet
            wallet = BorderWallet.load(wallet_path)
            logger.info(f"[ComputeNode] Loaded wallet: {wallet.address}")
        except Exception as e:
            logger.warning(f"[ComputeNode] Could not load wallet: {e}")

    node = BorderComputeNode(
        node_id=node_id,
        chain_endpoint=chain_endpoint,
        wallet=wallet,
    )
    app = node.create_app()

    print(f"\n⚡ BorderCompute Node")
    print(f"   Node ID : {node.node_id}")
    print(f"   Endpoint: http://{host}:{port}")
    print(f"   Chain   : {chain_endpoint or 'not connected'}")
    print(f"   Docs    : http://{host}:{port}/compute/docs")
    print(f"   Workers connect → register GPUs → earn BC per job\n")

    uvicorn.run(app, host=host, port=port, log_level="warning")
