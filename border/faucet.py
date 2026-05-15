"""
border.faucet — Testnet BC faucet Flask blueprint.
Drips a fixed amount of testnet BC to any address, rate-limited per IP
and per recipient address.
Mount into node_runner with --faucet flag:
    GET  /faucet               — HTML UI page
    POST /faucet/drip-ui       — form submission handler
    GET  /faucet/info          — drip amount, cooldown, chain stats
    POST /faucet/drip          — {"address": "BC_..."} -> drips BC
    GET  /faucet/history       — recent drip log (last 50)
"""
from __future__ import annotations
import logging
import time
from collections import deque
from typing import Dict, Deque, Tuple
from flask import Blueprint, jsonify, request
logger = logging.getLogger("border.faucet")
# -- Config -------------------------------------------------------------------
DRIP_AMOUNT_BC    = 10.0          # BC sent per drip
COOLDOWN_IP_SEC   = 60 * 60       # 1 hour per IP
COOLDOWN_ADDR_SEC = 60 * 60 * 4   # 4 hours per address
MAX_HISTORY       = 50

class Faucet:
    """
    Stateful faucet -- tracks last-drip times per IP and address.
    Parameters
    ----------
    chain   : BorderChain to send transactions from
    wallet  : BorderWallet funding the drips (must have enough BC)
    """
    def __init__(self, chain, wallet):
        self.chain  = chain
        self.wallet = wallet
        self._last_ip:   Dict[str, float] = {}
        self._last_addr: Dict[str, float] = {}
        self._history: Deque[dict]        = deque(maxlen=MAX_HISTORY)

    def drip(self, address: str, requester_ip: str) -> Tuple[bool, str]:
        now = time.time()
        if not address.startswith("BC_") or len(address) < 10:
            return False, "Invalid address format -- must start with BC_"
        last_ip = self._last_ip.get(requester_ip, 0)
        if now - last_ip < COOLDOWN_IP_SEC:
            remaining = int(COOLDOWN_IP_SEC - (now - last_ip))
            return False, f"IP rate-limited -- try again in {remaining // 60}m {remaining % 60}s"
        last_addr = self._last_addr.get(address, 0)
        if now - last_addr < COOLDOWN_ADDR_SEC:
            remaining = int(COOLDOWN_ADDR_SEC - (now - last_addr))
            return False, f"Address rate-limited -- try again in {remaining // 60}m {remaining % 60}s"
        faucet_balance = self.chain.get_balance(self.wallet.address)
        if faucet_balance < DRIP_AMOUNT_BC:
            logger.warning(f"[Faucet] Insufficient balance: {faucet_balance:.2f} BC")
            return False, "Faucet is temporarily empty -- check back later"
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
        self._last_ip[requester_ip]  = now
        self._last_addr[address]     = now
        entry = {
            "address":   address,
            "amount_bc": DRIP_AMOUNT_BC,
            "timestamp": now,
            "tx_id":     tx.tx_id,
        }
        self._history.appendleft(entry)
        logger.info(f"[Faucet] Dripped {DRIP_AMOUNT_BC} BC -> {address[:16]}  tx={tx.tx_id[:12]}")
        return True, f"Sent {DRIP_AMOUNT_BC} BC to {address} (tx: {tx.tx_id[:16]}...)"

    def info(self) -> dict:
        balance = self.chain.get_balance(self.wallet.address)
        return {
            "drip_amount_bc":      DRIP_AMOUNT_BC,
            "cooldown_ip_hours":   COOLDOWN_IP_SEC  // 3600,
            "cooldown_addr_hours": COOLDOWN_ADDR_SEC // 3600,
            "faucet_address":      self.wallet.address,
            "faucet_balance_bc":   round(balance, 4),
            "network":             "testnet",
            "chain_height":        self.chain.height,
        }

    def history(self) -> list:
        return list(self._history)


# -- HTML UI ------------------------------------------------------------------

def _faucet_html(info: dict, address: str = "", msg: str = "", css: str = "") -> str:
    msg_block = f'<div class="msg {css}">{msg}</div>' if msg else ""
    bal   = info.get("faucet_balance_bc", 0)
    drip  = info.get("drip_amount_bc", 0)
    cd_ip = info.get("cooldown_ip_hours", 1)
    height = info.get("chain_height", 0)
    return (
        "<!DOCTYPE html>\n"
        "<html lang=\"en\">\n"
        "<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">\n"
        "<title>Border Testnet Faucet</title>\n"
        "<style>\n"
        "  *{box-sizing:border-box;margin:0;padding:0}\n"
        "  body{background:#0d1117;color:#e6edf3;font-family:'Segoe UI',system-ui,sans-serif;"
        "min-height:100vh;display:flex;flex-direction:column;align-items:center;"
        "justify-content:center;padding:2rem}\n"
        "  h1{font-size:2rem;font-weight:700;margin-bottom:.25rem;color:#58a6ff}\n"
        "  .sub{color:#8b949e;margin-bottom:2rem;font-size:.95rem}\n"
        "  .cards{display:flex;gap:1rem;margin-bottom:2rem;flex-wrap:wrap;justify-content:center}\n"
        "  .card{background:#161b22;border:1px solid #30363d;border-radius:10px;"
        "padding:1.2rem 1.8rem;text-align:center;min-width:140px}\n"
        "  .card-label{font-size:.75rem;color:#8b949e;text-transform:uppercase;"
        "letter-spacing:.05em;margin-bottom:.4rem}\n"
        "  .card-val{font-size:1.4rem;font-weight:600;color:#58a6ff}\n"
        "  form{background:#161b22;border:1px solid #30363d;border-radius:12px;"
        "padding:2rem;width:100%;max-width:480px}\n"
        "  label{display:block;font-size:.85rem;color:#8b949e;margin-bottom:.5rem}\n"
        "  input[type=text]{width:100%;background:#0d1117;border:1px solid #30363d;"
        "border-radius:6px;padding:.75rem 1rem;color:#e6edf3;font-size:.95rem;"
        "outline:none;transition:border-color .2s}\n"
        "  input[type=text]:focus{border-color:#58a6ff}\n"
        "  button{margin-top:1rem;width:100%;background:#238636;border:none;"
        "border-radius:6px;padding:.75rem;color:#fff;font-size:1rem;"
        "font-weight:600;cursor:pointer;transition:background .2s}\n"
        "  button:hover{background:#2ea043}\n"
        "  .msg{margin-top:1rem;padding:.75rem 1rem;border-radius:6px;font-size:.9rem}\n"
        "  .ok{background:#0d2818;border:1px solid #238636;color:#3fb950}\n"
        "  .err{background:#2d1117;border:1px solid #da3633;color:#f85149}\n"
        "  .footer{margin-top:2rem;font-size:.8rem;color:#484f58}\n"
        "</style>\n"
        "</head>\n"
        "<body>\n"
        "<h1>Border Testnet Faucet</h1>\n"
        "<p class=\"sub\">Get free testnet BC tokens</p>\n"
        "<div class=\"cards\">\n"
        f"  <div class=\"card\"><div class=\"card-label\">Balance</div>"
        f"<div class=\"card-val\">{bal} BC</div></div>\n"
        f"  <div class=\"card\"><div class=\"card-label\">Drip Amount</div>"
        f"<div class=\"card-val\">{drip} BC</div></div>\n"
        f"  <div class=\"card\"><div class=\"card-label\">Cooldown</div>"
        f"<div class=\"card-val\">{cd_ip}h</div></div>\n"
        f"  <div class=\"card\"><div class=\"card-label\">Block Height</div>"
        f"<div class=\"card-val\">{height}</div></div>\n"
        "</div>\n"
        "<form method=\"POST\" action=\"/faucet/drip-ui\">\n"
        "<label for=\"address\">Your Border Wallet Address</label>\n"
        f"  <input type=\"text\" id=\"address\" name=\"address\" "
        f"placeholder=\"BC_...\" value=\"{address}\" autocomplete=\"off\" required>\n"
        f"  {msg_block}\n"
        "  <button type=\"submit\">Request Tokens</button>\n"
        "</form>\n"
        "<p class=\"footer\">Border Protocol &mdash; Testnet</p>\n"
        "</body>\n"
        "</html>\n"
    )


# -- Flask blueprint factory --------------------------------------------------

def make_faucet_blueprint(faucet: Faucet) -> Blueprint:
    bp = Blueprint("faucet", __name__)

    @bp.route("/faucet", methods=["GET"])
    def faucet_ui():
        return _faucet_html(faucet.info()), 200, {"Content-Type": "text/html; charset=utf-8"}

    @bp.route("/faucet/drip-ui", methods=["POST"])
    def faucet_drip_ui():
        address = request.form.get("address", "").strip()
        msg, css = "", ""
        if address:
            ip = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown")
            ip = ip.split(",")[0].strip()
            ok, result = faucet.drip(address, ip)
            msg = result
            css = "ok" if ok else "err"
        return _faucet_html(faucet.info(), address, msg, css), 200, {"Content-Type": "text/html; charset=utf-8"}

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
        requester_ip = requester_ip.split(",")[0].strip()
        ok, msg = faucet.drip(address, requester_ip)
        status  = 200 if ok else 429
        return jsonify({"ok": ok, "message": msg}), status

    @bp.route("/faucet/history", methods=["GET"])
    def faucet_history():
        return jsonify({"drips": faucet.history()})

    return bp
