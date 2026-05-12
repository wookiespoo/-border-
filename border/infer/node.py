"""
BorderInfer Node — FastAPI inference service

Routes:
  POST /infer/job           — submit an inference job
  GET  /infer/job/{id}      — poll job status + result
  POST /infer/result        — worker submits completed result
  POST /infer/worker        — register a worker
  GET  /infer/models        — list available models
  GET  /infer/stats         — market stats
  GET  /.border/infer       — service discovery
"""

from __future__ import annotations

import logging
from typing import Any, Optional

try:
    from fastapi import FastAPI, HTTPException
    import uvicorn
    _FASTAPI = True
except ImportError:
    _FASTAPI = False

from .job    import InferJob, InferResult, InferType, InferStatus
from .market import InferMarket, InferWorker
from .model  import ModelRegistry, ModelBackend

logger = logging.getLogger("border.infer.node")


class BorderInferNode:
    def __init__(self, node_address: str):
        self.node_address = node_address
        self.market       = InferMarket()
        self.models       = ModelRegistry()
        self._app: Optional[Any] = None

    def build_app(self) -> Any:
        if not _FASTAPI:
            raise RuntimeError("fastapi not installed")

        app = FastAPI(title="BorderInfer", version="1.0.0",
                      description="Private AI inference — pay BC per token")

        @app.get("/.border/infer")
        def discovery():
            return {
                "service":      "BorderInfer",
                "version":      "1.0",
                "node_address": self.node_address,
                "stats":        self.market.stats,
            }

        @app.post("/infer/worker")
        def register_worker(body: dict):
            worker = InferWorker(
                worker_id      = body.get("worker_id", ""),
                wallet_address = body["wallet_address"],
                endpoint       = body.get("endpoint", ""),
                model_ids      = body.get("model_ids", []),
                total_vram_gb  = body.get("total_vram_gb", 8.0),
                stake_bc       = body.get("stake_bc", 5.0),
                tokens_per_s   = body.get("tokens_per_s", 30.0),
            )
            self.market.register_worker(worker)
            return {"status": "registered", "worker_id": worker.worker_id}

        @app.post("/infer/job")
        def submit_job(body: dict):
            job = InferJob.from_dict(body)
            ok, reason = self.market.submit_job(job)
            return {"job_id": job.job_id, "status": job.status,
                    "worker": job.worker_address, "reason": reason}

        @app.get("/infer/job/{job_id}")
        def get_job(job_id: str):
            job = self.market.get_job(job_id)
            if not job:
                raise HTTPException(404, "Job not found")
            result = self.market.get_result(job_id)
            return {"job": job.to_dict(),
                    "result": result.to_dict() if result else None}

        @app.post("/infer/result")
        def submit_result(body: dict):
            result = InferResult(**body)
            ok, reason, bc = self.market.submit_result(result)
            if not ok:
                raise HTTPException(400, reason)
            return {"status": "accepted", "reward_bc": bc}

        @app.get("/infer/models")
        def list_models():
            return {"models": self.models.to_list(),
                    "market_models": self.market.stats["models_available"]}

        @app.get("/infer/stats")
        def stats():
            return self.market.stats

        self._app = app
        return app


def serve_infer(node_address: str, port: int = 8890, host: str = "0.0.0.0"):
    node = BorderInferNode(node_address=node_address)
    app  = node.build_app()
    import uvicorn
    uvicorn.run(app, host=host, port=port)
    return node
