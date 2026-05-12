"""
BorderInfer — Inference Market

Routes jobs to the best available worker that has the requested model loaded.
Scoring: prefer lowest latency × price, break ties by reputation score.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .job import InferJob, InferResult, InferStatus

logger = logging.getLogger("border.infer.market")


@dataclass
class InferWorker:
    worker_id:      str
    wallet_address: str
    endpoint:       str
    model_ids:      List[str]      # models this worker has loaded
    total_vram_gb:  float
    stake_bc:       float
    tokens_per_s:   float          = 30.0
    reputation:     float          = 0.0
    is_available:   bool           = True
    jobs_completed: int            = 0
    total_tokens:   int            = 0
    registered_at:  float          = field(default_factory=time.time)

    @property
    def score(self) -> float:
        # Higher stake + reputation = higher score; penalise slow workers
        return self.stake_bc * 0.5 + self.reputation + self.tokens_per_s * 0.1

    def can_serve(self, job: InferJob) -> bool:
        if not self.is_available:
            return False
        if job.model_id not in self.model_ids:
            return False
        if self.total_vram_gb < job.min_vram_gb:
            return False
        return True

    @classmethod
    def create(cls, wallet_address: str, endpoint: str,
               model_ids: List[str], total_vram_gb: float,
               stake_bc: float = 5.0, tokens_per_s: float = 30.0) -> "InferWorker":
        return cls(
            worker_id      = uuid.uuid4().hex[:16],
            wallet_address = wallet_address,
            endpoint       = endpoint,
            model_ids      = model_ids,
            total_vram_gb  = total_vram_gb,
            stake_bc       = stake_bc,
            tokens_per_s   = tokens_per_s,
        )


class InferMarket:
    def __init__(self):
        self._workers:  Dict[str, InferWorker]  = {}
        self._jobs:     Dict[str, InferJob]      = {}
        self._results:  Dict[str, InferResult]   = {}
        self._total_bc_paid:     float = 0.0
        self._total_tokens:      int   = 0

    # ── Worker management ─────────────────────────────────
    def register_worker(self, worker: InferWorker) -> None:
        self._workers[worker.worker_id] = worker
        logger.info(f"[InferMarket] Worker registered: {worker.worker_id} "
                    f"models={worker.model_ids} vram={worker.total_vram_gb}GB")

    def heartbeat(self, worker_id: str) -> bool:
        if worker_id not in self._workers:
            return False
        self._workers[worker_id].is_available = True
        return True

    def available_workers(self) -> List[InferWorker]:
        return [w for w in self._workers.values() if w.is_available]

    # ── Job submission ────────────────────────────────────
    def submit_job(self, job: InferJob) -> Tuple[bool, str]:
        self._jobs[job.job_id] = job
        worker = self._best_worker(job)
        if worker is None:
            logger.warning(f"[InferMarket] No worker for model={job.model_id}")
            return False, "no_worker_available"
        job.worker_address = worker.wallet_address
        job.status         = InferStatus.ASSIGNED
        worker.is_available = False
        logger.info(f"[InferMarket] Job {job.job_id[:8]} → worker {worker.worker_id[:8]} model={job.model_id}")
        return True, worker.worker_id

    def _best_worker(self, job: InferJob) -> Optional[InferWorker]:
        candidates = [w for w in self._workers.values() if w.can_serve(job)]
        if not candidates:
            return None
        return max(candidates, key=lambda w: w.score)

    # ── Result submission ─────────────────────────────────
    def submit_result(self, result: InferResult) -> Tuple[bool, str, float]:
        job = self._jobs.get(result.job_id)
        if job is None:
            return False, "job_not_found", 0.0

        job.status = InferStatus.COMPLETED
        self._results[result.result_id] = result

        # Credit worker
        worker = self._worker_by_address(result.worker_address)
        if worker:
            worker.is_available  = True
            worker.jobs_completed += 1
            worker.total_tokens  += result.total_tokens

        self._total_bc_paid += result.reward_bc
        self._total_tokens  += result.total_tokens

        logger.info(f"[InferMarket] Result {result.result_id[:8]} | "
                    f"{result.total_tokens} tokens | +{result.reward_bc:.6f} BC")
        return True, "accepted", result.reward_bc

    def _worker_by_address(self, address: str) -> Optional[InferWorker]:
        for w in self._workers.values():
            if w.wallet_address == address:
                return w
        return None

    def get_job(self, job_id: str) -> Optional[InferJob]:
        return self._jobs.get(job_id)

    def get_result(self, job_id: str) -> Optional[InferResult]:
        for r in self._results.values():
            if r.job_id == job_id:
                return r
        return None

    # ── Stats ─────────────────────────────────────────────
    @property
    def stats(self) -> dict:
        return {
            "workers_registered": len(self._workers),
            "workers_available":  len(self.available_workers()),
            "jobs_total":         len(self._jobs),
            "jobs_completed":     sum(1 for j in self._jobs.values()
                                      if j.status == InferStatus.COMPLETED),
            "total_tokens":       self._total_tokens,
            "total_bc_paid":      round(self._total_bc_paid, 8),
            "models_available":   list({m for w in self._workers.values()
                                        for m in w.model_ids}),
        }

    def retry_pending_jobs(self) -> int:
        """Re-attempt assignment for all PENDING jobs. Returns number newly assigned."""
        count = 0
        for job in self._jobs.values():
            if job.status == InferStatus.PENDING:
                worker = self._best_worker(job)
                if worker:
                    job.worker_address  = worker.wallet_address
                    job.status          = InferStatus.ASSIGNED
                    worker.is_available = False
                    count += 1
        return count
