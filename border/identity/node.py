"""
BorderID Node — FastAPI identity service

Routes:
  POST /identity/register          — register a new DID
  GET  /identity/{did}             — resolve DID document
  GET  /identity/handle/{handle}   — resolve by handle
  POST /identity/{did}/service     — add service endpoint
  POST /identity/{did}/claim       — add a verifiable claim
  GET  /identity/{did}/claims      — list claims for a DID
  GET  /identity/{did}/reputation  — get reputation score
  GET  /identity/search            — search registered DIDs
  GET  /identity/leaderboard       — top nodes by reputation
  GET  /identity/stats             — registry stats
  GET  /.border/identity           — service discovery
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import JSONResponse
    import uvicorn
    _FASTAPI = True
except ImportError:
    _FASTAPI = False

from .did import BorderDID, ServiceType
from .claim import VerifiableClaim, ClaimType
from .registry import IdentityRegistry
from .reputation import ReputationEngine

logger = logging.getLogger("border.identity.node")


class BorderIDNode:
    def __init__(
        self,
        node_address: str,
        persist_path: Optional[str] = None,
    ):
        self.node_address = node_address
        self.registry     = IdentityRegistry(persist_path=persist_path)
        self.reputation   = ReputationEngine(self.registry)
        self._app: Optional[Any] = None

    # ── Core API (usable without HTTP) ────────────────────
    def register_did(self, did_obj: BorderDID):
        ok, reason = self.registry.register(did_obj)
        if not ok:
            raise ValueError(reason)
        return did_obj

    def resolve(self, did_or_handle: str) -> Optional[BorderDID]:
        return self.registry.resolve(did_or_handle)

    def add_claim(self, claim: VerifiableClaim):
        ok, reason = self.registry.add_claim(claim)
        if not ok:
            raise ValueError(reason)
        return claim

    def get_reputation(self, did: str):
        return self.reputation.score(did)

    # ── FastAPI app ───────────────────────────────────────
    def build_app(self) -> Any:
        if not _FASTAPI:
            raise RuntimeError("fastapi not installed")

        app = FastAPI(title="BorderID", version="1.0.0")

        @app.get("/.border/identity")
        def discovery():
            return {
                "service":      "BorderID",
                "version":      "1.0",
                "node_address": self.node_address,
                "stats":        self.registry.stats,
            }

        @app.post("/identity/register")
        def register(body: dict):
            try:
                did_obj = BorderDID.from_dict(body)
                self.register_did(did_obj)
                return {"status": "registered", "did": did_obj.did}
            except Exception as e:
                raise HTTPException(400, str(e))

        @app.get("/identity/{did_str:path}")
        def resolve_did(did_str: str):
            doc = self.registry.resolve_document(did_str)
            if doc is None:
                raise HTTPException(404, f"DID not found: {did_str}")
            return doc

        @app.get("/identity/handle/{handle}")
        def resolve_handle(handle: str):
            did_obj = self.registry.resolve(handle)
            if did_obj is None:
                raise HTTPException(404, f"Handle not found: {handle}")
            return did_obj.to_document()

        @app.post("/identity/{did_str}/service")
        def add_service(did_str: str, body: dict):
            did_obj = self.registry.resolve(did_str)
            if did_obj is None:
                raise HTTPException(404, "DID not found")
            svc = did_obj.add_service(
                ServiceType(body["type"]),
                body["endpoint"],
                body.get("description", ""),
            )
            self.registry.update(did_obj)
            return {"status": "added", "service_id": svc.service_id}

        @app.post("/identity/{did_str}/claim")
        def add_claim(did_str: str, body: dict):
            try:
                claim = VerifiableClaim.from_dict(body)
                self.add_claim(claim)
                return {"status": "accepted", "claim_id": claim.claim_id}
            except Exception as e:
                raise HTTPException(400, str(e))

        @app.get("/identity/{did_str}/claims")
        def get_claims(did_str: str, claim_type: Optional[str] = None):
            ct = ClaimType(claim_type) if claim_type else None
            claims = self.registry.get_claims(did_str, claim_type=ct)
            return {"did": did_str, "claims": [c.to_dict() for c in claims]}

        @app.get("/identity/{did_str}/reputation")
        def get_reputation(did_str: str):
            return self.reputation.score(did_str).to_dict()

        @app.get("/identity/search")
        def search(service_type: Optional[str] = None,
                   region: Optional[str] = None,
                   min_stake: float = 0.0):
            st = ServiceType(service_type) if service_type else None
            results = self.registry.search(service_type=st, region=region,
                                           min_stake_bc=min_stake)
            return {"results": [d.to_dict() for d in results], "count": len(results)}

        @app.get("/identity/leaderboard")
        def leaderboard(top: int = 10):
            scores = self.reputation.leaderboard(top_n=top)
            return {"leaderboard": [s.to_dict() for s in scores]}

        @app.get("/identity/stats")
        def stats():
            return self.registry.stats

        self._app = app
        return app


def serve_identity(
    node_address: str,
    port: int = 9999,
    persist_path: Optional[str] = None,
    host: str = "0.0.0.0",
):
    node = BorderIDNode(node_address=node_address, persist_path=persist_path)
    app  = node.build_app()
    logger.info(f"[BorderID] Serving on {host}:{port}")
    import uvicorn
    uvicorn.run(app, host=host, port=port)
    return node
