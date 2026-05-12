"""
BorderRender Daemon — runs on your GPU rig

Polls RenderMarket for jobs, executes via ComfyUI / AUTOMATIC1111 / stub,
submits RenderResult, earns BC per frame.

CLI:
    python -m phantom.render.daemon \\
        --wallet wallet.json \\
        --market http://localhost:8891 \\
        --models sdxl,flux-schnell \\
        --backend comfyui \\
        --vram 12
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from typing import List, Optional

from .job    import RenderJob, RenderResult, RenderStatus, RenderType, RenderBackend
from .market import RenderMarket, RenderWorker

logger = logging.getLogger("border.render.daemon")


def _stub_render(job: RenderJob) -> tuple:
    """
    Stub renderer — produces a plausible result hash without a real GPU.
    In production: calls ComfyUI /prompt or A1111 /sdapi/v1/txt2img.
    """
    time.sleep(0.05)  # simulate render time

    # Simulate render time based on job complexity
    base_seconds = (job.steps / 20) * (job.width * job.height / (512 * 512)) * 8.0
    render_time  = base_seconds * job.num_frames

    # Produce a deterministic output hash from prompt
    raw = f"{job.prompt}:{job.seed}:{job.width}:{job.height}:{job.steps}"
    out_hash = hashlib.sha256(raw.encode()).hexdigest()

    frames = job.num_frames
    return frames, out_hash, render_time


def _comfyui_render(job: RenderJob, endpoint: str = "http://localhost:8188") -> tuple:
    """Call local ComfyUI. Falls back to stub if unavailable."""
    try:
        import urllib.request

        workflow = {
            "3": {"class_type": "KSampler", "inputs": {
                "seed": job.seed if job.seed >= 0 else int(time.time()),
                "steps": job.steps, "cfg": job.cfg_scale,
                "sampler_name": "euler", "scheduler": "normal",
                "denoise": 1.0,
            }},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"text": job.prompt}},
            "7": {"class_type": "CLIPTextEncode", "inputs": {"text": job.negative_prompt}},
        }
        payload = json.dumps({"prompt": workflow}).encode()
        req = urllib.request.Request(
            f"{endpoint}/prompt", data=payload,
            headers={"Content-Type": "application/json"}, method="POST"
        )
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=60) as resp:
            data        = json.loads(resp.read())
            render_time = time.time() - t0
            out_hash    = hashlib.sha256(str(data).encode()).hexdigest()
            return job.num_frames, out_hash, render_time
    except Exception as e:
        logger.warning(f"[Daemon] ComfyUI unavailable ({e}), using stub")
        return _stub_render(job)


class RenderDaemon:
    def __init__(self, wallet, market: RenderMarket,
                 model_ids: List[str], total_vram_gb: float,
                 backend: RenderBackend = RenderBackend.STUB,
                 stake_bc: float = 10.0, poll_interval: float = 1.0,
                 comfyui_endpoint: str = "http://localhost:8188"):
        self.wallet            = wallet
        self.market            = market
        self.backend           = backend
        self.poll_interval     = poll_interval
        self.comfyui_endpoint  = comfyui_endpoint

        # Estimate frames/min from VRAM (rough heuristic)
        frames_pm = max(1.0, total_vram_gb / 8.0 * 2.0)

        self.worker = RenderWorker.create(
            wallet_address = wallet.address,
            endpoint       = "http://0.0.0.0:8891",
            model_ids      = model_ids,
            total_vram_gb  = total_vram_gb,
            stake_bc       = stake_bc,
            frames_per_min = frames_pm,
        )
        self.market.register_worker(self.worker)

    def run_once(self) -> List[RenderResult]:
        results = []
        for job in list(self.market._jobs.values()):
            if (job.status == RenderStatus.ASSIGNED
                    and job.worker_address == self.wallet.address):
                result = self._execute(job)
                if result:
                    results.append(result)
        return results

    def _execute(self, job: RenderJob) -> Optional[RenderResult]:
        job.status = RenderStatus.RENDERING
        try:
            if self.backend == RenderBackend.COMFYUI:
                frames, out_hash, render_time = _comfyui_render(job, self.comfyui_endpoint)
            else:
                frames, out_hash, render_time = _stub_render(job)

            price_bc = min(job.max_price_bc, job.expected_cost_bc)
            result = RenderResult(
                result_id       = uuid.uuid4().hex,
                job_id          = job.job_id,
                worker_address  = self.wallet.address,
                client_address  = job.client_address,
                model_id        = job.model_id,
                render_type     = job.render_type,
                frames_rendered = frames,
                width           = job.width,
                height          = job.height,
                output_hash     = out_hash,
                output_url      = f"border://render/{out_hash}",
                render_time_s   = render_time,
                price_bc        = price_bc,
            )
            result.worker_signature = self.wallet.sign(result.hash().encode())

            ok, reason, bc = self.market.submit_result(result)
            logger.info(f"[Daemon] {job.render_type} | {job.model_id} | "
                        f"{frames} frame(s) | {render_time:.1f}s | +{bc:.6f} BC")
            return result
        except Exception as e:
            logger.error(f"[Daemon] Render failed: {e}")
            job.status = RenderStatus.FAILED
            return None

    def run(self):
        logger.info(f"[Daemon] Starting — wallet={self.wallet.address[:20]}... "
                    f"models={self.worker.model_ids}")
        while True:
            self.run_once()
            time.sleep(self.poll_interval)
