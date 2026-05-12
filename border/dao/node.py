"""
BorderDAO Node — FastAPI governance service

Routes:
  POST /dao/proposal              — submit a proposal
  GET  /dao/proposal/{id}         — get proposal + current tally
  GET  /dao/proposals             — list all proposals
  POST /dao/proposal/{id}/vote    — cast a vote
  GET  /dao/proposal/{id}/votes   — get all votes for a proposal
  POST /dao/proposal/{id}/tally   — finalise voting
  POST /dao/proposal/{id}/execute — execute a passed proposal
  GET  /dao/treasury              — treasury stats
  GET  /dao/parameters            — current protocol parameters
  GET  /dao/stats                 — overall DAO stats
  GET  /.border/dao               — service discovery
"""

from __future__ import annotations

import logging
from typing import Any, Optional

try:
    from fastapi import FastAPI, HTTPException
    _FASTAPI = True
except ImportError:
    _FASTAPI = False

from .proposal  import Proposal, ProposalType, ProposalStatus
from .vote      import Vote, VoteChoice
from .governance import GovernanceEngine
from .treasury  import BorderTreasury

logger = logging.getLogger("border.dao.node")


class BorderDAONode:
    def __init__(self, node_address: str, total_supply_fn=None):
        self.node_address    = node_address
        self.treasury        = BorderTreasury()
        self.governance      = GovernanceEngine(treasury=self.treasury)
        self._total_supply_fn = total_supply_fn or (lambda: 1_000_000.0)
        self._app: Optional[Any] = None

    @property
    def total_supply(self) -> float:
        return self._total_supply_fn()

    def build_app(self) -> Any:
        if not _FASTAPI:
            raise RuntimeError("fastapi not installed")

        app = FastAPI(title="BorderDAO", version="1.0.0",
                      description="Community governance — BC holders decide the protocol")

        @app.get("/.border/dao")
        def discovery():
            return {
                "service":      "BorderDAO",
                "version":      "1.0",
                "node_address": self.node_address,
                "stats":        self.governance.stats,
            }

        @app.post("/dao/proposal")
        def submit_proposal(body: dict):
            try:
                proposal = Proposal.from_dict(body)
                balance  = body.get("proposer_balance", 0.0)
                ok, reason = self.governance.submit(proposal, balance)
                if not ok:
                    raise HTTPException(400, reason)
                return {"status": "submitted", "proposal_id": proposal.proposal_id}
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(400, str(e))

        @app.get("/dao/proposals")
        def list_proposals(status: Optional[str] = None):
            proposals = self.governance.all_proposals()
            if status:
                proposals = [p for p in proposals if p.status == status]
            return {"proposals": [p.to_dict() for p in proposals],
                    "count": len(proposals)}

        @app.get("/dao/proposal/{pid}")
        def get_proposal(pid: str):
            p = self.governance.get_proposal(pid)
            if not p:
                raise HTTPException(404, "Proposal not found")
            return p.to_dict()

        @app.post("/dao/proposal/{pid}/vote")
        def cast_vote(pid: str, body: dict):
            vote = Vote.from_dict({**body, "proposal_id": pid})
            ok, reason = self.governance.cast_vote(vote)
            if not ok:
                raise HTTPException(400, reason)
            p = self.governance.get_proposal(pid)
            return {"status": "vote_cast", "tally": p.to_dict() if p else {}}

        @app.get("/dao/proposal/{pid}/votes")
        def get_votes(pid: str):
            votes = self.governance.get_votes(pid)
            return {"votes": [v.to_dict() for v in votes], "count": len(votes)}

        @app.post("/dao/proposal/{pid}/tally")
        def tally(pid: str):
            status, msg = self.governance.tally(pid, self.total_supply)
            return {"proposal_id": pid, "status": status, "result": msg}

        @app.post("/dao/proposal/{pid}/execute")
        def execute(pid: str):
            ok, msg = self.governance.execute(pid)
            if not ok:
                raise HTTPException(400, msg)
            return {"status": "executed", "result": msg}

        @app.get("/dao/treasury")
        def treasury_stats():
            return self.treasury.stats

        @app.get("/dao/parameters")
        def parameters():
            return self.governance.parameters

        @app.get("/dao/stats")
        def stats():
            return self.governance.stats

        self._app = app
        return app


def serve_dao(node_address: str, port: int = 9990,
              host: str = "0.0.0.0", total_supply_fn=None):
    node = BorderDAONode(node_address=node_address,
                         total_supply_fn=total_supply_fn)
    app  = node.build_app()
    import uvicorn
    uvicorn.run(app, host=host, port=port)
    return node
