"""
BorderCompute — Job Market
============================
The matching engine that connects clients with GPU workers.

Flow:
  1. Client submits a ComputeJob → market queues it
  2. Market finds best available worker (by score)
  3. Worker is assigned, job status → ASSIGNED
  4. Worker completes job, submits ComputeProof
  5. Market records completion, triggers BC payment
  6. Worker reputation + earnings updated
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Dict, List, Optional, Tuple

from .job import ComputeJob, ComputeProof, JobStatus, JobType
from .worker import BorderWorker, WorkerRegistry

logger = logging.getLogger("border.compute.market")


class ComputeMarket:
    """
    In-memory job market for a BorderComputeNode.

    Handles the full lifecycle:
      submit → assign → complete/fail → pay
    """

    def __init__(self):
        self.registry   = WorkerRegistry()
        self._jobs:     Dict[str, ComputeJob]    = {}
        self._proofs:   Dict[str, ComputeProof]  = {}  # job_id → proof
        self._payments: List[dict]               = []  # payment log

    # ─────────────────────────────────────────────────────
    # Job submission
    # ─────────────────────────────────────────────────────

    def submit_job(self, job: ComputeJob) -> Tuple[bool, str]:
        """
        Accept a job into the market queue.
        Returns (accepted, reason).
        """
        if job.job_id in self._jobs:
            return False, "Duplicate job_id"

        if job.max_price_bc <= 0:
            return False, "max_price_bc must be > 0"

        self._jobs[job.job_id] = job
        logger.info(f"[Market] Job queued: {job.job_id} type={job.job_type} price≤{job.max_price_bc} BC")

        # Immediately try to assign
        self._try_assign(job)
        return True, "accepted"

    def _try_assign(self, job: ComputeJob) -> bool:
        """Find the best worker and assign the job. Returns True if assigned."""
        if job.status != JobStatus.PENDING:
            return False

        worker = self.registry.best_worker_for(job.min_vram_gb, job.max_price_bc)
        if not worker:
            logger.debug(f"[Market] No worker available for {job.job_id} (need {job.min_vram_gb}GB VRAM)")
            return False

        job.worker_address = worker.wallet_address
        job.status         = JobStatus.ASSIGNED
        job.assigned_at    = time.time()
        worker.is_available = False

        logger.info(
            f"[Market] Assigned {job.job_id} → worker {worker.worker_id} "
            f"({worker.gpu_specs[0].name if worker.gpu_specs else 'unknown'})"
        )
        return True

    # ─────────────────────────────────────────────────────
    # Proof submission (worker completed a job)
    # ─────────────────────────────────────────────────────

    def submit_proof(self, proof: ComputeProof) -> Tuple[bool, str, float]:
        """
        Worker submits a compute proof after completing a job.
        Returns (accepted, reason, bc_earned).
        """
        job = self._jobs.get(proof.job_id)
        if not job:
            return False, "Unknown job_id", 0.0

        if job.status != JobStatus.ASSIGNED:
            return False, f"Job not in ASSIGNED state (state={job.status})", 0.0

        if job.worker_address != proof.worker_address:
            return False, "Proof worker_address does not match assigned worker", 0.0

        if proof.job_id in self._proofs:
            return False, "Proof already submitted for this job", 0.0

        # Verify worker signature when public key is present
        if proof.worker_public_key:
            if not proof.verify_signature():
                return False, "Invalid worker signature", 0.0

        # Verify proof hashes match job input
        if proof.input_hash != job.input_hash():
            return False, "input_hash mismatch — proof does not match job", 0.0

        # Calculate earnings
        bc_earned = proof.compute_reward_bc()
        if bc_earned > job.max_price_bc * 2:
            # Cap at 2x max_price to prevent abuse
            bc_earned = job.max_price_bc

        # Update job
        job.status       = JobStatus.COMPLETED
        job.completed_at = time.time()

        # Store proof
        self._proofs[proof.job_id] = proof

        # Update worker stats
        worker = self._find_worker_by_address(proof.worker_address)
        if worker:
            worker.record_completion(bc_earned)

        # Log payment
        self._payments.append({
            "job_id":         proof.job_id,
            "worker_address": proof.worker_address,
            "client_address": proof.client_address,
            "bc_earned":      bc_earned,
            "timestamp":      time.time(),
        })

        logger.info(
            f"[Market] Job {proof.job_id} completed ✓ "
            f"worker={proof.worker_address[:16]}... earned={bc_earned:.6f} BC"
        )
        return True, "accepted", bc_earned

    # ─────────────────────────────────────────────────────
    # Job failure / expiry
    # ─────────────────────────────────────────────────────

    def fail_job(self, job_id: str, reason: str = "worker_failed") -> bool:
        """Mark a job as failed and free the worker."""
        job = self._jobs.get(job_id)
        if not job:
            return False

        job.status = JobStatus.FAILED
        job.error  = reason

        worker = self._find_worker_by_address(job.worker_address)
        if worker:
            worker.record_failure()

        logger.warning(f"[Market] Job {job_id} FAILED: {reason}")

        # Re-queue as a new pending job for retry
        retry = ComputeJob.create(
            job_type=job.job_type,
            client_address=job.client_address,
            model_id=job.model_id,
            input_data=job.input_data,
            max_price_bc=job.max_price_bc,
            min_vram_gb=job.min_vram_gb,
        )
        self._jobs[retry.job_id] = retry
        self._try_assign(retry)
        logger.info(f"[Market] Retrying as {retry.job_id}")
        return True

    def expire_stale_jobs(self) -> int:
        """Expire jobs that timed out. Returns count of expired jobs."""
        expired = 0
        for job in list(self._jobs.values()):
            if job.is_expired():
                job.status = JobStatus.EXPIRED
                expired += 1
        return expired

    def retry_pending_jobs(self) -> int:
        """Try to assign any pending jobs that weren't assigned yet."""
        assigned = 0
        for job in self._jobs.values():
            if job.status == JobStatus.PENDING:
                if self._try_assign(job):
                    assigned += 1
        return assigned

    # ─────────────────────────────────────────────────────
    # Queries
    # ─────────────────────────────────────────────────────

    def get_job(self, job_id: str) -> Optional[ComputeJob]:
        return self._jobs.get(job_id)

    def get_proof(self, job_id: str) -> Optional[ComputeProof]:
        return self._proofs.get(job_id)

    def pending_jobs(self) -> List[ComputeJob]:
        return [j for j in self._jobs.values() if j.status == JobStatus.PENDING]

    def assigned_jobs(self) -> List[ComputeJob]:
        return [j for j in self._jobs.values() if j.status == JobStatus.ASSIGNED]

    def completed_jobs(self) -> List[ComputeJob]:
        return [j for j in self._jobs.values() if j.status == JobStatus.COMPLETED]

    def all_proofs(self) -> List[ComputeProof]:
        return list(self._proofs.values())

    def unsubmitted_proofs(self, submitted_ids: set) -> List[ComputeProof]:
        """Return proofs not yet recorded on the blockchain."""
        return [p for p in self._proofs.values() if p.proof_id not in submitted_ids]

    # ─────────────────────────────────────────────────────
    # Stats
    # ─────────────────────────────────────────────────────

    @property
    def stats(self) -> dict:
        jobs    = list(self._jobs.values())
        proofs  = list(self._proofs.values())
        total_bc = sum(p["bc_earned"] for p in self._payments)

        return {
            "jobs_total":     len(jobs),
            "jobs_pending":   sum(1 for j in jobs if j.status == JobStatus.PENDING),
            "jobs_assigned":  sum(1 for j in jobs if j.status == JobStatus.ASSIGNED),
            "jobs_completed": sum(1 for j in jobs if j.status == JobStatus.COMPLETED),
            "jobs_failed":    sum(1 for j in jobs if j.status == JobStatus.FAILED),
            "jobs_expired":   sum(1 for j in jobs if j.status == JobStatus.EXPIRED),
            "proofs_total":   len(proofs),
            "total_bc_paid":  round(total_bc, 6),
            "total_compute_seconds": round(sum(p.compute_seconds for p in proofs), 2),
            "total_bytes_processed": sum(p.bytes_processed for p in proofs),
            **self.registry.stats,
        }

    # ─────────────────────────────────────────────────────
    # Internal helpers
    # ─────────────────────────────────────────────────────

    def _find_worker_by_address(self, address: Optional[str]) -> Optional[BorderWorker]:
        if not address:
            return None
        for w in self.registry._workers.values():
            if w.wallet_address == address:
                return w
        return None
