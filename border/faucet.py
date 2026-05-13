"""
border.faucet — Testnet BC faucet Flask blueprint.

Drips a fixed amount of testnet BC to any address, rate-limited per IP
and per recipient address.

Mount into node_runner with --faucet flag:
    GET  /faucet/info          — drip amount, cooldown, chain stats
    POST /faucet/drip          — {"address": "BC_..."} → drips BC
    GET  /faucet/history       — recent drip log (last 50)
"""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Dict, Deque, Tuple

from flask import Blueprint, jsonify, request

logger = logging.getLogger("border.faucet")

# ── Config ────────────────────────────────────────────────────────────────────

DRIP_AMOUNT_BC   = 10.0          # BC sent per drip
COOLDOWN_IP_SEC  = 60 * 60       # 1 hour per IP
COOLDOWN_ADDR_SEC= 60 * 60 * 4   # 4 hours per address
MAX_HISTORY      = 50


class Faucet:
    """
    Stateful faucet — tracks last-drip times per IP and address.

    Parameters
    ----------
    chain   : BorderChain to send transactions from
    wallet  : BorderWallet funding the drips (must have enough BC)
    """

    def __init__(self, chain, wallet):
        self.chain  = chain
        self.wallet = wallet
        self._last_ip:   Dict[str, float] = {}   # ip → last drip timestamp
        self._last_addr: Dict[str, float] = {}   # address → last drip timestamp
        self._history: Deque[dict]        = deque(maxlen=MAX_HISTORY)

    # ------------------------------------------------------------------ #
    # Core drip logic
    # ------------------------------------------------------------------ #

    def drip(self, address: str, requester_ip: str) -> Tuple[bool, str]:
        """
        Send DRIP_AMOUNT_BC to address if cooldowns allow.
        Returns (ok, message).
        """
        now = time.time()

        # Validate address format
        if not address.startswith("BC_") or len(address) < 10:
            return False, "Invalid address format — must start with BC_"

        # Rate limit by IP
        last_ip = self._last_ip.get(requester_ip, 0)
        if now - last_ip < COOLDOWN_IP_SEC:
            remaining = int(COOLDOWN_IP_SEC - (now - last_ip))
            return False, f"IP rate-limited — try again in {remaining // 60}m {remaining % 60}s"

        # Rate limit by address
        last_addr = self._last_addr.get(address, 0)
        if now - last_addr < COOLDOWN_ADDR_SEC:
            remaining = int(COOLDOWN_ADDR_SEC - (now - last_addr))
            return False, f"Address rate-limited — try again in {remaining // 60}m {remaining % 60}s"

        # Check faucet balance
        faucet_balance = self.chain.get_balance(self.wallet.address)
        if faucet_balance < DRIP_AMOUNT_BC:
            logger.warning(f"[Faucet] Insufficient balance: {faucet_balance:.2f} BC")
            return False, "Faucet is temporarily empty — check back later"

        # Create and submit transaction
        from border.blockchain.transaction import Transaction
        tx = Transaction.create(
            from_address = self.wallet.address,
            to_address   = address,
            amount       = DRIP_AMOUNT_BC,
            public_key   = self.wallet.public_key_b64,
            fee          = 0.0001,
        )
        tx.signature = self.wallet.sign(tx.signing_data())

        if not self.chain.add_transaction(tx):
            return False, "Transaction rejected by chain (check mempool / balance)"

        # Record drip
        self._last_ip[requester_ip]  = now
        self._last_addr[address]     = now
        entry = {
            "address":   address,
            "amount_bc": DRIP_AMOUNT_BC,
            "timestamp": now,
            "tx_id":     tx.tx_id,
        }
        self._history.appendleft(entry)
        logger.info(f"[Faucet] Dripped {DRIP_AMOUNT_BC} BC → {address[:16]}  tx={tx.tx_id[:12]}")
        return True, f"Sent {DRIP_AMOUNT_BC} BC to {address} (tx: {tx.tx_id[:16]}…)"

    def info(self) -> dict:
        balance = self.chain.get_balance(self.wallet.address)
        return {
            "drip_amount_bc":    DRIP_AMOUNT_BC,
            "cooldown_ip_hours": COOLDOWN_IP_SEC  // 3600,
            "cooldown_addr_hours": COOLDOWN_ADDR_SEC // 3600,
            "faucet_address":    self.wallet.address,
            "faucet_balance_bc": round(balance, 4),
            "network":           "testnet",
            "chain_height":      self.chain.height,
        }

    def history(self) -> list:
        return list(self._history)


# ── Flask blueprint factory ────────────────────────────────────────────────────

def make_faucet_blueprint(faucet: Faucet) -> Blueprint:
    bp = Blueprint("faucet", __name__)

    @bp.route("/faucet/info", methods=["GET"])
    def faucet_info():
        return jsonify(faucet.info())

    @bp.route("/faucet/drip", methods=["POST"])
    def faucet_drip():
        data = request.get_json(silent=True) or {}
        address = data.get("address", "").strip()
        if not address:
            return jsonify({"ok": False, "error": "missing 'address' field"}), 400

        requester_ip = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown")
        requester_ip = requester_ip.split(",")[0].strip()   # take first if proxied

        ok, msg = faucet.drip(address, requester_ip)
        status  = 200 if ok else 429
        return jsonify({"ok": ok, "message": msg}), status

    @bp.route("/faucet/history", methods=["GET"])
    def faucet_history():
        return jsonify({"drips": faucet.history()})

    return bp
