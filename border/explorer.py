"""
border.explorer — Block explorer served at /explorer.

Pages:
  GET /explorer                  — home: latest blocks + search bar
  GET /explorer/block/<index>    — block detail (txs, BW proofs, hashes)
  GET /explorer/tx/<tx_id>       — transaction detail
  GET /explorer/address/<addr>   — address: balance, sent/received history
  GET /explorer/search?q=<term>  — redirect to block/tx/address
"""

from flask import Blueprint, Response, request, redirect

# ── helpers ──────────────────────────────────────────────────────────────────

_CSS = """
<style>
:root{--bg:#0d1117;--surface:#161b22;--border:#30363d;--green:#3fb950;
  --yellow:#d29922;--red:#f85149;--blue:#58a6ff;--text:#e6edf3;
  --muted:#8b949e;--font:'SF Mono','Fira Code',monospace}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:var(--font);
  font-size:13px;padding:20px;max-width:1100px;margin:0 auto}
a{color:var(--blue);text-decoration:none}a:hover{text-decoration:underline}
h1{font-size:20px;color:var(--blue);margin-bottom:4px}
h2{font-size:13px;color:var(--muted);text-transform:uppercase;
  letter-spacing:1px;margin:20px 0 8px}
.nav{margin-bottom:20px;font-size:12px;color:var(--muted)}
.nav a{margin-right:12px}
.search{display:flex;gap:8px;margin-bottom:20px}
.search input{flex:1;background:var(--surface);border:1px solid var(--border);
  color:var(--text);padding:8px 12px;border-radius:6px;font-family:var(--font);
  font-size:13px}
.search button{background:var(--blue);color:#000;border:none;padding:8px 16px;
  border-radius:6px;cursor:pointer;font-family:var(--font);font-size:13px;
  font-weight:600}
table{width:100%;border-collapse:collapse;font-size:12px}
th{color:var(--muted);text-align:left;padding:6px 8px;
  border-bottom:1px solid var(--border)}
td{padding:6px 8px;border-bottom:1px solid var(--border);
  vertical-align:top}
tr:hover td{background:#1c2128}
.card{background:var(--surface);border:1px solid var(--border);
  border-radius:6px;padding:16px;margin-bottom:16px}
.kv{display:flex;flex-direction:column;gap:8px}
.kv-row{display:flex;gap:16px;padding:6px 0;
  border-bottom:1px solid var(--border)}
.kv-row:last-child{border-bottom:none}
.kv-label{color:var(--muted);min-width:160px;flex-shrink:0}
.kv-val{word-break:break-all}
.badge{display:inline-block;padding:1px 8px;border-radius:10px;font-size:11px}
.badge-blue{background:#0d2038;color:var(--blue);border:1px solid var(--blue)}
.badge-green{background:#0d2a12;color:var(--green);border:1px solid var(--green)}
.badge-yellow{background:#2a1f00;color:var(--yellow);border:1px solid var(--yellow)}
.hash{font-size:11px;color:var(--muted)}
.green{color:var(--green)} .red{color:var(--red)} .yellow{color:var(--yellow)}
.empty{color:var(--muted);padding:20px 0;text-align:center}
</style>
"""

_NAV = '<div class="nav"><a href="/explorer">⬡ Explorer</a><a href="/dashboard">Dashboard</a></div>'

def _page(title: str, body: str) -> str:
    return f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} — Border Explorer</title>{_CSS}</head><body>
{_NAV}{body}</body></html>"""

def _search_bar(val: str = "") -> str:
    return f"""<form class="search" action="/explorer/search" method="get">
<input name="q" placeholder="Search by block #, block hash, tx id, or address…" value="{val}">
<button type="submit">Search</button></form>"""

def _fmt_ts(ts) -> str:
    if not ts: return "—"
    import datetime
    return datetime.datetime.utcfromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M:%S UTC")

def _fmt_bc(v) -> str:
    if v is None: return "—"
    return f"{float(v):.8f} BC"

def _shorten(s: str, n: int = 20) -> str:
    if not s: return "—"
    return s[:n] + "…" if len(s) > n else s


# ── blueprint factory ─────────────────────────────────────────────────────────

def make_explorer_blueprint(chain) -> Blueprint:
    bp = Blueprint("explorer", __name__)

    # ── Home ──────────────────────────────────────────────────────────────────
    @bp.route("/explorer")
    def home():
        height = chain.height
        start  = max(0, height - 19)
        blocks = chain.blocks_range(start, height) if height >= 0 else []
        rows   = ""
        for b in reversed(blocks):
            txs    = len(b.transactions)
            proofs = len(b.bandwidth_proofs)
            rows += f"""<tr>
<td><a href="/explorer/block/{b.index}">{b.index}</a></td>
<td class="hash"><a href="/explorer/block/{b.index}">{b.block_hash[:20]}…</a></td>
<td><a href="/explorer/address/{b.miner_address}">{_shorten(b.miner_address,18)}</a></td>
<td>{txs}</td><td>{proofs}</td>
<td style="color:var(--muted)">{_fmt_ts(b.timestamp)}</td></tr>"""
        body = f"""<h1>⬡ Border Block Explorer</h1>
<p style="color:var(--muted);margin-bottom:16px">Chain height: <strong style="color:var(--text)">{height}</strong></p>
{_search_bar()}
<div class="card">
<h2>Latest Blocks</h2>
<table><thead><tr><th>#</th><th>Hash</th><th>Miner</th>
<th>TXs</th><th>BW Proofs</th><th>Time</th></tr></thead>
<tbody>{rows or '<tr><td colspan="6" class="empty">No blocks yet</td></tr>'}</tbody>
</table></div>"""
        return Response(_page("Home", body), mimetype="text/html")

    # ── Block detail ──────────────────────────────────────────────────────────
    @bp.route("/explorer/block/<int:index>")
    def block_detail(index: int):
        blks = chain.blocks_range(index, index)
        if not blks:
            body = f"<h1>Block #{index}</h1><p class='empty'>Block not found.</p>"
            return Response(_page(f"Block #{index}", body), mimetype="text/html"), 404
        b = blks[0]

        # Transactions table
        tx_rows = ""
        for tx in b.transactions:
            tx_rows += f"""<tr>
<td><a href="/explorer/tx/{tx.tx_id}">{_shorten(tx.tx_id,16)}</a></td>
<td><a href="/explorer/address/{tx.from_address}">{_shorten(tx.from_address,16)}</a></td>
<td><a href="/explorer/address/{tx.to_address}">{_shorten(tx.to_address,16)}</a></td>
<td class="green">{_fmt_bc(tx.amount)}</td>
<td style="color:var(--muted)">{_fmt_bc(tx.fee)}</td></tr>"""

        # BW proofs table
        proof_rows = ""
        for p in b.bandwidth_proofs:
            proof_rows += f"""<tr>
<td>{_shorten(p.receipt_id,16)}</td>
<td><a href="/explorer/address/{p.relay_address}">{_shorten(p.relay_address,16)}</a></td>
<td>{_shorten(p.client_id,14)}</td>
<td class="blue">{p.bytes_forwarded // (1024*1024)} MB</td>
<td style="color:var(--muted)">{_fmt_ts(p.timestamp)}</td></tr>"""

        nav_prev = f'<a href="/explorer/block/{index-1}">← #{index-1}</a>' if index > 0 else ""
        nav_next = f'<a href="/explorer/block/{index+1}">#{index+1} →</a>' if index < chain.height else ""

        body = f"""<h1>Block #{index}</h1>
<div style="display:flex;gap:16px;margin-bottom:16px;font-size:12px">{nav_prev} {nav_next}</div>
{_search_bar()}
<div class="card"><div class="kv">
<div class="kv-row"><span class="kv-label">Index</span><span class="kv-val">{b.index}</span></div>
<div class="kv-row"><span class="kv-label">Hash</span><span class="kv-val hash">{b.block_hash}</span></div>
<div class="kv-row"><span class="kv-label">Previous Hash</span><span class="kv-val hash">{b.previous_hash}</span></div>
<div class="kv-row"><span class="kv-label">Miner</span><span class="kv-val">
  <a href="/explorer/address/{b.miner_address}">{b.miner_address}</a></span></div>
<div class="kv-row"><span class="kv-label">Timestamp</span><span class="kv-val">{_fmt_ts(b.timestamp)}</span></div>
<div class="kv-row"><span class="kv-label">Difficulty</span><span class="kv-val">{b.difficulty}</span></div>
<div class="kv-row"><span class="kv-label">Nonce</span><span class="kv-val">{b.nonce}</span></div>
<div class="kv-row"><span class="kv-label">Transactions</span>
  <span class="kv-val">{len(b.transactions)}</span></div>
<div class="kv-row"><span class="kv-label">Bandwidth Proofs</span>
  <span class="kv-val">{len(b.bandwidth_proofs)}</span></div>
</div></div>

<div class="card"><h2>Transactions</h2>
<table><thead><tr><th>TX ID</th><th>From</th><th>To</th>
<th>Amount</th><th>Fee</th></tr></thead>
<tbody>{tx_rows or '<tr><td colspan="5" class="empty">No transactions</td></tr>'}</tbody>
</table></div>

<div class="card"><h2>Bandwidth Proofs</h2>
<table><thead><tr><th>Receipt ID</th><th>Relay</th><th>Client</th>
<th>Bytes</th><th>Time</th></tr></thead>
<tbody>{proof_rows or '<tr><td colspan="5" class="empty">No bandwidth proofs</td></tr>'}</tbody>
</table></div>"""
        return Response(_page(f"Block #{index}", body), mimetype="text/html")

    # ── Transaction detail ────────────────────────────────────────────────────
    @bp.route("/explorer/tx/<tx_id>")
    def tx_detail(tx_id: str):
        found_tx = None
        found_block = None
        for b in chain.blocks_range(0, chain.height):
            for tx in b.transactions:
                if tx.tx_id == tx_id:
                    found_tx = tx
                    found_block = b
                    break
            if found_tx:
                break

        if not found_tx:
            body = f"<h1>Transaction</h1><p class='empty'>Transaction {tx_id[:20]}… not found.</p>"
            return Response(_page("TX", body), mimetype="text/html"), 404

        body = f"""<h1>Transaction</h1>
{_search_bar()}
<div class="card"><div class="kv">
<div class="kv-row"><span class="kv-label">TX ID</span><span class="kv-val hash">{found_tx.tx_id}</span></div>
<div class="kv-row"><span class="kv-label">In Block</span><span class="kv-val">
  <a href="/explorer/block/{found_block.index}">#{found_block.index}</a></span></div>
<div class="kv-row"><span class="kv-label">From</span><span class="kv-val">
  <a href="/explorer/address/{found_tx.from_address}">{found_tx.from_address}</a></span></div>
<div class="kv-row"><span class="kv-label">To</span><span class="kv-val">
  <a href="/explorer/address/{found_tx.to_address}">{found_tx.to_address}</a></span></div>
<div class="kv-row"><span class="kv-label">Amount</span>
  <span class="kv-val green">{_fmt_bc(found_tx.amount)}</span></div>
<div class="kv-row"><span class="kv-label">Fee</span>
  <span class="kv-val">{_fmt_bc(found_tx.fee)}</span></div>
<div class="kv-row"><span class="kv-label">Timestamp</span>
  <span class="kv-val">{_fmt_ts(found_block.timestamp)}</span></div>
<div class="kv-row"><span class="kv-label">Signature</span>
  <span class="kv-val hash" style="font-size:10px;word-break:break-all">{found_tx.signature[:64]}…</span></div>
</div></div>"""
        return Response(_page("TX Detail", body), mimetype="text/html")

    # ── Address detail ────────────────────────────────────────────────────────
    @bp.route("/explorer/address/<address>")
    def address_detail(address: str):
        balance = chain.get_balance(address)
        staked  = chain.get_staked(address)

        sent, received, mined = [], [], []
        for b in chain.blocks_range(0, chain.height):
            if b.miner_address == address:
                mined.append(b)
            for tx in b.transactions:
                if tx.from_address == address:
                    sent.append((b, tx))
                elif tx.to_address == address:
                    received.append((b, tx))

        def tx_rows(pairs, direction):
            rows = ""
            for b, tx in reversed(pairs[-50:]):
                other = tx.to_address if direction == "sent" else tx.from_address
                amt_cls = "red" if direction == "sent" else "green"
                sign    = "−" if direction == "sent" else "+"
                rows += f"""<tr>
<td><a href="/explorer/tx/{tx.tx_id}">{_shorten(tx.tx_id,16)}</a></td>
<td><a href="/explorer/block/{b.index}">#{b.index}</a></td>
<td><a href="/explorer/address/{other}">{_shorten(other,16)}</a></td>
<td class="{amt_cls}">{sign}{_fmt_bc(tx.amount)}</td>
<td style="color:var(--muted)">{_fmt_ts(b.timestamp)}</td></tr>"""
            return rows or f'<tr><td colspan="5" class="empty">None</td></tr>'

        mined_rows = "".join(
            f'<tr><td><a href="/explorer/block/{b.index}">#{b.index}</a></td>'
            f'<td style="color:var(--muted)">{_fmt_ts(b.timestamp)}</td></tr>'
            for b in reversed(mined[-20:])
        ) or '<tr><td colspan="2" class="empty">None</td></tr>'

        body = f"""<h1>Address</h1>
{_search_bar(address)}
<div class="card"><div class="kv">
<div class="kv-row"><span class="kv-label">Address</span>
  <span class="kv-val">{address}</span></div>
<div class="kv-row"><span class="kv-label">Balance</span>
  <span class="kv-val green">{_fmt_bc(balance)}</span></div>
<div class="kv-row"><span class="kv-label">Staked</span>
  <span class="kv-val yellow">{_fmt_bc(staked)}</span></div>
<div class="kv-row"><span class="kv-label">Blocks mined</span>
  <span class="kv-val">{len(mined)}</span></div>
<div class="kv-row"><span class="kv-label">TXs sent</span>
  <span class="kv-val">{len(sent)}</span></div>
<div class="kv-row"><span class="kv-label">TXs received</span>
  <span class="kv-val">{len(received)}</span></div>
</div></div>

<div class="card"><h2>Received</h2>
<table><thead><tr><th>TX</th><th>Block</th><th>From</th><th>Amount</th><th>Time</th></tr></thead>
<tbody>{tx_rows(received,'received')}</tbody></table></div>

<div class="card"><h2>Sent</h2>
<table><thead><tr><th>TX</th><th>Block</th><th>To</th><th>Amount</th><th>Time</th></tr></thead>
<tbody>{tx_rows(sent,'sent')}</tbody></table></div>

<div class="card"><h2>Blocks Mined</h2>
<table><thead><tr><th>Block</th><th>Time</th></tr></thead>
<tbody>{mined_rows}</tbody></table></div>"""
        return Response(_page("Address", body), mimetype="text/html")

    # ── Search ────────────────────────────────────────────────────────────────
    @bp.route("/explorer/search")
    def search():
        q = (request.args.get("q") or "").strip()
        if not q:
            return redirect("/explorer")

        # Block number?
        if q.isdigit():
            return redirect(f"/explorer/block/{q}")

        # Block hash (64 hex chars)?
        if len(q) == 64 and all(c in "0123456789abcdefABCDEF" for c in q):
            return redirect(f"/explorer/block/{q}")  # will 404 if not found

        # Address?
        if q.startswith("BC_"):
            return redirect(f"/explorer/address/{q}")

        # TX id (32 hex chars)?
        if len(q) == 32 and all(c in "0123456789abcdef" for c in q.lower()):
            return redirect(f"/explorer/tx/{q}")

        # Fallback: try address
        return redirect(f"/explorer/address/{q}")

    return bp
