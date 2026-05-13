"""
Border P2P - HTTP server for peer-to-peer protocol endpoints.

Mounts onto /p2p/* and handles:
  GET  /p2p/ping          -- liveness check, returns chain height
  GET  /p2p/peers         -- returns our peer table
  POST /p2p/announce      -- peer tells us about itself
  POST /p2p/gossip        -- receive a gossip envelope
  GET  /p2p/block_hash    -- return hash at a given block index
  GET  /p2p/blocks        -- return a range of blocks (for sync)
  POST /p2p/tx            -- accept a new transaction into mempool
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from flask import Blueprint, jsonify, request

if TYPE_CHECKING:
    pass

logger = logging.getLogger("border.p2p.server")


def create_p2p_blueprint(node: "P2PNode") -> Blueprint:
    """
    Build and return a Flask Blueprint wired to a P2PNode instance.
    Mount it on a Flask app with: app.register_blueprint(bp)
    """
    bp = Blueprint("p2p", __name__)

    # ------------------------------------------------------------------
    # Liveness / peer exchange
    # ------------------------------------------------------------------

    @bp.route("/p2p/ping", methods=["GET"])
    def ping():
        from_host = request.args.get("from_host", "")
        from_port = int(request.args.get("from_port", 0))
        peer_node_id = request.args.get("node_id", "")
        if from_host and from_port:
            node.discovery.add_peer(from_host, from_port, peer_node_id)
        return jsonify({
            "ok": True,
            "chain_height": node.chain.height,
            "node_id": node.node_id,
            "version": "0.1.0",
        })

    @bp.route("/p2p/peers", methods=["GET"])
    def get_peers():
        peers = node.discovery.get_peers(reachable_only=True)
        return jsonify({"peers": [p.to_dict() for p in peers]})

    @bp.route("/p2p/announce", methods=["POST"])
    def announce():
        data = request.get_json(force=True) or {}
        host = data.get("host", "")
        port = int(data.get("port", 0))
        peer_node_id = data.get("node_id", "")
        if host and port:
            node.discovery.add_peer(host, port, peer_node_id)
        return jsonify({"ok": True})

    # ------------------------------------------------------------------
    # Gossip
    # ------------------------------------------------------------------

    @bp.route("/p2p/gossip", methods=["POST"])
    def gossip():
        envelope = request.get_json(force=True) or {}
        fresh = node.gossip.receive(envelope)
        return jsonify({"ok": True, "fresh": fresh})

    # ------------------------------------------------------------------
    # Block / chain sync
    # ------------------------------------------------------------------

    @bp.route("/p2p/block_hash", methods=["GET"])
    def block_hash():
        index = int(request.args.get("index", -1))
        h = node.chain.block_hash_at(index)
        if h is None:
            return jsonify({"error": "index out of range"}), 404
        return jsonify({"index": index, "hash": h})

    @bp.route("/p2p/blocks", methods=["GET"])
    def blocks():
        start = int(request.args.get("start", 0))
        end = int(request.args.get("end", start))
        end = min(end, start + 200)   # cap at 200 per request
        blks = node.chain.blocks_range(start, end)
        return jsonify({"blocks": [b.to_dict() for b in blks]})

    # ------------------------------------------------------------------
    # Transaction relay
    # ------------------------------------------------------------------

    @bp.route("/p2p/tx", methods=["POST"])
    def relay_tx():
        data = request.get_json(force=True) or {}
        from ..blockchain.transaction import Transaction
        try:
            tx = Transaction.from_dict(data)
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        ok = node.chain.add_transaction(tx)
        if ok:
            node.gossip.broadcast("transaction", tx.to_dict())
        return jsonify({"ok": ok})

    return bp
