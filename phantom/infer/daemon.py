"""
BorderInfer Daemon — runs on your GPU rig

Polls the InferMarket for assigned jobs, executes them via
ollama / llama.cpp / stub, submits InferResult, earns BC per token.

CLI:
    python -m phantom.infer.daemon \\
        --wallet wallet.json \\
        --market http://localhost:8890 \\
        --models llama3:8b,mistral:7b,nomic-embed-text \\
        --backend ollama
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import time
import uuid
from typing import List, Optional

from .job    import InferJob, InferResult, InferStatus, InferType
from .market import InferMarket, InferWorker
from .model  import ModelBackend, ModelRegistry, ModelSpec

logger = logging.getLogger("border.infer.daemon")


def _count_tokens(text: str) -> int:
    """Rough token counter — ~4 chars per token (good enough for billing)."""
    return max(1, len(text) // 4)


def _stub_inference(job: InferJob, model: ModelSpec) -> tuple:
    """
    Stub inference — returns plausible output without actually calling a GPU.
    In production: replaced by ollama/llama.cpp HTTP calls.
    """
    time.sleep(0.01)   # simulate latency

    input_text = job.input_text()
    input_tokens = _count_tokens(input_text)

    if job.infer_type == InferType.EMBEDDING:
        # Return 768-dim stub embedding
        import hashlib
        seed = int(hashlib.md5(input_text.encode()).hexdigest(), 16)
        emb = [(((seed >> i) & 0xFF) / 255.0 - 0.5) for i in range(768)]
        return input_tokens, 0, "", emb, 12.5

    responses = {
        InferType.CHAT:       f"[BorderInfer] Decentralised AI response to: '{input_text[:60]}...' — powered by your GPU, no cloud required.",
        InferType.COMPLETION: f"[BorderInfer] {input_text[:40]}... the future is decentralised.",
        InferType.CLASSIFY:   "[BorderInfer] Classification: POSITIVE (confidence: 0.94)",
        InferType.SUMMARISE:  f"[BorderInfer] Summary: {input_text[:80]}...",
    }
    output_text   = responses.get(job.infer_type, "[BorderInfer] Done.")
    output_tokens = _count_tokens(output_text)
    latency_ms    = (input_tokens + output_tokens) / model.tokens_per_s * 1000

    return input_tokens, output_tokens, output_text, None, latency_ms


def _ollama_inference(job: InferJob, model: ModelSpec) -> tuple:
    """Call local ollama instance. Falls back to stub if unavailable."""
    try:
        import urllib.request
        payload = json.dumps({
            "model":  job.model_id,
            "prompt": job.input_text(),
            "stream": False,
            "options": {"num_predict": job.max_tokens, "temperature": job.temperature},
        }).encode()
        req = urllib.request.Request(
            f"{model.endpoint}/api/generate",
            data=payload, headers={"Content-Type": "application/json"}, method="POST"
        )
        t0  = time.time()
        with urllib.request.urlopen(req, timeout=30) as resp:
            data       = json.loads(resp.read())
            latency_ms = (time.time() - t0) * 1000
            out_text   = data.get("response", "")
            in_tok     = data.get("prompt_eval_count", _count_tokens(job.input_text()))
            out_tok    = data.get("eval_count",        _count_tokens(out_text))
            return in_tok, out_tok, out_text, None, latency_ms
    except Exception as e:
        logger.warning(f"[Daemon] ollama unavailable ({e}), using stub")
        return _stub_inference(job, model)


class InferDaemon:
    def __init__(self, wallet, market: InferMarket,
                 model_ids: List[str], total_vram_gb: float,
                 backend: ModelBackend = ModelBackend.STUB,
                 stake_bc: float = 10.0, poll_interval: float = 1.0):
        self.wallet        = wallet
        self.market        = market
        self.backend       = backend
        self.poll_interval = poll_interval
        self.registry      = ModelRegistry()

        # Register declared models
        for mid in model_ids:
            self.registry.register(mid, backend=backend)

        self.worker = InferWorker.create(
            wallet_address = wallet.address,
            endpoint       = "http://0.0.0.0:8890",
            model_ids      = model_ids,
            total_vram_gb  = total_vram_gb,
            stake_bc       = stake_bc,
            tokens_per_s   = 45.0,
        )
        self.market.register_worker(self.worker)

    def run_once(self) -> List[InferResult]:
        """Process all currently assigned jobs. Returns list of results."""
        results = []
        for job in list(self.market._jobs.values()):
            if (job.status == InferStatus.ASSIGNED
                    and job.worker_address == self.wallet.address):
                result = self._execute(job)
                if result:
                    results.append(result)
        return results

    def _execute(self, job: InferJob) -> Optional[InferResult]:
        model = self.registry.get(job.model_id)
        if model is None:
            logger.error(f"[Daemon] Model not loaded: {job.model_id}")
            return None

        job.status = InferStatus.STREAMING
        try:
            if self.backend == ModelBackend.OLLAMA:
                in_tok, out_tok, out_text, emb, latency = _ollama_inference(job, model)
            else:
                in_tok, out_tok, out_text, emb, latency = _stub_inference(job, model)

            price_bc = min(job.max_price_bc,
                           (in_tok / 1000) * 0.0002 + (out_tok / 1000) * 0.0008)

            result = InferResult(
                result_id       = uuid.uuid4().hex,
                job_id          = job.job_id,
                worker_address  = self.wallet.address,
                client_address  = job.client_address,
                model_id        = job.model_id,
                input_tokens    = in_tok,
                output_tokens   = out_tok,
                output_text     = out_text,
                embeddings      = emb,
                latency_ms      = latency,
                price_bc        = price_bc,
            )
            result.worker_signature = self.wallet.sign(result.hash().encode())

            ok, reason, bc = self.market.submit_result(result)
            logger.info(f"[Daemon] {job.infer_type} | {result.total_tokens} tok | "
                        f"+{bc:.6f} BC | {latency:.0f}ms")
            return result

        except Exception as e:
            logger.error(f"[Daemon] Execution failed: {e}")
            job.status = InferStatus.FAILED
            return None

    def run(self):
        logger.info(f"[Daemon] Starting — wallet={self.wallet.address[:20]}... "
                    f"models={self.worker.model_ids}")
        while True:
            self.run_once()
            time.sleep(self.poll_interval)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="BorderInfer Worker Daemon")
    parser.add_argument("--wallet",   required=True, help="Path to wallet JSON")
    parser.add_argument("--market",   default="http://localhost:8890")
    parser.add_argument("--models",   default="llama3:8b",
                        help="Comma-separated model IDs")
    parser.add_argument("--backend",  default="ollama",
                        choices=["ollama", "llama_cpp", "stub"])
    parser.add_argument("--vram",     type=float, default=8.0)
    parser.add_argument("--stake",    type=float, default=10.0)
    args = parser.parse_args()

    from phantom.blockchain import BorderWallet
    wallet  = BorderWallet.load(args.wallet)
    market  = InferMarket()
    backend = ModelBackend(args.backend)
    daemon  = InferDaemon(
        wallet       = wallet,
        market       = market,
        model_ids    = [m.strip() for m in args.models.split(",")],
        total_vram_gb= args.vram,
        backend      = backend,
        stake_bc     = args.stake,
    )
    daemon.run()
