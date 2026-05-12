"""
BorderCompute — Worker Registry
=================================
Workers are GPU operators who register their hardware,
accept compute jobs, and earn BorderCoin.

A Worker:
  - Has a BorderWallet (identity + earnings address)
  - Declares GPU specs (what jobs they can handle)
  - Stakes BC (higher stake = higher trust = more jobs)
  - Has a reachable endpoint (where the daemon listens)
  - Builds a reputation score over time
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .job import GPUSpec, ComputeType, MIN_STAKE_TO_WORK


# ─────────────────────────────────────────────────────────
# Worker
# ─────────────────────────────────────────────────────────

@dataclass
class BorderWorker:
    """
    A registered GPU node on the BorderCompute network.

    Workers stake BC as collateral — if they accept a job and
    fail to deliver, they lose their stake. Honest workers
    build reputation and attract higher-value jobs.
    """
    worker_id:      str
    wallet_address: str             # BC address — where earnings go
    endpoint:       str             # http://host:port — where to send jobs
    gpu_specs:      List[GPUSpec]   # All GPUs on this machine
    stake_bc:       float           # BC staked as collateral
    registered_at:  float           = field(default_factory=time.time)
    last_seen:      float           = field(default_factory=time.time)
    jobs_completed: int             = 0
    jobs_failed:    int             = 0
    total_earned_bc: float          = 0.0
    is_available:   bool            = True
    region:         str             = "UNKNOWN"

    # ── derived properties ────────────────────────────────

    @property
    def reputation(self) -> float:
        """
        0.0 – 1.0 reputation score.
        New workers start at 0.5.
        Rises with completed jobs, falls with failures.
        """
        total = self.jobs_completed + self.jobs_failed
        if total == 0:
            return 0.5
        return round(self.jobs_completed / total, 4)

    @property
    def max_vram_gb(self) -> int:
        """Largest single GPU VRAM available."""
        if not self.gpu_specs:
            return 0
        return max(g.vram_gb for g in self.gpu_specs)

    @property
    def total_vram_gb(self) -> int:
        """Sum of all GPU VRAM — for parallel jobs."""
        return sum(g.vram_gb for g in self.gpu_specs)

    @property
    def gpu_count(self) -> int:
        return len(self.gpu_specs)

    @property
    def is_trusted(self) -> bool:
        """Worker has enough stake and decent reputation."""
        return self.stake_bc >= MIN_STAKE_TO_WORK and self.reputation >= 0.3

    @property
    def is_online(self) -> bool:
        """Worker checked in within the last 60 seconds."""
        return time.time() - self.last_seen < 60

    # ── factory ──────────────────────────────────────────

    @classmethod
    def create(
        cls,
        wallet_address: str,
        endpoint:       str,
        gpu_specs:      Optional[List[GPUSpec]] = None,
        stake_bc:       float = 0.0,
        region:         str   = "UNKNOWN",
    ) -> "BorderWorker":
        if gpu_specs is None:
            gpu_specs = GPUSpec.detect()
        return cls(
            worker_id=f"worker_{uuid.uuid4().hex[:12]}",
            wallet_address=wallet_address,
            endpoint=endpoint,
            gpu_specs=gpu_specs,
            stake_bc=stake_bc,
            region=region,
        )

    # ── matching ──────────────────────────────────────────

    def can_handle(self, min_vram_gb: int, max_price_bc: float) -> bool:
        """Can this worker accept a job with the given requirements?"""
        return (
            self.is_available
            and self.is_online
            and self.is_trusted
            and self.max_vram_gb >= min_vram_gb
            and max_price_bc >= 0  # worker will quote actual price
        )

    def score_for_job(self, min_vram_gb: int, max_price_bc: float) -> float:
        """
        Priority score for job assignment.
        Higher is better. Balances reputation, stake, and availability.
        """
        if not self.can_handle(min_vram_gb, max_price_bc):
            return -1.0
        vram_headroom = (self.max_vram_gb - min_vram_gb) / max(self.max_vram_gb, 1)
        return (
            self.reputation * 0.5
            + min(self.stake_bc / 100, 0.3)
            + vram_headroom * 0.2
        )

    # ── heartbeat ─────────────────────────────────────────

    def heartbeat(self) -> None:
        self.last_seen = time.time()

    def record_completion(self, earned_bc: float) -> None:
        self.jobs_completed += 1
        self.total_earned_bc += earned_bc
        self.is_available = True

    def record_failure(self) -> None:
        self.jobs_failed += 1
        self.is_available = True

    # ── serialisation ─────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "worker_id":       self.worker_id,
            "wallet_address":  self.wallet_address,
            "endpoint":        self.endpoint,
            "gpu_specs":       [g.to_dict() for g in self.gpu_specs],
            "stake_bc":        self.stake_bc,
            "registered_at":   self.registered_at,
            "last_seen":       self.last_seen,
            "jobs_completed":  self.jobs_completed,
            "jobs_failed":     self.jobs_failed,
            "total_earned_bc": self.total_earned_bc,
            "is_available":    self.is_available,
            "region":          self.region,
            "reputation":      self.reputation,
            "max_vram_gb":     self.max_vram_gb,
            "total_vram_gb":   self.total_vram_gb,
            "gpu_count":       self.gpu_count,
            "is_online":       self.is_online,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "BorderWorker":
        return cls(
            worker_id=d["worker_id"],
            wallet_address=d["wallet_address"],
            endpoint=d["endpoint"],
            gpu_specs=[GPUSpec.from_dict(g) for g in d["gpu_specs"]],
            stake_bc=d["stake_bc"],
            registered_at=d["registered_at"],
            last_seen=d["last_seen"],
            jobs_completed=d["jobs_completed"],
            jobs_failed=d["jobs_failed"],
            total_earned_bc=d["total_earned_bc"],
            is_available=d["is_available"],
            region=d.get("region", "UNKNOWN"),
        )


# ─────────────────────────────────────────────────────────
# Worker Registry
# ─────────────────────────────────────────────────────────

class WorkerRegistry:
    """
    In-memory registry of all workers known to a BorderComputeNode.
    Handles registration, heartbeats, and job matching.
    """

    def __init__(self):
        self._workers: Dict[str, BorderWorker] = {}

    def register(self, worker: BorderWorker) -> bool:
        """Register or update a worker. Returns True if new."""
        is_new = worker.worker_id not in self._workers
        worker.heartbeat()
        self._workers[worker.worker_id] = worker
        return is_new

    def heartbeat(self, worker_id: str) -> bool:
        """Update last_seen for a worker. Returns False if unknown."""
        if worker_id not in self._workers:
            return False
        self._workers[worker_id].heartbeat()
        return True

    def get(self, worker_id: str) -> Optional[BorderWorker]:
        return self._workers.get(worker_id)

    def best_worker_for(self, min_vram_gb: int, max_price_bc: float) -> Optional[BorderWorker]:
        """Find the highest-scoring available worker for a job."""
        candidates = [
            (w.score_for_job(min_vram_gb, max_price_bc), w)
            for w in self._workers.values()
        ]
        candidates = [(s, w) for s, w in candidates if s >= 0]
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]

    def all_online(self) -> List[BorderWorker]:
        return [w for w in self._workers.values() if w.is_online]

    def all_available(self) -> List[BorderWorker]:
        return [w for w in self._workers.values() if w.is_available and w.is_online]

    @property
    def stats(self) -> dict:
        workers = list(self._workers.values())
        online  = self.all_online()
        avail   = self.all_available()
        return {
            "total_workers":     len(workers),
            "online_workers":    len(online),
            "available_workers": len(avail),
            "total_vram_gb":     sum(w.total_vram_gb for w in online),
            "total_gpus":        sum(w.gpu_count for w in online),
            "total_jobs_done":   sum(w.jobs_completed for w in workers),
            "total_bc_paid":     round(sum(w.total_earned_bc for w in workers), 6),
        }
