"""
BorderInfer — Private AI inference on the Border network.

Your GPU. Your models. You earn BC per token.
No OpenAI. No Anthropic. No logs. No surveillance.

Usage:
    from phantom.infer import InferJob, InferType, InferMarket, InferDaemon

    # Client submits a job
    job = InferJob.create(
        infer_type=InferType.CHAT,
        client_address="BC_...",
        model_id="llama3:8b",
        messages=[{"role": "user", "content": "What is BorderInfer?"}],
        max_price_bc=0.001,
    )
    market.submit_job(job)

    # Worker daemon runs on your GPU rig
    daemon = InferDaemon(wallet=wallet, market=market,
                         model_ids=["llama3:8b", "mistral:7b"],
                         total_vram_gb=12.0)
    daemon.run()
"""

from .job    import (InferJob, InferResult, InferType, InferStatus,
                     BC_PER_1K_TOKENS_INPUT, BC_PER_1K_TOKENS_OUTPUT,
                     BC_PER_EMBEDDING, MIN_STAKE_TO_INFER)
from .model  import ModelSpec, ModelRegistry, ModelBackend
from .market import InferMarket, InferWorker
from .node   import BorderInferNode, serve_infer
from .daemon import InferDaemon

__all__ = [
    "InferJob", "InferResult", "InferType", "InferStatus",
    "BC_PER_1K_TOKENS_INPUT", "BC_PER_1K_TOKENS_OUTPUT",
    "BC_PER_EMBEDDING", "MIN_STAKE_TO_INFER",
    "ModelSpec", "ModelRegistry", "ModelBackend",
    "InferMarket", "InferWorker",
    "BorderInferNode", "serve_infer",
    "InferDaemon",
]
