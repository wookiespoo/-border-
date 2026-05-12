#!/usr/bin/env python3
"""
BorderInfer + BorderRender Demo
================================
Full end-to-end demo of private AI inference and GPU rendering.

What this shows:
  BorderInfer:
    1. Worker registers with 4 models (llama3, mistral, phi3, nomic-embed)
    2. Client submits 5 jobs: chat, completion, embedding, classify, summarise
    3. Worker daemon runs jobs, earns BC per token
    4. Proofs submitted to BorderChain

  BorderRender:
    5. Worker registers with sdxl + animatediff
    6. Client submits 4 render jobs: image, upscale, video, 3D
    7. Worker daemon renders, earns BC per frame
    8. Proofs submitted to BorderChain

  Combined:
    9. Single block mined covering all compute + bandwidth
   10. Worker balance shown — BC earned from both inference and rendering

No running servers. No GPU required. Everything in-process.
"""

import sys, os, time, hashlib, uuid
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from phantom.blockchain import (
    BorderWallet, BorderChain, BandwidthProof, ComputeProofRecord,
    BLOCK_REWARD, BC_PER_GB, MIN_BYTES_PER_BLOCK,
)
from phantom.ledger  import BandwidthLedger
from phantom.infer   import (
    InferJob, InferType, InferMarket, InferWorker, InferDaemon,
    ModelRegistry, ModelBackend, BC_PER_1K_TOKENS_INPUT, BC_PER_1K_TOKENS_OUTPUT,
)
from phantom.render  import (
    RenderJob, RenderType, RenderMarket, RenderWorker, RenderDaemon, RenderBackend,
    BC_PER_IMAGE_1024, BC_PER_VIDEO_FRAME,
)

# ── Colours ───────────────────────────────────────────────
GREEN  = "\033[92m"; YELLOW = "\033[93m"; CYAN   = "\033[96m"
MAGENTA= "\033[95m"; BOLD   = "\033[1m";  RESET  = "\033[0m"
def h(t):    print(f"\n{BOLD}{CYAN}{'─'*60}{RESET}\n{BOLD}{t}{RESET}")
def ok(t):   print(f"  {GREEN}✓{RESET} {t}")
def info(t): print(f"  {YELLOW}→{RESET} {t}")
def gpu(t):  print(f"  {MAGENTA}⚡{RESET} {t}")


def bw_proof(ledger, relay_wallet, client_id, mb):
    bfwd = int(mb * 1024 * 1024)
    sid  = f"sess_{uuid.uuid4().hex[:8]}"
    rec  = ledger.record(client_id=client_id, bytes_forwarded=bfwd, session_id=sid)
    return BandwidthProof(
        receipt_id=rec.receipt_id, relay_address=relay_wallet.address,
        client_id=client_id, bytes_forwarded=bfwd,
        timestamp=rec.timestamp, session_id=sid,
        relay_signature=rec.signature or "sig", client_signature=None,
    )


def main():
    print(f"\n{BOLD}⚡ BorderInfer + BorderRender — Decentralised AI & GPU Demo{RESET}")
    print(f"   Your GPUs earn BC for every token and every frame\n")

    # ──────────────────────────────────────────────────────
    # 1. Wallets
    # ──────────────────────────────────────────────────────
    h("Step 1: Create wallets")

    worker_wallet = BorderWallet.create()
    client_wallet = BorderWallet.create()

    ok(f"Worker : {worker_wallet.address}")
    ok(f"Client : {client_wallet.address}")
    info("Worker earns BC for inference tokens AND render frames AND mining blocks")

    # ──────────────────────────────────────────────────────
    # 2. BorderInfer — register worker
    # ──────────────────────────────────────────────────────
    h("Step 2: Register GPU worker with BorderInfer market")

    infer_market = InferMarket()
    infer_daemon = InferDaemon(
        wallet        = worker_wallet,
        market        = infer_market,
        model_ids     = ["llama3:8b", "mistral:7b", "phi3:mini", "nomic-embed-text"],
        total_vram_gb = 12.0,
        backend       = ModelBackend.STUB,
        stake_bc      = 15.0,
    )

    ok(f"Worker registered | models={infer_daemon.worker.model_ids}")
    ok(f"VRAM: {infer_daemon.worker.total_vram_gb}GB | stake: {infer_daemon.worker.stake_bc} BC")
    info(f"Pricing: {BC_PER_1K_TOKENS_INPUT}/1K input tokens, {BC_PER_1K_TOKENS_OUTPUT}/1K output tokens")

    # ──────────────────────────────────────────────────────
    # 3. Submit inference jobs
    # ──────────────────────────────────────────────────────
    h("Step 3: Client submits 5 inference jobs")

    infer_jobs = [
        InferJob.create(InferType.CHAT, client_wallet.address, "llama3:8b",
                        [{"role": "user", "content": "What is BorderInfer and why does it matter for AI privacy?"}],
                        max_tokens=200, max_price_bc=0.002),
        InferJob.create(InferType.COMPLETION, client_wallet.address, "mistral:7b",
                        [{"role": "user", "content": "The Border network enables"}],
                        max_tokens=100, max_price_bc=0.001),
        InferJob.create(InferType.EMBEDDING, client_wallet.address, "nomic-embed-text",
                        [{"role": "user", "content": "Decentralised AI inference on GPU nodes"}],
                        max_price_bc=0.0001),
        InferJob.create(InferType.CLASSIFY, client_wallet.address, "phi3:mini",
                        [{"role": "user", "content": "Border protocol is great for censorship resistance"}],
                        max_tokens=50, max_price_bc=0.0005),
        InferJob.create(InferType.SUMMARISE, client_wallet.address, "llama3:8b",
                        [{"role": "user", "content": "BorderInfer routes AI jobs to GPU workers who earn BC per token. No cloud. No surveillance. Private by default."}],
                        max_tokens=80, max_price_bc=0.001),
    ]

    for job in infer_jobs:
        ok_flag, reason = infer_market.submit_job(job)
        ok(f"{job.infer_type:<12} | {job.model_id:<20} | status={job.status}")

    # ──────────────────────────────────────────────────────
    # 4. Worker runs inference jobs
    # ──────────────────────────────────────────────────────
    h("Step 4: Worker runs all inference jobs")

    # Re-mark assigned jobs (daemon processes all assigned to this wallet)
    # Since worker is marked unavailable after first job, re-enable between runs
    infer_results = []
    total_infer_bc = 0.0
    total_tokens   = 0

    for job in infer_jobs:
        infer_daemon.worker.is_available = True
        infer_market.retry_pending_jobs()
        results = infer_daemon.run_once()
        for r in results:
            infer_results.append(r)
            total_infer_bc += r.reward_bc
            total_tokens   += r.total_tokens
            embed_note = f" | embedding dim={len(r.embeddings)}" if r.embeddings else f" | '{r.output_text[:50]}...'"
            ok(f"{job.infer_type:<12} | {r.total_tokens:>5} tokens | +{r.reward_bc:.6f} BC{embed_note}")

    info(f"Total inference: {total_tokens} tokens | {total_infer_bc:.6f} BC earned")

    # ──────────────────────────────────────────────────────
    # 5. BorderRender — register worker
    # ──────────────────────────────────────────────────────
    h("Step 5: Register GPU worker with BorderRender market")

    render_market = RenderMarket()
    render_daemon = RenderDaemon(
        wallet           = worker_wallet,
        market           = render_market,
        model_ids        = ["sdxl", "flux-schnell", "animatediff"],
        total_vram_gb    = 12.0,
        backend          = RenderBackend.STUB,
        stake_bc         = 20.0,

    )

    ok(f"Worker registered | models={render_daemon.worker.model_ids}")
    ok(f"VRAM: {render_daemon.worker.total_vram_gb}GB | frames/min: {render_daemon.worker.frames_per_min}")
    info(f"Pricing: {BC_PER_IMAGE_1024} BC/image | {BC_PER_VIDEO_FRAME} BC/video frame")

    # ──────────────────────────────────────────────────────
    # 6. Submit render jobs
    # ──────────────────────────────────────────────────────
    h("Step 6: Client submits 4 render jobs")

    render_jobs = [
        RenderJob.create(RenderType.IMAGE, client_wallet.address, "sdxl",
                         "A decentralised city floating in the clouds, cyberpunk aesthetic, ultra detailed",
                         negative_prompt="blurry, low quality",
                         width=1024, height=1024, steps=30, max_price_bc=0.003),
        RenderJob.create(RenderType.UPSCALE, client_wallet.address, "sdxl",
                         "Upscale: border network node interface",
                         width=2048, height=2048, steps=10, max_price_bc=0.002),
        RenderJob.create(RenderType.VIDEO, client_wallet.address, "animatediff",
                         "Smooth ocean waves at sunset, cinematic",
                         width=512, height=512, steps=20, num_frames=24, fps=12,
                         max_price_bc=0.015, min_vram_gb=8),
        RenderJob.create(RenderType.IMAGE, client_wallet.address, "flux-schnell",
                         "Portrait of an AI node, glowing circuits, minimal",
                         width=1024, height=1024, steps=4, max_price_bc=0.002),
    ]

    for job in render_jobs:
        ok_flag, reason = render_market.submit_job(job)
        ok(f"{job.render_type:<8} | {job.model_id:<14} | {job.width}×{job.height} "
           f"| {job.num_frames} frame(s) | status={job.status}")

    # ──────────────────────────────────────────────────────
    # 7. Worker renders jobs
    # ──────────────────────────────────────────────────────
    h("Step 7: Worker renders all jobs")

    render_results = []
    total_render_bc = 0.0
    total_frames    = 0

    for job in render_jobs:
        render_daemon.worker.is_available = True
        render_market.retry_pending_jobs()
        results = render_daemon.run_once()
        for r in results:
            render_results.append(r)
            total_render_bc += r.reward_bc
            total_frames    += r.frames_rendered
            ok(f"{r.render_type:<8} | {r.model_id:<14} | "
               f"{r.frames_rendered} frame(s) | {r.render_time_s:.1f}s | "
               f"+{r.reward_bc:.6f} BC | hash={r.output_hash[:16]}...")

    info(f"Total rendered: {total_frames} frames | {total_render_bc:.6f} BC earned")

    # ──────────────────────────────────────────────────────
    # 8. Submit all proofs to BorderChain + mine block
    # ──────────────────────────────────────────────────────
    h("Step 8: Submit proofs to BorderChain and mine a block")

    chain  = BorderChain()
    ledger = BandwidthLedger(node_id="relay_main")

    # Bandwidth for block threshold
    for cid, mb in [("u1", 45.0), ("u2", 40.0), ("u3", 20.0)]:
        chain.add_proof(bw_proof(ledger, worker_wallet, cid, mb))

    # Convert infer results to chain records
    for i, result in enumerate(infer_results):
        record = ComputeProofRecord(
            proof_id        = result.result_id,
            job_id          = result.job_id,
            worker_address  = result.worker_address,
            client_address  = result.client_address,
            compute_seconds = result.latency_ms / 1000,
            bytes_processed = (result.total_tokens * 4),  # ~4 bytes per token
            input_hash      = result.input_hash(),
            output_hash     = result.output_hash(),
            timestamp       = result.timestamp,
            price_bc        = result.price_bc,
        )
        chain.add_compute_proof(record)

    # Convert render results to chain records
    for result in render_results:
        record = ComputeProofRecord(
            proof_id        = result.result_id,
            job_id          = result.job_id,
            worker_address  = result.worker_address,
            client_address  = result.client_address,
            compute_seconds = result.render_time_s,
            bytes_processed = result.frames_rendered * result.width * result.height * 3,
            input_hash      = hashlib.sha256(result.job_id.encode()).hexdigest(),
            output_hash     = result.output_hash,
            timestamp       = result.timestamp,
            price_bc        = result.price_bc,
        )
        chain.add_compute_proof(record)

    info(f"BW pending: {chain.pending_bandwidth_mb:.1f}MB")
    info(f"Compute proofs pending: {chain.stats['pending_compute_proofs']}")

    block = chain.create_block(miner_address=worker_wallet.address)
    assert block is not None, "Not enough bandwidth!"
    ok_flag, reason = chain.add_block(block)
    assert ok_flag, f"Block rejected: {reason}"

    bw_reward  = block.total_bandwidth_pc
    cpu_reward = block.total_compute_bc
    total_reward = BLOCK_REWARD + bw_reward + cpu_reward

    ok(f"Block #{block.index} mined!")
    ok(f"  Bandwidth proofs  : {len(block.bandwidth_proofs)}")
    ok(f"  Compute proofs    : {len(block.compute_proofs)} (infer + render)")
    ok(f"  Block reward      : {BLOCK_REWARD:.1f} BC")
    ok(f"  Bandwidth reward  : {bw_reward:.6f} BC")
    ok(f"  Compute reward    : {cpu_reward:.6f} BC")
    ok(f"  TOTAL             : {total_reward:.6f} BC")

    # ──────────────────────────────────────────────────────
    # 9. Final balances + stats
    # ──────────────────────────────────────────────────────
    h("Step 9: Final balances and network stats")

    worker_balance = chain.get_balance(worker_wallet.address)
    valid, reason  = chain.validate_chain()

    ok(f"Worker balance    : {worker_balance:.8f} BC")
    ok(f"Chain height      : {chain.height}")
    ok(f"Chain valid       : {valid} — {reason}")
    ok(f"Total supply      : {chain.total_supply:.8f} BC")

    h("Step 10: Market stats")
    i_stats = infer_market.stats
    r_stats = render_market.stats
    ok(f"Infer  — jobs: {i_stats['jobs_completed']} | tokens: {i_stats['total_tokens']:,} | BC paid: {i_stats['total_bc_paid']:.6f}")
    ok(f"Render — jobs: {r_stats['jobs_completed']} | frames: {r_stats['total_frames']:,} | BC paid: {r_stats['total_bc_paid']:.6f}")

    # ──────────────────────────────────────────────────────
    # Assertions
    # ──────────────────────────────────────────────────────
    assert i_stats['jobs_completed'] == len(infer_jobs),  "All infer jobs must complete"
    assert r_stats['jobs_completed'] == len(render_jobs), "All render jobs must complete"
    assert worker_balance > 0,   "Worker must have earned BC"
    assert chain.height == 1,    "One block mined"
    assert valid,                "Chain must be valid"

    print(f"\n{BOLD}{GREEN}{'═'*60}")
    print(f"  ALL TESTS PASSED ✓")
    print(f"  BorderInfer + BorderRender working end-to-end!")
    print(f"")
    print(f"  {i_stats['total_tokens']:,} AI tokens  ·  {r_stats['total_frames']} rendered frames")
    print(f"  {total_infer_bc:.6f} BC (inference)  +  {total_render_bc:.6f} BC (rendering)")
    print(f"  Your GPUs. Your income. No middleman.")
    print(f"{'═'*60}{RESET}\n")

    print(f"{BOLD}Worker Earnings Summary:{RESET}")
    print(f"  Inference BC  : {total_infer_bc:.6f}")
    print(f"  Render BC     : {total_render_bc:.6f}")
    print(f"  Block reward  : {total_reward:.6f}")
    print(f"  Wallet balance: {worker_balance:.8f} BC")
    print()


if __name__ == "__main__":
    main()
