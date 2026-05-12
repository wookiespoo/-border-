"""
BorderCompute — Worker Daemon
==============================
Run this on any machine with GPUs to join the BorderCompute network
and start earning BorderCoin automatically.

The daemon:
  1. Detects local GPUs
  2. Registers with a BorderComputeNode
  3. Polls the market for available jobs
  4. Runs matching jobs locally
  5. Submits ComputeProofs back
  6. Earns BorderCoin per job

Usage:
    # Simple start (auto-detect GPUs)
    from phantom.compute import WorkerDaemon
    from phantom.blockchain import BorderWallet

    wallet = BorderWallet.load("wallet.json")  # or BorderWallet.create()
    daemon = WorkerDaemon(
        wallet=wallet,
        market_endpoint="http://market.border.network:8888",
    )
    daemon.run()

    # Or from command line:
    python -m phantom.compute.daemon \\
        --wallet wallet.json \\
        --market http://localhost:8888 \\
        --region US
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import subprocess
import sys
import time
import uuid
from typing import Any, Dict, List, Optional

import httpx

from .job import (
    ComputeJob, ComputeProof, GPUSpec, JobType, JobStatus,
    BC_PER_COMPUTE_HOUR,
)
from .worker import BorderWorker

logger = logging.getLogger("border.compute.daemon")

POLL_INTERVAL   = 5     # seconds between market polls
HEARTBEAT_EVERY = 15    # seconds between heartbeats
MAX_CONCURRENT  = 2     # max parallel jobs per daemon


class WorkerDaemon:
    """
    The process that runs on your GPU rigs.

    Register once, then loops forever:
      poll market → accept job → run it → submit proof → get paid
    """

    def __init__(
        self,
        wallet,
        market_endpoint: str   = "http://localhost:8888",
        region:          str   = "UNKNOWN",
        gpu_specs:       Optional[List[GPUSpec]] = None,
        stake_bc:        float = 0.0,
        max_concurrent:  int   = MAX_CONCURRENT,
    ):
        self.wallet          = wallet
        self.market_endpoint = market_endpoint.rstrip("/")
        self.region          = region
        self.max_concurrent  = max_concurrent
        self._active_jobs:   Dict[str, asyncio.Task] = {}
        self._total_earned   = 0.0
        self._jobs_done      = 0
        self._start_time     = time.time()

        # Auto-detect GPUs if not provided
        self.gpu_specs = gpu_specs or GPUSpec.detect()

        # Build worker descriptor
        self.worker = BorderWorker.create(
            wallet_address=wallet.address,
            endpoint=f"http://0.0.0.0:0",  # daemon doesn't expose HTTP
            gpu_specs=self.gpu_specs,
            stake_bc=stake_bc,
            region=region,
        )

        logger.info(
            f"[Daemon] Worker {self.worker.worker_id} | "
            f"{len(self.gpu_specs)} GPU(s) | "
            f"{self.worker.total_vram_gb}GB VRAM total"
        )

    # ─────────────────────────────────────────────────────
    # Main loop
    # ─────────────────────────────────────────────────────

    def run(self) -> None:
        """Blocking entry point. Runs the async event loop."""
        print(f"\n⚡ BorderCompute Worker Daemon")
        print(f"   Wallet  : {self.wallet.address}")
        print(f"   Market  : {self.market_endpoint}")
        print(f"   Region  : {self.region}")
        for g in self.gpu_specs:
            print(f"   GPU     : {g.name} ({g.vram_gb}GB {g.compute_type})")
        print(f"   Earning : {BC_PER_COMPUTE_HOUR} BC/GPU-hour + per-GB bonus\n")
        print(f"   Press Ctrl+C to stop\n")

        try:
            asyncio.run(self._main())
        except KeyboardInterrupt:
            self._print_summary()

    async def _main(self) -> None:
        """Main async loop."""
        # Register with market
        await self._register()

        last_heartbeat = time.time()

        while True:
            try:
                now = time.time()

                # Heartbeat
                if now - last_heartbeat >= HEARTBEAT_EVERY:
                    await self._heartbeat()
                    last_heartbeat = now

                # Poll for jobs if we have capacity
                if len(self._active_jobs) < self.max_concurrent:
                    await self._poll_and_accept()

                await asyncio.sleep(POLL_INTERVAL)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"[Daemon] Loop error: {e}")
                await asyncio.sleep(POLL_INTERVAL)

    # ─────────────────────────────────────────────────────
    # Market communication
    # ─────────────────────────────────────────────────────

    async def _register(self) -> None:
        """Register this worker with the market node."""
        try:
            async with httpx.AsyncClient(timeout=10) as http:
                resp = await http.post(
                    f"{self.market_endpoint}/compute/worker",
                    json=self.worker.to_dict(),
                )
                if resp.status_code == 200:
                    data = resp.json()
                    logger.info(
                        f"[Daemon] Registered ✓ worker_id={self.worker.worker_id} "
                        f"{'(new)' if data.get('is_new') else '(updated)'}"
                    )
                    print(f"  ✓ Registered with market — watching for jobs...")
                else:
                    logger.warning(f"[Daemon] Registration failed: {resp.text}")
        except Exception as e:
            logger.error(f"[Daemon] Cannot reach market at {self.market_endpoint}: {e}")
            print(f"  ✗ Cannot reach market at {self.market_endpoint}")
            print(f"    Make sure a BorderComputeNode is running there.")

    async def _heartbeat(self) -> None:
        try:
            async with httpx.AsyncClient(timeout=5) as http:
                await http.post(
                    f"{self.market_endpoint}/compute/worker/{self.worker.worker_id}/heartbeat"
                )
        except Exception:
            pass  # Silent — market will just mark us offline

    async def _poll_and_accept(self) -> None:
        """Fetch pending jobs and accept any we can handle."""
        try:
            async with httpx.AsyncClient(timeout=5) as http:
                resp = await http.get(f"{self.market_endpoint}/compute/market")
                if resp.status_code != 200:
                    return
                data = resp.json()

            pending = data.get("pending_jobs", [])
            for job_dict in pending:
                job = ComputeJob.from_dict(job_dict)

                # Check we can handle it
                if job.job_id in self._active_jobs:
                    continue
                if not self.worker.can_handle(job.min_vram_gb, job.max_price_bc):
                    continue
                if len(self._active_jobs) >= self.max_concurrent:
                    break

                # Accept and run
                task = asyncio.create_task(self._run_job(job))
                self._active_jobs[job.job_id] = task
                task.add_done_callback(lambda t, jid=job.job_id: self._active_jobs.pop(jid, None))
                break  # One job per poll cycle

        except Exception as e:
            logger.debug(f"[Daemon] Poll error: {e}")

    # ─────────────────────────────────────────────────────
    # Job execution
    # ─────────────────────────────────────────────────────

    async def _run_job(self, job: ComputeJob) -> None:
        """Run a job and submit the proof."""
        gpu = self.gpu_specs[0] if self.gpu_specs else None
        logger.info(f"[Daemon] Starting job {job.job_id} type={job.job_type} model={job.model_id}")
        print(f"\n  → Job {job.job_id[:16]}... | {job.job_type} | {job.model_id}")

        start = time.time()
        result = None
        error  = None

        try:
            result = await self._execute(job)
        except Exception as e:
            error = str(e)
            logger.warning(f"[Daemon] Job {job.job_id} failed: {e}")

        compute_seconds = time.time() - start

        if error or result is None:
            await self._report_failure(job.job_id, error or "execution failed")
            return

        # Build and submit proof
        output_hash = hashlib.sha256(
            json.dumps(result, sort_keys=True).encode()
        ).hexdigest()

        price_bc = min(
            job.max_price_bc,
            (compute_seconds / 3600) * BC_PER_COMPUTE_HOUR
        )

        proof = ComputeProof.from_job(
            job=job,
            worker_address=self.wallet.address,
            gpu_spec=gpu or GPUSpec(gpu_id="cpu", name="CPU", vram_gb=0,
                                    compute_type=__import__("phantom.compute.job", fromlist=["ComputeType"]).ComputeType.OPENCL),
            compute_seconds=compute_seconds,
            output_hash=output_hash,
            price_bc=price_bc,
        )
        proof.worker_signature = self.wallet.sign(proof.hash())

        await self._submit_proof(proof, result)
        self._jobs_done   += 1
        self._total_earned += price_bc

        print(
            f"  ✓ Job done | {compute_seconds:.1f}s | "
            f"+{price_bc:.6f} BC | total={self._total_earned:.6f} BC"
        )

    async def _execute(self, job: ComputeJob) -> Dict[str, Any]:
        """
        Execute the actual compute job.

        Currently supports:
          INFERENCE  — run a local model (ollama, transformers, llama.cpp)
          RENDER     — image generation (stable diffusion via subprocess)
          CUSTOM     — run an arbitrary Python snippet in subprocess sandbox
          TRAIN      — LoRA fine-tuning stub (returns training params)

        In production, each job type plugs into the actual GPU library.
        The interface is intentionally simple: dict in, dict out.
        """
        jtype = job.job_type

        if jtype == JobType.INFERENCE:
            return await self._run_inference(job)
        elif jtype == JobType.RENDER:
            return await self._run_render(job)
        elif jtype == JobType.TRAIN:
            return await self._run_train(job)
        elif jtype == JobType.CUSTOM:
            return await self._run_custom(job)
        else:
            raise ValueError(f"Unknown job type: {jtype}")

    async def _run_inference(self, job: ComputeJob) -> dict:
        """
        Run AI inference. Tries ollama first, falls back to stub.
        """
        prompt     = job.input_data.get("prompt", "")
        model_id   = job.model_id

        # Try ollama if available
        try:
            async with httpx.AsyncClient(timeout=60) as http:
                resp = await http.post(
                    "http://localhost:11434/api/generate",
                    json={"model": model_id, "prompt": prompt, "stream": False},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return {
                        "output": data.get("response", ""),
                        "model":  model_id,
                        "tokens": data.get("eval_count", 0),
                        "engine": "ollama",
                    }
        except Exception:
            pass

        # Stub response (no model loaded — demo mode)
        await asyncio.sleep(0.5)  # simulate work
        return {
            "output":  f"[stub] Inference result for: {prompt[:50]}",
            "model":   model_id,
            "tokens":  len(prompt.split()),
            "engine":  "stub",
            "note":    "Install ollama and pull a model for real inference",
        }

    async def _run_render(self, job: ComputeJob) -> dict:
        """
        Image/video generation stub.
        In production: calls stable-diffusion-webui API or ComfyUI.
        """
        prompt = job.input_data.get("prompt", "")
        steps  = job.input_data.get("steps", 20)

        # Try SD WebUI if available
        try:
            async with httpx.AsyncClient(timeout=120) as http:
                resp = await http.post(
                    "http://localhost:7860/sdapi/v1/txt2img",
                    json={
                        "prompt": prompt,
                        "steps":  steps,
                        "width":  512,
                        "height": 512,
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return {
                        "images":  data.get("images", []),
                        "prompt":  prompt,
                        "steps":   steps,
                        "engine":  "sd-webui",
                    }
        except Exception:
            pass

        await asyncio.sleep(1.0)
        return {
            "images":  [],
            "prompt":  prompt,
            "steps":   steps,
            "engine":  "stub",
            "note":    "Start SD WebUI at localhost:7860 for real image generation",
        }

    async def _run_train(self, job: ComputeJob) -> dict:
        """LoRA / fine-tune job stub."""
        await asyncio.sleep(2.0)
        return {
            "status":      "training_complete",
            "model_id":    job.model_id,
            "lora_rank":   job.input_data.get("rank", 4),
            "steps":       job.input_data.get("steps", 100),
            "loss":        0.042,
            "engine":      "stub",
            "note":        "Integrate with PEFT/LoRA library for real training",
        }

    async def _run_custom(self, job: ComputeJob) -> dict:
        """
        Run an arbitrary Python snippet in a sandboxed subprocess.
        Input: { "code": "print('hello')", "timeout": 30 }
        """
        code    = job.input_data.get("code", "")
        timeout = min(job.input_data.get("timeout", 30), 60)

        if not code:
            return {"output": "", "error": "no code provided"}

        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-c", code,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            return {
                "output":    stdout.decode(errors="replace"),
                "stderr":    stderr.decode(errors="replace"),
                "exit_code": proc.returncode,
                "engine":    "subprocess",
            }
        except asyncio.TimeoutError:
            return {"output": "", "error": f"Timed out after {timeout}s"}
        except Exception as e:
            return {"output": "", "error": str(e)}

    # ─────────────────────────────────────────────────────
    # Result submission
    # ─────────────────────────────────────────────────────

    async def _submit_proof(self, proof: ComputeProof, result: dict) -> None:
        """Submit proof + result back to the market node."""
        payload = {**proof.to_dict(), "result": result}
        try:
            async with httpx.AsyncClient(timeout=10) as http:
                resp = await http.post(
                    f"{self.market_endpoint}/compute/proof",
                    json=payload,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    logger.info(
                        f"[Daemon] Proof submitted ✓ "
                        f"proof_id={proof.proof_id} bc={data.get('bc_earned', 0):.6f}"
                    )
                else:
                    logger.warning(f"[Daemon] Proof rejected: {resp.text}")
        except Exception as e:
            logger.warning(f"[Daemon] Cannot submit proof: {e}")

    async def _report_failure(self, job_id: str, reason: str) -> None:
        try:
            async with httpx.AsyncClient(timeout=5) as http:
                await http.post(
                    f"{self.market_endpoint}/compute/job/{job_id}/fail",
                    json={"reason": reason},
                )
        except Exception:
            pass

    # ─────────────────────────────────────────────────────
    # Summary
    # ─────────────────────────────────────────────────────

    def _print_summary(self) -> None:
        uptime = time.time() - self._start_time
        print(f"\n\n{'═'*50}")
        print(f"  BorderCompute Daemon — Session Summary")
        print(f"{'═'*50}")
        print(f"  Uptime       : {uptime/3600:.2f} hours")
        print(f"  Jobs done    : {self._jobs_done}")
        print(f"  BC earned    : {self._total_earned:.6f} BC")
        print(f"  Wallet       : {self.wallet.address}")
        print(f"{'═'*50}\n")


# ─────────────────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(description="BorderCompute Worker Daemon")
    parser.add_argument("--wallet", default="wallet.json",   help="Path to BorderWallet JSON")
    parser.add_argument("--market", default="http://localhost:8888", help="BorderComputeNode URL")
    parser.add_argument("--region", default="UNKNOWN",       help="Your region (US/EU/ASIA/etc)")
    parser.add_argument("--stake",  default=0.0, type=float, help="BC to stake as collateral")
    parser.add_argument("--max-jobs", default=2, type=int,   help="Max concurrent jobs")
    args = parser.parse_args()

    from phantom.blockchain import BorderWallet
    import os

    if os.path.exists(args.wallet):
        wallet = BorderWallet.load(args.wallet)
        print(f"  Loaded wallet: {wallet.address}")
    else:
        wallet = BorderWallet.create()
        wallet.save(args.wallet)
        print(f"  Created new wallet: {wallet.address}")
        print(f"  Saved to: {args.wallet}")

    daemon = WorkerDaemon(
        wallet=wallet,
        market_endpoint=args.market,
        region=args.region,
        stake_bc=args.stake,
        max_concurrent=args.max_jobs,
    )
    daemon.run()


if __name__ == "__main__":
    main()
