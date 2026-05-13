"""
BorderDNS Node -- FastAPI DNS service

Routes:
  POST /dns/register           -- register a .border name
  GET  /dns/resolve/{name}     -- resolve name -> records
  GET  /dns/address/{name}     -- resolve name -> BC address
  GET  /dns/did/{name}         -- resolve name -> DID
  GET  /dns/services/{name}    -- resolve name -> service endpoints
  POST /dns/transfer           -- transfer name to new owner
  GET  /dns/names/{address}    -- list all names owned by address
  GET  /dns/search             -- search names by keyword
  GET  /dns/stats              -- registry stats
  GET  /.border/dns            -- service discovery
"""

from __future__ import annotations

import logging
from typing import Any, Optional

try:
    from fastapi import FastAPI, HTTPException
    _FASTAPI = True
except ImportError:
    _FASTAPI = False

from .record   import DNSRecord, RecordType, REGISTRATION_FEE_BC
from .registry import DNSRegistry
from .resolver import DNSResolver

logger = logging.getLogger("border.dns.node")


class BorderDNSNode:
    def __init__(self, node_address: str, persist_path: Optional[str] = None):
        self.node_address = node_address
        self.registry     = DNSRegistry(persist_path=persist_path)
        self.resolver     = DNSResolver(self.registry)
        self._app: Optional[Any] = None

    def build_app(self) -> Any:
        if not _FASTAPI:
            raise RuntimeError("fastapi not installed")

        app = FastAPI(title="BorderDNS", version="1.0.0",
                      description="Decentralised naming for the Border network -- no registrar, no ICANN")

        @app.get("/.border/dns")
        def discovery():
            return {
                "service":      "BorderDNS",
                "version":      "1.0",
                "node_address": self.node_address,
                "stats":        self.registry.stats,
                "cache":        self.resolver.cache_stats,
            }

        @app.post("/dns/register")
        def register(body: dict):
            """
            Register a .border name. Body must include:
              owner_public_key  -- Ed25519 public key (base64) of the owner wallet
              owner_signature   -- wallet.sign(f"register:{name}:{owner_address}".encode())
            """
            try:
                record   = DNSRecord.from_dict(body)
                fee_paid = body.get("fee_paid", 0.0)
                ok, reason = self.registry.register(
                    record,
                    fee_paid         = fee_paid,
                    owner_public_key = body.get("owner_public_key", ""),
                    owner_signature  = body.get("owner_signature", ""),
                )
                if not ok:
                    raise HTTPException(400, reason)
                return {"status": "registered", "name": record.name,
                        "record_id": record.record_id}
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(400, str(e))

        @app.get("/dns/resolve/{name:path}")
        def resolve(name: str, record_type: Optional[str] = None):
            rt      = RecordType(record_type) if record_type else None
            records = self.resolver.resolve(name, rt)
            return {"name": name, "records": [r.to_dict() for r in records],
                    "count": len(records)}

        @app.get("/dns/address/{name:path}")
        def resolve_address(name: str):
            addr = self.resolver.resolve_address(name)
            if not addr:
                raise HTTPException(404, f"No address record for {name}")
            return {"name": name, "address": addr}

        @app.get("/dns/did/{name:path}")
        def resolve_did(name: str):
            did = self.resolver.resolve_did(name)
            if not did:
                raise HTTPException(404, f"No DID record for {name}")
            return {"name": name, "did": did}

        @app.get("/dns/services/{name:path}")
        def resolve_services(name: str, service_type: Optional[str] = None):
            srvs = self.resolver.resolve_services(name, service_type)
            return {"name": name, "services": [s.to_dict() for s in srvs]}

        @app.post("/dns/transfer")
        def transfer(body: dict):
            """
            Transfer a .border name. Body must include:
              from_public_key -- Ed25519 public key (base64) of the current owner
              from_signature  -- wallet.sign(f"transfer:{name}:{from_address}:{to_address}".encode())
            """
            ok, reason = self.registry.transfer(
                body["name"], body["from_address"],
                body["to_address"], body.get("fee_paid", 0.0),
                from_public_key = body.get("from_public_key", ""),
                from_signature  = body.get("from_signature", ""),
            )
            if not ok:
                raise HTTPException(400, reason)
            return {"status": "transferred", "name": body["name"],
                    "new_owner": body["to_address"]}

        @app.get("/dns/names/{address}")
        def names_for(address: str):
            names = self.registry.names_for(address)
            return {"address": address, "names": names, "count": len(names)}

        @app.get("/dns/search")
        def search(q: str = ""):
            results = self.registry.search(q)
            return {"query": q, "results": results, "count": len(results)}

        @app.get("/dns/stats")
        def stats():
            return {**self.registry.stats, "cache": self.resolver.cache_stats}

        self._app = app
        return app


def serve_dns(node_address: str, port: int = 9953,
              host: str = "0.0.0.0", persist_path: Optional[str] = None):
    node = BorderDNSNode(node_address=node_address, persist_path=persist_path)
    app  = node.build_app()
    import uvicorn
    uvicorn.run(app, host=host, port=port)
    return node
