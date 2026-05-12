"""
BorderRender — Render Market

Routes jobs to workers with the right GPU VRAM and loaded model.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .job import RenderJob, RenderResult, RenderStatus, RenderType

logger = logging.getLogger("border.render.market")


@dataclass
class RenderWorker:
    worker_id:       str
    wallet_address:  str
    endpoint:        str
    model_ids:       List[str]
    total_vram_gb:   float
    stake_bc:        float
    frames_per_min:  float          = 2.0    # benchmark
    reputation:      float          = 0.0
    is_available:    bool           = True
    jobs_completed:  int            = 0
    total_frames:    int            = 0
    registered_at:   float          = field(default_factory=time.time)

    @property
    def score(self) -> float:
        return self.stake_bc * 0.5 + self.reputation + self.frames_per_min

    def can_serve(self, job: RenderJob) -> bool:
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
               stake_bc: float = 10.0, frames_per_min: float = 2.0) -> "RenderWorker":
        return cls(
            worker_id      = uuid.uuid4().hex[:16],
            wallet_address = wallet_address,
            endpoint       = endpoint,
            model_ids      = model_ids,
            total_vram_gb  = total_vram_gb,
            stake_bc       = stake_bc,
            frames_per_min = frames_per_min,
        )


class RenderMarket:
    def __init__(self):
        self._workers: Dict[str, RenderWorker]  = {}
        self._jobs:    Dict[str, RenderJob]      = {}
        self._results: Dict[str, RenderResult]   = {}
        self._total_bc_paid:    float = 0.0
        self._total_frames:     int   = 0

    def register_worker(self, worker: RenderWorker) -> None:
        self._workers[worker.worker_id] = worker
        logger.info(f"[RenderMarket] Worker: {worker.worker_id[:8]} "
                    f"vram={worker.total_vram_gb}GB models={worker.model_ids}")

    def available_workers(self) -> List[RenderWorker]:
        return [w for w in self._workers.values() if w.is_available]

    def submit_job(self, job: RenderJob) -> Tuple[bool, str]:
        self._jobs[job.job_id] = job
        worker = self._best_worker(job)
        if worker is None:
            logger.warning(f"[RenderMarket] No worker for model={job.model_id}")
            return False, "no_worker_available"
        job.worker_address  = worker.wallet_address
        job.status          = RenderStatus.ASSIGNED
        worker.is_available = False
        logger.info(f"[RenderMarket] Job {job.job_id[:8]} → worker {worker.worker_id[:8]} "
                    f"type={job.render_type} model={job.model_id}")
        return True, worker.worker_id

    def _best_worker(self, job: RenderJob) -> Optional[RenderWorker]:
        candidates = [w for w in self._workers.values() if w.can_serve(job)]
        if not candidates:
            return None
        return max(candidates, key=lambda w: w.score)

    def submit_result(self, result: RenderResult) -> Tuple[bool, str, float]:
        job = self._jobs.get(result.job_id)
        if job is None:
            return False, "job_not_found", 0.0

        job.status = RenderStatus.COMPLETED
        self._results[result.result_id] = result

        worker = self._worker_by_address(result.worker_address)
        if worker:
            worker.is_available   = True
            worker.jobs_completed += 1
            worker.total_frames   += result.frames_rendered

        self._total_bc_paid += result.reward_bc
        self._total_frames  += result.frames_rendered

        logger.info(f"[RenderMarket] Result {result.result_id[:8]} | "
                    f"{result.frames_rendered} frames | +{result.reward_bc:.6f} BC")
        return True, "accepted", result.reward_bc

    def _worker_by_address(self, address: str) -> Optional[RenderWorker]:
        for w in self._workers.values():
            if w.wallet_address == address:
                return w
        return None

    def get_job(self, job_id: str) -> Optional[RenderJob]:
        return self._jobs.get(job_id)

    def get_result(self, job_id: str) -> Optional[RenderResult]:
        for r in self._results.values():
            if r.job_id == job_id:
                return r
        return None

    @property
    def stats(self) -> dict:
        return {
            "workers_registered": len(self._workers),
            "workers_available":  len(self.available_workers()),
            "jobs_total":         len(self._jobs),
            "jobs_completed":     sum(1 for j in self._jobs.values()
                                      if j.status == RenderStatus.COMPLETED),
            "total_frames":       self._total_frames,
            "total_bc_paid":      round(self._total_bc_paid, 8),
            "models_available":   list({m for w in self._workers.values()
                                        for m in w.model_ids}),
        }

    def retry_pending_jobs(self) -> int:
        count = 0
        for job in self._jobs.values():
            if job.status == RenderStatus.PENDING:
                worker = self._best_worker(job)
                if worker:
                    job.worker_address  = worker.wallet_address
                    job.status          = RenderStatus.ASSIGNED
                    worker.is_available = False
                    count += 1
        return count
