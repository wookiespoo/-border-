"""
BorderRender Node — FastAPI render service

Routes:
  POST /render/job          — submit a render job
  GET  /render/job/{id}     — poll job status + result
  POST /render/result       — worker submits completed result
  POST /render/worker       — register a worker
  GET  /render/models       — list available models
  GET  /render/stats        — market stats
  GET  /.border/render      — service discovery
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

from .job    import RenderJob, RenderResult, RenderType, RenderStatus
from .market import RenderMarket, RenderWorker

logger = logging.getLogger("border.render.node")


class BorderRenderNode:
    def __init__(self, node_address: str):
        self.node_address = node_address
        self.market       = RenderMarket()
        self._app: Optional[Any] = None

    def build_app(self) -> Any:
        if not _FASTAPI:
            raise RuntimeError("fastapi not installed")

        app = FastAPI(title="BorderRender", version="1.0.0",
                      description="Decentralised GPU rendering — pay BC per frame")

        @app.get("/.border/render")
        def discovery():
            return {
                "service":      "BorderRender",
                "version":      "1.0",
                "node_address": self.node_address,
                "stats":        self.market.stats,
            }

        @app.post("/render/worker")
        def register_worker(body: dict):
            worker = RenderWorker(
                worker_id      = body.get("worker_id", ""),
                wallet_address = body["wallet_address"],
                endpoint       = body.get("endpoint", ""),
                model_ids      = body.get("model_ids", []),
                total_vram_gb  = body.get("total_vram_gb", 8.0),
                stake_bc       = body.get("stake_bc", 10.0),
                frames_per_min = body.get("frames_per_min", 2.0),
            )
            self.market.register_worker(worker)
            return {"status": "registered", "worker_id": worker.worker_id}

        @app.post("/render/job")
        def submit_job(body: dict):
            job = RenderJob.from_dict(body)
            ok, reason = self.market.submit_job(job)
            return {"job_id": job.job_id, "status": job.status,
                    "worker": job.worker_address, "reason": reason}

        @app.get("/render/job/{job_id}")
        def get_job(job_id: str):
            job = self.market.get_job(job_id)
            if not job:
                raise HTTPException(404, "Job not found")
            result = self.market.get_result(job_id)
            return {"job": job.to_dict(),
                    "result": result.to_dict() if result else None}

        @app.post("/render/result")
        def submit_result(body: dict):
            result = RenderResult(**body)
            ok, reason, bc = self.market.submit_result(result)
            if not ok:
                raise HTTPException(400, reason)
            return {"status": "accepted", "reward_bc": bc}

        @app.get("/render/models")
        def list_models():
            return {"models": self.market.stats["models_available"]}

        @app.get("/render/stats")
        def stats():
            return self.market.stats

        self._app = app
        return app


def serve_render(node_address: str, port: int = 8891, host: str = "0.0.0.0"):
    node = BorderRenderNode(node_address=node_address)
    app  = node.build_app()
    import uvicorn
    uvicorn.run(app, host=host, port=port)
    return node
