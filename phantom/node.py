"""
Border Relay Node
Run this on any machine with free internet to become a relay.
You forward traffic for people in censored regions and earn BorderCoin.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import Dict, Optional

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .obfuscate import BorderObfuscator, BorderSession
from .ledger import BandwidthLedger, BandwidthReceipt

logger = logging.getLogger("border.node")


class BorderRelayNode:
    """
    A Border relay node.

    Accepts obfuscated requests from clients,
    forwards them to the real destination,
    returns obfuscated responses,
    and logs bandwidth for BorderCoin rewards.

    BorderCoin integration (optional):
        Pass `wallet` and `chain_endpoint` to automatically submit
        bandwidth proofs to a local BorderCoin blockchain node.
        Every byte forwarded = BorderCoin earned.

    Usage:
        # Basic relay (no blockchain)
        node = BorderRelayNode(node_id="mynode")

        # Relay + earn BorderCoin automatically
        from phantom.blockchain import BorderWallet
        wallet = BorderWallet.load("wallet.json")
        node = BorderRelayNode(
            node_id="mynode",
            wallet=wallet,
            chain_endpoint="http://localhost:7777",
        )
        app = node.create_app()
        uvicorn.run(app, host="0.0.0.0", port=8080)
    """

    def __init__(
        self,
        node_id: str,
        endpoint: str = "http://localhost:8080",
        region: str = "UNKNOWN",
        max_request_size_mb: int = 10,
        wallet=None,
        chain_endpoint: Optional[str] = None,
    ):
        self.node_id = node_id
        self.endpoint = endpoint
        self.region = region
        self.max_request_size = max_request_size_mb * 1024 * 1024
        self.obfuscator = BorderObfuscator()
        self.ledger = BandwidthLedger(node_id=node_id)
        self._sessions: Dict[str, BorderSession] = {}
        self._start_time = time.time()
        self._bytes_forwarded = 0
        self._requests_served = 0

        self.wallet = wallet
        self.chain_endpoint = chain_endpoint
        self._proofs_submitted = 0
        self._coin_earned = 0.0

        if wallet and chain_endpoint:
            logger.info(
                f"[Border Relay] BorderCoin enabled — "
                f"wallet={wallet.address[:20]}... chain={chain_endpoint}"
            )
        elif wallet or chain_endpoint:
            logger.warning(
                "[Border Relay] BorderCoin partially configured — "
                "pass both wallet and chain_endpoint to enable earning."
            )

        logger.info(f"[Border Relay] node_id={node_id} region={region} endpoint={endpoint}")

    def _receipt_to_proof(self, receipt: BandwidthReceipt) -> dict:
        return {
            "receipt_id": receipt.receipt_id,
            "relay_address": self.wallet.address,
            "client_id": receipt.client_id,
            "bytes_forwarded": receipt.bytes_forwarded,
            "timestamp": receipt.timestamp,
            "session_id": receipt.session_id,
            "relay_signature": receipt.signature or "unsigned",
            "client_signature": None,
        }

    async def _submit_proof(self, receipt: BandwidthReceipt) -> None:
        if not (self.wallet and self.chain_endpoint):
            return
        proof = self._receipt_to_proof(receipt)
        bc_value = receipt.bytes_forwarded / (1024 ** 3)
        try:
            async with httpx.AsyncClient(timeout=5) as http:
                resp = await http.post(f"{self.chain_endpoint}/proof", json=proof)
                if resp.status_code == 200 and resp.json().get("accepted"):
                    self._proofs_submitted += 1
                    self._coin_earned += bc_value
                    logger.info(
                        f"[Relay→Chain] Proof accepted ✓ "
                        f"+{bc_value:.6f} BC | total={self._coin_earned:.4f} BC"
                    )
        except Exception as e:
            logger.debug(f"[Relay→Chain] Chain unreachable: {e}")

    def create_app(self) -> FastAPI:
        app = FastAPI(
            title="Border Relay Node",
            description="A Border Protocol relay node. Forwards traffic for people in censored regions.",
            docs_url=None,
            redoc_url=None,
        )

        @app.post("/api/v1/data")
        async def handle_border_request(request: Request):
            return await self._handle_request(request)

        @app.get("/api/v1/health")
        async def health():
            return {"status": "ok", "ts": int(time.time() * 1000)}

        @app.get("/api/v1/status")
        async def status():
            stats = {
                "uptime": time.time() - self._start_time,
                "requests": self._requests_served,
                "bytes_forwarded": self._bytes_forwarded,
                "region": self.region,
            }
            if self.wallet:
                stats["bordercoin"] = {
                    "address": self.wallet.address,
                    "proofs_submitted": self._proofs_submitted,
                    "coin_earned_approx": round(self._coin_earned, 6),
                    "chain_endpoint": self.chain_endpoint,
                }
            return stats

        @app.get("/.border/card")
        async def node_card():
            card = {
                "border": "0.1",
                "node_id": self.node_id,
                "type": "RELAY",
                "endpoint": self.endpoint,
                "region": self.region,
                "uptime_seconds": time.time() - self._start_time,
                "bytes_forwarded": self._bytes_forwarded,
            }
            if self.wallet:
                card["bordercoin_address"] = self.wallet.address
            return card

        return app

    async def _handle_request(self, request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid request")

        session_id = body.get("mid", "default")
        if session_id not in self._sessions:
            session = BorderSession.create()
            self._sessions[session_id] = session
        else:
            session = self._sessions[session_id]

        try:
            payload = self.obfuscator.unwrap_request(body, session)
        except Exception as e:
            logger.warning(f"Failed to unwrap request: {e}")
            raise HTTPException(status_code=400, detail="Bad request")

        border_type = payload.get("type", "PROXY")

        if border_type == "HANDSHAKE":
            return await self._handle_handshake(payload, session)
        elif border_type == "PROXY":
            return await self._handle_proxy(payload, session, body)
        elif border_type == "PING":
            return await self._handle_ping(payload, session)
        else:
            raise HTTPException(status_code=400, detail="Unknown type")

    async def _handle_handshake(self, payload: dict, session: BorderSession) -> JSONResponse:
        logger.info(f"[Border Relay] HANDSHAKE from client")
        response = {
            "type": "HANDSHAKE_OK",
            "node_id": self.node_id,
            "session_id": session.session_id,
            "relay_pubkey": __import__("base64").b64encode(session.our_public_key_bytes).decode(),
        }
        wrapped = self.obfuscator.wrap_response(response, session)
        return JSONResponse(content=wrapped)

    async def _handle_proxy(self, payload: dict, session: BorderSession, raw_body: dict) -> JSONResponse:
        url = payload.get("url")
        method = payload.get("method", "GET").upper()
        headers = payload.get("headers", {})
        body = payload.get("body")
        client_id = payload.get("client_id", "unknown")

        if not url:
            raise HTTPException(status_code=400, detail="Missing url")

        safe_headers = {
            k: v for k, v in headers.items()
            if k.lower() not in ("host", "x-forwarded-for", "x-real-ip")
        }

        logger.info(f"[Border Relay] PROXY {method} {url[:60]}...")

        start = time.time()
        bytes_in = len(str(raw_body))

        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as http:
                response = await http.request(
                    method=method,
                    url=url,
                    headers=safe_headers,
                    content=body.encode() if body else None,
                )
                response_body = response.text
                status_code = response.status_code
        except httpx.TimeoutException:
            response_body = "Request timed out"
            status_code = 504
        except Exception as e:
            logger.warning(f"[Border Relay] Proxy error for {url}: {e}")
            response_body = str(e)
            status_code = 502

        duration_ms = int((time.time() - start) * 1000)
        bytes_out = len(response_body)
        total_bytes = bytes_in + bytes_out

        self._bytes_forwarded += total_bytes
        self._requests_served += 1

        receipt = self.ledger.record(
            client_id=client_id,
            bytes_forwarded=total_bytes,
            session_id=session.session_id,
        )

        if self.wallet and self.chain_endpoint:
            asyncio.create_task(self._submit_proof(receipt))

        result = {
            "type": "PROXY_RESPONSE",
            "status_code": status_code,
            "body": response_body[:50000],
            "duration_ms": duration_ms,
            "bytes": total_bytes,
            "receipt_id": receipt.receipt_id,
        }

        wrapped = self.obfuscator.wrap_response(result, session)
        return JSONResponse(content=wrapped)

    async def _handle_ping(self, payload: dict, session: BorderSession) -> JSONResponse:
        response = {
            "type": "PONG",
            "node_id": self.node_id,
            "uptime": time.time() - self._start_time,
            "bytes_forwarded": self._bytes_forwarded,
        }
        wrapped = self.obfuscator.wrap_response(response, session)
        return JSONResponse(content=wrapped)


def serve_relay(
    node_id: Optional[str] = None,
    host: str = "0.0.0.0",
    port: int = 8080,
    region: str = "UNKNOWN",
    wallet_path: Optional[str] = None,
    chain_endpoint: Optional[str] = None,
) -> None:
    """
    Start a Border relay node server.

    Args:
        node_id:        Unique node identifier (auto-generated if omitted)
        host:           Bind address
        port:           Bind port
        region:         Geographic region label (e.g. "US", "EU", "ASIA")
        wallet_path:    Path to BorderWallet JSON file (for earning BorderCoin)
        chain_endpoint: URL of local BorderChainNode (e.g. "http://localhost:7777")
    """
    import uvicorn

    if not node_id:
        node_id = hashlib.sha256(f"node-{time.time()}".encode()).hexdigest()[:16]

    endpoint = f"http://{host}:{port}"

    wallet = None
    if wallet_path:
        try:
            from .blockchain import BorderWallet
            wallet = BorderWallet.load(wallet_path)
            logger.info(f"[Border Relay] Loaded wallet: {wallet.address}")
        except Exception as e:
            logger.warning(f"[Border Relay] Could not load wallet: {e}")

    node = BorderRelayNode(
        node_id=node_id,
        endpoint=endpoint,
        region=region,
        wallet=wallet,
        chain_endpoint=chain_endpoint,
    )
    app = node.create_app()

    print(f"\n🌐 Border Relay Node")
    print(f"   Node ID : {node_id}")
    print(f"   Region  : {region}")
    print(f"   Endpoint: {endpoint}")
    if wallet:
        print(f"   Wallet  : {wallet.address}")
        print(f"   Chain   : {chain_endpoint or 'not connected'}")
        print(f"   Earning : 1 BC per GB forwarded + block rewards")
    else:
        print(f"   Earning : disabled (no wallet configured)")
    print(f"   Helping people in censored regions access the free internet\n")

    uvicorn.run(app, host=host, port=port, log_level="warning")
