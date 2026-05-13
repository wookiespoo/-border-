"""
Tests for border.compute — job, worker, market
"""
import time
import uuid
import pytest

from border.compute.job import (
    ComputeType,
    ComputeJob, ComputeProof, GPUSpec, JobType, JobStatus,
    MIN_STAKE_TO_WORK,
)
from border.compute.worker import BorderWorker, WorkerRegistry
from border.compute.market import ComputeMarket


# ─────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────

def make_gpu(name="RTX 4090", vram_gb=24) -> GPUSpec:
    return GPUSpec(gpu_id=f"gpu_{uuid.uuid4().hex[:8]}", name=name,
                   vram_gb=vram_gb, compute_type=ComputeType.CUDA)


def make_worker(vram_gb=24, stake_bc=10.0, available=True) -> BorderWorker:
    w = BorderWorker.create(
        wallet_address="BC_worker_" + "a" * 32,
        endpoint="http://localhost:8080",
        gpu_specs=[make_gpu(vram_gb=vram_gb)],
        stake_bc=stake_bc,
    )
    w.is_available = available
    w.heartbeat()
    return w


def make_job(max_price_bc=1.0, min_vram_gb=8) -> ComputeJob:
    return ComputeJob.create(
        job_type=JobType.INFERENCE,
        client_address="BC_client_" + "b" * 32,
        model_id="llama-3-8b",
        input_data={"prompt": "hello world"},
        max_price_bc=max_price_bc,
        min_vram_gb=min_vram_gb,
    )


# ─────────────────────────────────────────────────────────
# GPUSpec
# ─────────────────────────────────────────────────────────

class TestGPUSpec:
    def test_roundtrip(self):
        g = make_gpu()
        assert GPUSpec.from_dict(g.to_dict()).vram_gb == g.vram_gb

    def test_vram_stored(self):
        g = make_gpu(vram_gb=48)
        assert g.vram_gb == 48


# ─────────────────────────────────────────────────────────
# ComputeJob
# ─────────────────────────────────────────────────────────

class TestComputeJob:
    def test_create_generates_id(self):
        job = make_job()
        assert job.job_id.startswith("job_")

    def test_initial_status_pending(self):
        assert make_job().status == JobStatus.PENDING

    def test_input_hash_deterministic(self):
        job = make_job()
        assert job.input_hash() == job.input_hash()

    def test_different_inputs_different_hash(self):
        j1 = ComputeJob.create(job_type=JobType.INFERENCE, client_address="BC_" + "a" * 32,
                               model_id="m", input_data={"x": 1}, max_price_bc=1.0, min_vram_gb=4)
        j2 = ComputeJob.create(job_type=JobType.INFERENCE, client_address="BC_" + "a" * 32,
                               model_id="m", input_data={"x": 2}, max_price_bc=1.0, min_vram_gb=4)
        assert j1.input_hash() != j2.input_hash()

    def test_not_expired_when_fresh(self):
        assert not make_job().is_expired()

    def test_roundtrip(self):
        job = make_job()
        assert ComputeJob.from_dict(job.to_dict()).job_id == job.job_id


# ─────────────────────────────────────────────────────────
# BorderWorker
# ─────────────────────────────────────────────────────────

class TestBorderWorker:
    def test_initial_reputation(self):
        w = make_worker()
        assert w.reputation == 0.5

    def test_reputation_rises_with_completions(self):
        w = make_worker()
        w.record_completion(0.5)
        w.record_completion(0.5)
        assert w.reputation > 0.5

    def test_reputation_falls_with_failures(self):
        w = make_worker()
        w.record_completion(0.5)
        w.record_failure()
        assert w.reputation < 1.0

    def test_is_trusted_with_sufficient_stake(self):
        w = make_worker(stake_bc=MIN_STAKE_TO_WORK + 1)
        w.record_completion(1.0)
        assert w.is_trusted

    def test_not_trusted_without_stake(self):
        w = make_worker(stake_bc=0.0)
        assert not w.is_trusted

    def test_is_online_after_heartbeat(self):
        w = make_worker()
        w.heartbeat()
        assert w.is_online

    def test_can_handle_matching_vram(self):
        w = make_worker(vram_gb=24, stake_bc=10.0)
        w.record_completion(1.0)
        assert w.can_handle(min_vram_gb=8, max_price_bc=1.0)

    def test_cannot_handle_insufficient_vram(self):
        w = make_worker(vram_gb=4, stake_bc=10.0)
        assert not w.can_handle(min_vram_gb=16, max_price_bc=1.0)

    def test_roundtrip(self):
        w = make_worker()
        w2 = BorderWorker.from_dict(w.to_dict())
        assert w2.worker_id == w.worker_id
        assert w2.wallet_address == w.wallet_address


# ─────────────────────────────────────────────────────────
# WorkerRegistry
# ─────────────────────────────────────────────────────────

class TestWorkerRegistry:
    def test_register_new(self):
        reg = WorkerRegistry()
        w = make_worker()
        assert reg.register(w) is True

    def test_register_update_existing(self):
        reg = WorkerRegistry()
        w = make_worker()
        reg.register(w)
        assert reg.register(w) is False

    def test_heartbeat_known_worker(self):
        reg = WorkerRegistry()
        w = make_worker()
        reg.register(w)
        assert reg.heartbeat(w.worker_id) is True

    def test_heartbeat_unknown_worker(self):
        reg = WorkerRegistry()
        assert reg.heartbeat("nonexistent") is False

    def test_best_worker_returns_highest_score(self):
        reg = WorkerRegistry()
        w_low = make_worker(vram_gb=8, stake_bc=1.0)
        w_low.record_completion(1.0)
        w_high = make_worker(vram_gb=24, stake_bc=50.0)
        w_high.record_completion(1.0)
        w_high.record_completion(1.0)
        reg.register(w_low)
        reg.register(w_high)
        best = reg.best_worker_for(min_vram_gb=4, max_price_bc=1.0)
        assert best is not None
        assert best.worker_id == w_high.worker_id

    def test_no_worker_if_vram_too_low(self):
        reg = WorkerRegistry()
        w = make_worker(vram_gb=4, stake_bc=10.0)
        w.record_completion(1.0)
        reg.register(w)
        assert reg.best_worker_for(min_vram_gb=24, max_price_bc=1.0) is None


# ─────────────────────────────────────────────────────────
# ComputeMarket
# ─────────────────────────────────────────────────────────

class TestComputeMarket:
    def _market_with_worker(self, vram_gb=24, stake_bc=10.0):
        market = ComputeMarket()
        w = make_worker(vram_gb=vram_gb, stake_bc=stake_bc)
        w.record_completion(1.0)
        market.registry.register(w)
        return market, w

    def test_submit_job_accepted(self):
        market, _ = self._market_with_worker()
        ok, reason = market.submit_job(make_job())
        assert ok, reason

    def test_duplicate_job_rejected(self):
        market, _ = self._market_with_worker()
        job = make_job()
        market.submit_job(job)
        ok, reason = market.submit_job(job)
        assert not ok
        assert "Duplicate" in reason

    def test_zero_price_rejected(self):
        market, _ = self._market_with_worker()
        ok, reason = market.submit_job(make_job(max_price_bc=0.0))
        assert not ok

    def test_job_auto_assigned_when_worker_available(self):
        market, _ = self._market_with_worker()
        job = make_job()
        market.submit_job(job)
        assert market.get_job(job.job_id).status == JobStatus.ASSIGNED

    def test_job_stays_pending_when_no_worker(self):
        market = ComputeMarket()
        job = make_job()
        market.submit_job(job)
        assert market.get_job(job.job_id).status == JobStatus.PENDING

    def test_submit_proof_completes_job(self):
        market, worker = self._market_with_worker()
        job = make_job()
        market.submit_job(job)
        stored_job = market.get_job(job.job_id)
        assert stored_job.status == JobStatus.ASSIGNED

        proof = ComputeProof.from_job(
            job=stored_job,
            worker_address=worker.wallet_address,
            gpu_spec=worker.gpu_specs[0],
            compute_seconds=10.0,
            output_hash="abc123",
            price_bc=0.5,
        )
        ok, reason, earned = market.submit_proof(proof)
        assert ok, reason
        assert earned > 0
        assert market.get_job(job.job_id).status == JobStatus.COMPLETED

    def test_proof_wrong_worker_rejected(self):
        market, worker = self._market_with_worker()
        job = make_job()
        market.submit_job(job)
        stored_job = market.get_job(job.job_id)
        proof = ComputeProof.from_job(
            job=stored_job,
            worker_address="BC_wrong_" + "x" * 32,
            gpu_spec=worker.gpu_specs[0],
            compute_seconds=10.0,
            output_hash="abc123",
            price_bc=0.5,
        )
        ok, reason, _ = market.submit_proof(proof)
        assert not ok
        assert "worker_address" in reason

    def test_stats_totals(self):
        market, _ = self._market_with_worker()
        market.submit_job(make_job())
        s = market.stats
        assert s["jobs_total"] >= 1
