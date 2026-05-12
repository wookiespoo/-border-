#!/usr/bin/env python3
"""
BorderCompute Demo
==================
Full end-to-end demo of the BorderCompute GPU network.

What this shows:
  1. Create wallets (client + worker)
  2. Worker registers GPU specs (RX 580 x12, RTX 3080, RTX 3060, GTX 1080 Ti)
  3. Client submits 4 jobs (inference, render, train, custom)
  4. Market matches jobs to best worker
  5. Worker "runs" jobs, submits ComputeProofs
  6. Proofs go into BorderChain
  7. Worker earns BorderCoin automatically
  8. Chain validates everything

No running servers — everything in-process.
"""

import sys
import os
import time
import hashlib
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..")))

from border.blockchain import (
    BorderWallet,
    BorderChain,
    BandwidthProof,
    ComputeProofRecord,
    BLOCK_REWARD,
    BC_PER_GB,
    BC_PER_COMPUTE_HOUR,
    MIN_BYTES_PER_BLOCK,
)
from border.compute import (
    ComputeJob,
    ComputeProof,
    GPUSpec,
    JobType,
    JobStatus,
    ComputeType,
    BorderWorker,
    ComputeMarket,
)
from border.ledger import BandwidthLedger

# ─────────────────────────────────────────────────────────
# ANSI colours
# ─────────────────────────────────────────────────────────
GREEN  = "\033[92m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
MAGENTA= "\033[95m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def h(text):  print(f"\n{BOLD}{CYAN}{'─'*60}{RESET}\n{BOLD}{text}{RESET}")
def ok(text): print(f"  {GREEN}✓{RESET} {text}")
def info(text):print(f"  {YELLOW}→{RESET} {text}")
def gpu(text): print(f"  {MAGENTA}⚡{RESET} {text}")


# ─────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────

def simulate_bandwidth_session(ledger, relay_wallet, client_id, mb):
    """Re-used from blockchain_demo — relay session → BandwidthProof."""
    import uuid
    bytes_fwd  = int(mb * 1024 * 1024)
    session_id = f"sess_{uuid.uuid4().hex[:8]}"
    receipt = ledger.record(client_id=client_id, bytes_forwarded=bytes_fwd, session_id=session_id)
    return BandwidthProof(
        receipt_id=receipt.receipt_id,
        relay_address=relay_wallet.address,
        client_id=client_id,
        bytes_forwarded=bytes_fwd,
        timestamp=receipt.timestamp,
        session_id=session_id,
        relay_signature=receipt.signature or "demo_sig",
        client_signature=None,
    )


def simulate_job_execution(job: ComputeJob, worker_wallet: BorderWallet, gpu_spec: GPUSpec):
    """
    Simulate a GPU worker running a job.
    In production: actually calls ollama / SD WebUI / subprocess.
    Here: instant stub that returns plausible results.
    """
    # Simulate compute time proportional to job type
    compute_times = {
        JobType.INFERENCE: 2.3,
        JobType.RENDER:    8.7,
        JobType.TRAIN:    45.2,
        JobType.CUSTOM:    1.1,
    }
    compute_seconds = compute_times.get(job.job_type, 2.0)

    # Stub result
    results = {
        JobType.INFERENCE: {
            "output": "BorderCompute enables decentralised AI inference — no cloud required.",
            "tokens": 42,
            "model":  job.model_id,
            "engine": "stub",
        },
        JobType.RENDER: {
            "images":  ["<base64_image_data>"],
            "prompt":  job.input_data.get("prompt", ""),
            "steps":   job.input_data.get("steps", 20),
            "engine":  "stub",
        },
        JobType.TRAIN: {
            "status":   "training_complete",
            "loss":     0.037,
            "steps":    job.input_data.get("steps", 100),
            "lora_rank":job.input_data.get("rank", 4),
            "engine":   "stub",
        },
        JobType.CUSTOM: {
            "output":    "Hello from BorderCompute!",
            "exit_code": 0,
            "engine":    "subprocess",
        },
    }
    result = results[job.job_type]
    output_hash = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()

    price_bc = min(job.max_price_bc, (compute_seconds / 3600) * BC_PER_COMPUTE_HOUR)

    proof = ComputeProof.from_job(
        job=job,
        worker_address=worker_wallet.address,
        gpu_spec=gpu_spec,
        compute_seconds=compute_seconds,
        output_hash=output_hash,
        price_bc=price_bc,
    )
    proof.worker_signature = worker_wallet.sign(proof.hash())
    return proof, result, compute_seconds


# ══════════════════════════════════════════════════════════
# DEMO
# ══════════════════════════════════════════════════════════

def main():
    print(f"\n{BOLD}⚡ BorderCompute — Decentralised GPU Network Demo{RESET}")
    print(f"   Your GPUs earn BorderCoin for every job they run\n")

    # ──────────────────────────────────────────────────────
    # 1. Wallets
    # ──────────────────────────────────────────────────────
    h("Step 1: Create wallets")

    client_wallet = BorderWallet.create()
    worker_wallet = BorderWallet.create()
    miner_wallet  = worker_wallet   # same node mines blocks

    ok(f"Client wallet : {client_wallet.address}")
    ok(f"Worker wallet : {worker_wallet.address}")
    info("Worker earns BC for compute jobs AND for mining blocks")

    # ──────────────────────────────────────────────────────
    # 2. Register GPU rig
    # ──────────────────────────────────────────────────────
    h("Step 2: Register GPU rig (your actual hardware)")

    gpu_specs = [
        GPUSpec(gpu_id=f"rx580_{i}", name="RX 580",       vram_gb=8,  compute_type=ComputeType.ROCM)
        for i in range(12)
    ] + [
        GPUSpec(gpu_id="rtx3080",   name="RTX 3080",      vram_gb=12, compute_type=ComputeType.CUDA),
        GPUSpec(gpu_id="rtx3060",   name="RTX 3060",      vram_gb=12, compute_type=ComputeType.CUDA),
        GPUSpec(gpu_id="gtx1080ti", name="GTX 1080 Ti",   vram_gb=11, compute_type=ComputeType.CUDA),
    ]

    worker = BorderWorker.create(
        wallet_address=worker_wallet.address,
        endpoint="http://0.0.0.0:8888",
        gpu_specs=gpu_specs,
        stake_bc=10.0,
        region="US",
    )

    for g in gpu_specs[:3]:
        gpu(f"{g.name} ({g.vram_gb}GB {g.compute_type})")
    gpu(f"... and {len(gpu_specs)-3} more GPUs")
    ok(f"Total: {worker.gpu_count} GPUs | {worker.total_vram_gb}GB VRAM | stake={worker.stake_bc} BC")
    ok(f"Worker ID: {worker.worker_id}")

    # ──────────────────────────────────────────────────────
    # 3. Set up market + blockchain
    # ──────────────────────────────────────────────────────
    h("Step 3: Initialise market + blockchain")

    market = ComputeMarket()
    market.registry.register(worker)

    chain = BorderChain()
    ok(f"Market ready | {len(market.registry.all_available())} worker(s) available")
    ok(f"Chain initialised | height={chain.height}")
    info(f"Economics: {BC_PER_COMPUTE_HOUR} BC/GPU-hour | {BC_PER_GB} BC/GB bandwidth | {BLOCK_REWARD} BC block reward")

    # ──────────────────────────────────────────────────────
    # 4. Submit compute jobs
    # ──────────────────────────────────────────────────────
    h("Step 4: Client submits compute jobs")

    jobs = [
        ComputeJob.create(
            job_type=JobType.INFERENCE,
            client_address=client_wallet.address,
            model_id="llama3:8b",
            input_data={"prompt": "What is BorderCompute?"},
            max_price_bc=0.005,
            min_vram_gb=8,
        ),
        ComputeJob.create(
            job_type=JobType.RENDER,
            client_address=client_wallet.address,
            model_id="sdxl",
            input_data={"prompt": "A futuristic decentralised network, cyberpunk", "steps": 30},
            max_price_bc=0.02,
            min_vram_gb=8,
        ),
        ComputeJob.create(
            job_type=JobType.TRAIN,
            client_address=client_wallet.address,
            model_id="mistral:7b",
            input_data={"rank": 8, "steps": 200, "dataset": "custom_data.jsonl"},
            max_price_bc=0.10,
            min_vram_gb=12,
        ),
        ComputeJob.create(
            job_type=JobType.CUSTOM,
            client_address=client_wallet.address,
            model_id="python3",
            input_data={"code": "print('Hello from BorderCompute!')", "timeout": 10},
            max_price_bc=0.001,
            min_vram_gb=0,
        ),
    ]

    for job in jobs:
        accepted, reason = market.submit_job(job)
        status = job.status
        worker_short = (job.worker_address or "")[:20]
        ok(f"{job.job_type:<12} | {job.model_id:<16} | {status} | worker: {worker_short}...")

    info(f"All {len(jobs)} jobs submitted and assigned to worker")

    # ──────────────────────────────────────────────────────
    # 5. Worker runs jobs + submits proofs
    # ──────────────────────────────────────────────────────
    h("Step 5: Worker runs jobs and submits compute proofs")

    # Use RTX 3080 for big jobs, RX 580 for small ones
    gpu_map = {
        JobType.INFERENCE: gpu_specs[12],  # RTX 3080
        JobType.RENDER:    gpu_specs[12],  # RTX 3080
        JobType.TRAIN:     gpu_specs[13],  # RTX 3060
        JobType.CUSTOM:    gpu_specs[0],   # RX 580
    }

    total_bc_earned = 0.0
    compute_proofs_for_chain = []

    for job in jobs:
        if job.status != JobStatus.ASSIGNED:
            continue

        gpu_used = gpu_map[job.job_type]
        proof, result, secs = simulate_job_execution(job, worker_wallet, gpu_used)

        accepted, reason, bc_earned = market.submit_proof(proof)
        total_bc_earned += bc_earned

        ok(
            f"{job.job_type:<12} | {gpu_used.name:<14} | "
            f"{secs:.1f}s | +{bc_earned:.6f} BC"
        )

        # Convert to chain record
        chain_record = ComputeProofRecord(
            proof_id=proof.proof_id,
            job_id=proof.job_id,
            worker_address=proof.worker_address,
            client_address=proof.client_address,
            compute_seconds=proof.compute_seconds,
            bytes_processed=proof.bytes_processed,
            input_hash=proof.input_hash,
            output_hash=proof.output_hash,
            timestamp=proof.timestamp,
            price_bc=proof.price_bc,
        )
        compute_proofs_for_chain.append(chain_record)
        chain.add_compute_proof(chain_record)

    info(f"Worker total earned this round: {total_bc_earned:.6f} BC")

    # ──────────────────────────────────────────────────────
    # 6. Mine a block (with bandwidth + compute proofs)
    # ──────────────────────────────────────────────────────
    h("Step 6: Mine a block — bandwidth + compute proofs included")

    # Need bandwidth proofs to meet MIN_BYTES_PER_BLOCK threshold
    ledger = BandwidthLedger(node_id="relay_01")
    bw_sessions = [
        ("user_tehran_01",   40.0),
        ("user_beijing_02",  35.0),
        ("user_moscow_03",   30.0),
    ]
    for client_id, mb in bw_sessions:
        bp = simulate_bandwidth_session(ledger, worker_wallet, client_id, mb)
        chain.add_proof(bp)

    info(f"Bandwidth pool: {chain.pending_bandwidth_mb:.1f}MB")
    info(f"Compute proofs pending: {chain.stats['pending_compute_proofs']}")

    block = chain.create_block(miner_address=worker_wallet.address)
    assert block is not None, "Not enough bandwidth to mine!"

    accepted, reason = chain.add_block(block)
    assert accepted, f"Block rejected: {reason}"

    bw_reward      = block.total_bandwidth_pc
    compute_reward = block.total_compute_bc
    total_reward   = BLOCK_REWARD + bw_reward + compute_reward

    ok(f"Block #{block.index} mined! ⛏")
    ok(f"  Bandwidth proofs : {len(block.bandwidth_proofs)} | {block.total_bytes/(1024*1024):.1f}MB")
    ok(f"  Compute proofs   : {len(block.compute_proofs)} jobs")
    ok(f"  Block reward     : {BLOCK_REWARD:.1f} BC")
    ok(f"  Bandwidth reward : {bw_reward:.4f} BC")
    ok(f"  Compute reward   : {compute_reward:.4f} BC")
    ok(f"  TOTAL reward     : {total_reward:.4f} BC → {worker_wallet.address[:20]}...")

    # ──────────────────────────────────────────────────────
    # 7. Check balances
    # ──────────────────────────────────────────────────────
    h("Step 7: Final balances")

    worker_balance = chain.get_balance(worker_wallet.address)
    client_balance = chain.get_balance(client_wallet.address)

    ok(f"Worker balance : {worker_balance:.8f} BC  ← earned running GPU jobs!")
    ok(f"Client balance : {client_balance:.8f} BC")
    ok(f"Total supply   : {chain.total_supply:.8f} BC")

    # ──────────────────────────────────────────────────────
    # 8. Market stats
    # ──────────────────────────────────────────────────────
    h("Step 8: Market + chain stats")

    stats = market.stats
    chain_stats = chain.stats
    valid, reason = chain.validate_chain()

    ok(f"Jobs completed   : {stats['jobs_completed']}")
    ok(f"Total BC paid    : {stats['total_bc_paid']:.6f} BC")
    ok(f"Total compute    : {stats['total_compute_seconds']:.1f}s GPU time")
    ok(f"Chain height     : {chain_stats['height']}")
    ok(f"Chain valid      : {valid} — {reason}")
    ok(f"Spent bw proofs  : {chain_stats['spent_receipts']}")
    ok(f"Spent cmp proofs : {chain_stats['spent_compute_proofs']}")

    # Assertions
    assert valid,               "Chain must be valid"
    assert worker_balance > 0,  "Worker must have earned BC"
    assert stats['jobs_completed'] == len(jobs), "All jobs must be complete"
    assert chain.height == 1,   "Should have mined 1 block"

    print(f"\n{BOLD}{GREEN}{'═'*60}")
    print(f"  ALL TESTS PASSED ✓")
    print(f"  BorderCompute is working end-to-end!")
    print(f"")
    print(f"  Your 15 GPUs → jobs → proofs → BorderCoin")
    print(f"  No middleman. No platform. You own the rails.")
    print(f"{'═'*60}{RESET}\n")

    # Summary
    print(f"{BOLD}GPU Earnings Summary:{RESET}")
    print(f"  Worker address  : {worker_wallet.address}")
    print(f"  Total BC earned : {worker_balance:.6f} BC")
    print(f"  Jobs run        : {stats['jobs_completed']}")
    print(f"  GPU-hours used  : {stats['total_compute_seconds']/3600:.4f}h")
    print(f"  GPUs available  : {worker.gpu_count} ({worker.total_vram_gb}GB VRAM)")
    print()


if __name__ == "__main__":
    main()
