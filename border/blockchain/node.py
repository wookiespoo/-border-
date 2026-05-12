"""
BorderCoin P2P Network Node
Blockchain nodes sync with each other, broadcast new blocks,
and accept transactions. No central server. No authority.
"""

from __future__ import annotations

import logging
import time
from typing import List, Optional, Set

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .block import Block, BandwidthProof
from .chain import BorderChain
from .transaction import Transaction
from .wallet import BorderWallet

logger = logging.getLogger("border.blockchain.node")


class BorderChainNode:
    """
    A full BorderCoin node.
    Maintains the blockchain, syncs with peers, mines new blocks.

    Usage:
        wallet = BorderWallet.create()
        node = BorderChainNode(wallet=wallet, port=7777)
        node.run()
    """

    def __init__(
        self,
        wallet: BorderWallet,
        port: int = 7777,
        peers: Optional[List[str]] = None,
        persist_path: Optional[str] = None,
    ):
        self.wallet = wallet
        self.port = port
        self.endpoint = f"http://localhost:{port}"
        self.chain = BorderChain(persist_path=persist_path)
        self._peers: Set[str] = set(peers or [])
        self._start_time = time.time()

        logger.info(f"[ChainNode] Wallet: {wallet.address}")
        logger.info(f"[ChainNode] Chain height: {self.chain.height}")

    def create_app(self) -> FastAPI:
        app = FastAPI(
            title="BorderCoin Node",
            description="A BorderCoin blockchain node",
        )

        @app.get("/chain")
        async def get_chain():
            """Return the full chain."""
            return {
                "chain": [b.to_dict() for b in self.chain._chain],
                "length": len(self.chain._chain),
            }

        @app.get("/chain/stats")
        async def get_stats():
            """Chain statistics."""
            return {
                **self.chain.stats,
                "node_address": self.wallet.address,
                "peers": list(self._peers),
                "uptime": time.time() - self._start_time,
            }

        @app.get("/balance/{address}")
        async def get_balance(address: str):
            """Get balance for any address."""
            return {
                "address": address,
                "balance": self.chain.get_balance(address),
                "unit": "BC",
            }

        @app.get("/balances")
        async def get_all_balances():
            return self.chain.get_all_balances()

        @app.post("/transaction")
        async def submit_transaction(request: Request):
            """Submit a transaction to the mempool."""
            data = await request.json()
            tx = Transaction.from_dict(data)
            accepted = self.chain.add_transaction(tx)
            if not accepted:
                raise HTTPException(status_code=400, detail="Transaction rejected")
            await self._broadcast_transaction(tx)
            return {"status": "accepted", "tx_id": tx.tx_id}

        @app.post("/block")
        async def receive_block(request: Request):
            """Receive a new block from a peer."""
            data = await request.json()
            block = Block.from_dict(data)
            accepted, reason = self.chain.add_block(block)
            return {"accepted": accepted, "reason": reason}

        @app.post("/proof")
        async def submit_proof(request: Request):
            """Submit a bandwidth proof for mining."""
            data = await request.json()
            proof = BandwidthProof(**data)
            accepted = self.chain.add_proof(proof)
            if accepted:
                await self._try_mine_block()
            return {"accepted": accepted}

        @app.get("/peers")
        async def get_peers():
            return {"peers": list(self._peers)}

        @app.post("/peers")
        async def add_peer(request: Request):
            data = await request.json()
            peer = data.get("endpoint")
            if peer:
                self._peers.add(peer)
            return {"peers": list(self._peers)}

        @app.post("/sync")
        async def sync():
            """Sync with all known peers."""
            result = await self._sync_with_peers()
            return result

        return app

    async def _try_mine_block(self) -> Optional[Block]:
        """Try to produce a block if enough bandwidth proofs exist."""
        block = self.chain.create_block(
            miner_address=self.wallet.address,
        )
        if block:
            accepted, reason = self.chain.add_block(block)
            if accepted:
                logger.info(
                    f"[ChainNode] ⛏ Mined block #{block.index}! "
                    f"Reward: {block.total_bandwidth_pc + 1.0:.4f} PC"
                )
                await self._broadcast_block(block)
                return block
        return None

    async def _broadcast_block(self, block: Block) -> None:
        """Broadcast a new block to all peers."""
        data = block.to_dict()
        async with httpx.AsyncClient(timeout=5) as http:
            for peer in list(self._peers):
                try:
                    await http.post(f"{peer}/block", json=data)
                except Exception:
                    pass

    async def _broadcast_transaction(self, tx: Transaction) -> None:
        """Broadcast a transaction to all peers."""
        data = tx.to_dict()
        async with httpx.AsyncClient(timeout=5) as http:
            for peer in list(self._peers):
                try:
                    await http.post(f"{peer}/transaction", json=data)
                except Exception:
                    pass

    async def _sync_with_peers(self) -> dict:
        """Sync chain with peers — replace ours if they have a longer valid one."""
        replaced = False
        async with httpx.AsyncClient(timeout=10) as http:
            for peer in list(self._peers):
                try:
                    resp = await http.get(f"{peer}/chain")
                    data = resp.json()
                    peer_chain = [Block.from_dict(b) for b in data["chain"]]
                    if self.chain.replace_chain(peer_chain):
                        replaced = True
                        logger.info(f"[ChainNode] Chain replaced from peer {peer}")
                except Exception as e:
                    logger.debug(f"[ChainNode] Peer {peer} unreachable: {e}")

        return {
            "replaced": replaced,
            "height": self.chain.height,
            "peers_checked": len(self._peers),
        }

    def run(self, host: str = "0.0.0.0") -> None:
        """Start the blockchain node."""
        import uvicorn
        app = self.create_app()

        print(f"\n💎 BorderCoin Node")
        print(f"   Address : {self.wallet.address}")
        print(f"   Height  : {self.chain.height}")
        print(f"   Supply  : {self.chain.total_supply} PC")
        print(f"   Port    : {self.port}")
        print(f"   Mining  : Proof of Bandwidth")
        print(f"   Reward  : 1 PC per block + 1 PC per GB forwarded\n")

        uvicorn.run(app, host=host, port=self.port, log_level="warning")
