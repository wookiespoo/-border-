"""
BorderCompute
Decentralised GPU compute network powered by BorderCoin.

Every GPU job = a ComputeProof = BorderCoin earned.
No middleman. No platform fees. You own the hardware, you earn the coin.

Usage:
    # Start a market node
    from phantom.compute import BorderComputeNode, serve_compute
    serve_compute(chain_endpoint="http://localhost:7777", port=8888)

    # Run a worker daemon on your GPU rig
    from phantom.compute import WorkerDaemon
    from phantom.blockchain import BorderWallet
    wallet = BorderWallet.load("wallet.json")
    daemon = WorkerDaemon(wallet=wallet, market_endpoint="http://localhost:8888")
    daemon.run()

    # Submit a job as a client
    from phantom.compute import ComputeJob, JobType
    job = ComputeJob.create(
        job_type=JobType.INFERENCE,
        client_address="BC_...",
        model_id="llama3",
        input_data={"prompt": "Explain borderless internet"},
        max_price_bc=0.01,
    )
"""

from .job import (
    ComputeJob,
    ComputeProof,
    GPUSpec,
    JobType,
    JobStatus,
    ComputeType,
    BC_PER_COMPUTE_HOUR,
    BC_PER_GB_PROCESSED,
    MIN_STAKE_TO_WORK,
)
from .worker import BorderWorker, WorkerRegistry
from .market import ComputeMarket
from .node import BorderComputeNode, serve_compute
from .daemon import WorkerDaemon

__all__ = [
    "ComputeJob",
    "ComputeProof",
    "GPUSpec",
    "JobType",
    "JobStatus",
    "ComputeType",
    "BorderWorker",
    "WorkerRegistry",
    "ComputeMarket",
    "BorderComputeNode",
    "WorkerDaemon",
    "serve_compute",
    "BC_PER_COMPUTE_HOUR",
    "BC_PER_GB_PROCESSED",
    "MIN_STAKE_TO_WORK",
]
