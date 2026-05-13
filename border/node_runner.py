"""
border.node_runner — Unified Border node.

Boots all subsystems in a single process:
  - Flask HTTP server (blockchain + P2P routes)
  - P2PNode (peer discovery, gossip, chain sync)
  - BorderStorageNode  (optional, --storage)
  - BorderComputeNode  (optional, --compute)
  - BorderDNSNode      (optional, --dns)

Usage
-----
  python -m border.node_runner --port 9000 --data-dir ~/.border \
      --peers seed.border.network:9000 \
      --storage --compute --dns

Environment variables (override CLI flags):
  BORDER_PORT, BORDER_DATA_DIR, BORDER_PEERS
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
from pathlib import Path
from typing import List, Optional

from flask import Flask, jsonify, request

from .blockchain.chain import BorderChain
from .relay import BorderRelay
from .blockchain.wallet import BorderWallet
# BorderChainNode available at border.blockchain.node if needed
from .p2p.node import P2PNode
from .p2p.server import create_p2p_blueprint

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("border.node_runner")


# ---------------------------------------------------------------------------
# Helper — load or create wallet
# ---------------------------------------------------------------------------

def _load_wallet(data_dir: Path) -> BorderWallet:
    wallet_path = str(data_dir / "wallet.json")
    if Path(wallet_path).exists():
        try:
            w = BorderWallet.load(wallet_path)
            logger.info(f"[Node] Loaded wallet  address={w.address}")
            return w
        except Exception as e:
            logger.warning(f"[Node] Could not load wallet ({e}), generating new one")
    w = BorderWallet.create()
    w.save(wallet_path)
    logger.info(f"[Node] Created wallet  address={w.address}")
    return w


# ---------------------------------------------------------------------------
# BorderNode — assembles all subsystems
# ---------------------------------------------------------------------------

class BorderNode:
    """
    All-in-one Border protocol node.

    Parameters
    ----------
    host        : bind address (default 0.0.0.0)
    port        : HTTP listen port (default 9000)
    data_dir    : persistent storage root
    peers       : bootstrap peer addresses  host:port
    enable_storage, enable_compute, enable_dns : optional subsystems
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 9000,
        data_dir: str = "~/.border",
        peers: Optional[List[str]] = None,
        enable_storage: bool = False,
        enable_compute: bool = False,
        enable_dns: bool = False,
    ):
        self.host = host
        self.port = port
        self.data_dir = Path(data_dir).expanduser()
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.wallet = _load_wallet(self.data_dir)

        # Core blockchain
        self.chain = BorderChain(
            persist_path=str(self.data_dir / "chain.json")
        )

        # P2P layer
        self.p2p = P2PNode(
            chain=self.chain,
            self_host=host,
            self_port=port,
            seeds=peers or [],
            data_dir=str(self.data_dir),
        )

        # Relay layer — proof-of-bandwidth + mining daemon
        self.relay = BorderRelay(
            wallet=self.wallet,
            chain=self.chain,
            p2p=self.p2p,
        )

        # Flask app
        self.app = Flask("border-node")
        self._register_core_routes()
        self.app.register_blueprint(create_p2p_blueprint(self.p2p))

        # Optional subsystems (mounted on sub-ports via threads)
        self._storage_node = None
        self._compute_node = None
        self._dns_node = None

        if enable_storage:
            self._init_storage()
        if enable_compute:
            self._init_compute()
        if enable_dns:
            self._init_dns()

    # -------------------------------------------------------------------
    # Core HTTP routes
    # -------------------------------------------------------------------

    def _register_core_routes(self) -> None:
        app = self.app

        @app.route("/status", methods=["GET"])
        def status():
            return jsonify({
                "node": self.p2p.status(),
                "chain": self.chain.stats,
                "relay": self.relay.stats(),
                "wallet_address": self.wallet.address,
                "subsystems": {
                    "storage": self._storage_node is not None,
                    "compute": self._compute_node is not None,
                    "dns":     self._dns_node is not None,
                },
            })

        @app.route("/chain/height", methods=["GET"])
        def chain_height():
            return jsonify({"height": self.chain.height})

        @app.route("/chain/block/<int:index>", methods=["GET"])
        def get_block(index):
            blks = self.chain.blocks_range(index, index)
            if not blks:
                return jsonify({"error": "not found"}), 404
            return jsonify(blks[0].to_dict())

        @app.route("/chain/balance/<address>", methods=["GET"])
        def get_balance(address):
            return jsonify({"address": address,
                            "balance": self.chain.get_balance(address)})

        @app.route("/chain/tx", methods=["POST"])
        def submit_tx():
            from .blockchain.transaction import Transaction
            data = request.get_json(force=True) or {}
            try:
                tx = Transaction.from_dict(data)
            except Exception as e:
                return jsonify({"ok": False, "error": str(e)}), 400
            ok = self.chain.add_transaction(tx)
            if ok:
                self.p2p.broadcast_tx(tx)
            return jsonify({"ok": ok})

        @app.route("/chain/mine", methods=["POST"])
        def mine():
            block = self.chain.create_block(
                miner_address=self.wallet.address
            )
            if block is None:
                return jsonify({"ok": False,
                                "error": "not enough bandwidth proofs"}), 202
            ok, reason = self.chain.add_block(block)
            if ok:
                self.p2p.broadcast_block(block)
                return jsonify({"ok": True, "block": block.index,
                                "hash": block.block_hash})
            return jsonify({"ok": False, "error": reason}), 400

        @app.route("/wallet", methods=["GET"])
        def wallet_info():
            return jsonify({
                "address": self.wallet.address,
                "balance": self.chain.get_balance(self.wallet.address),
                "public_key": self.wallet.public_key_b64,
            })

        @app.route("/relay/status", methods=["GET"])
        def relay_status():
            return jsonify(self.relay.stats())

        @app.route("/relay/session/open", methods=["POST"])
        def relay_open():
            session = self.relay.open_session()
            import base64
            return jsonify({
                "session_id": session.session_id,
                "public_key": base64.b64encode(session.our_public_key_bytes).decode(),
            })

        @app.route("/relay/session/close", methods=["POST"])
        def relay_close():
            data = request.get_json(force=True) or {}
            sid = data.get("session_id", "")
            self.relay.close_session(sid)
            return jsonify({"ok": True})

    # -------------------------------------------------------------------
    # Optional subsystem boot helpers
    # -------------------------------------------------------------------

    def _init_storage(self) -> None:
        try:
            from .storage.node import BorderStorageNode
            storage_dir = str(self.data_dir / "storage")
            self._storage_node = BorderStorageNode(
                storage_dir=storage_dir,
                chain=self.chain,
                wallet=self.wallet,
            )
            logger.info("[Node] BorderStorageNode subsystem loaded")
        except Exception as e:
            logger.warning(f"[Node] Storage subsystem failed to load: {e}")

    def _init_compute(self) -> None:
        try:
            from .compute.market import ComputeMarket
            self._compute_node = ComputeMarket()
            logger.info("[Node] BorderComputeNode subsystem loaded")
        except Exception as e:
            logger.warning(f"[Node] Compute subsystem failed to load: {e}")

    def _init_dns(self) -> None:
        try:
            from .dns.registry import DNSRegistry as BorderDNSRegistry
            self._dns_node = BorderDNSRegistry()
            logger.info("[Node] BorderDNS subsystem loaded")
        except Exception as e:
            logger.warning(f"[Node] DNS subsystem failed to load: {e}")

    # -------------------------------------------------------------------
    # Run
    # -------------------------------------------------------------------

    def run(self) -> None:
        self.p2p.start()
        self.relay.start()
        logger.info(f"[Node] Border node starting  http://{self.host}:{self.port}")
        logger.info(f"[Node] Wallet  {self.wallet.address}")
        logger.info(f"[Node] Chain   height={self.chain.height}")
        logger.info(f"[Node] Peers   seeds={len(self.p2p.discovery.get_peers(False))}")
        self.app.run(host=self.host, port=self.port, threaded=True)


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        prog="border-node",
        description="Border protocol unified node runner",
    )
    parser.add_argument("--host",     default=os.environ.get("BORDER_HOST", "0.0.0.0"))
    parser.add_argument("--port",     type=int,
                        default=int(os.environ.get("BORDER_PORT", "9000")))
    parser.add_argument("--data-dir", default=os.environ.get("BORDER_DATA_DIR", "~/.border"))
    parser.add_argument("--peers",    nargs="*", default=[],
                        help="Bootstrap peers: host:port [host:port ...]")
    parser.add_argument("--storage",  action="store_true", help="Enable storage node")
    parser.add_argument("--compute",  action="store_true", help="Enable compute market")
    parser.add_argument("--dns",      action="store_true", help="Enable DNS registry")
    parser.add_argument("--verbose",  action="store_true")

    args = parser.parse_args(argv)

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Merge env-var peers
    env_peers = [p.strip() for p in
                 os.environ.get("BORDER_PEERS", "").split(",") if p.strip()]
    all_peers = (args.peers or []) + env_peers

    node = BorderNode(
        host=args.host,
        port=args.port,
        data_dir=args.data_dir,
        peers=all_peers,
        enable_storage=args.storage,
        enable_compute=args.compute,
        enable_dns=args.dns,
    )
    node.run()


if __name__ == "__main__":
    main()
