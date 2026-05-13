"""
border.dashboard — single-file HTML dashboard served at /dashboard.

Polls the node's own REST API every 5 seconds and renders:
  • Chain stats (height, mempool, difficulty)
  • Connected peers
  • Node wallet + balance
  • Relay stats (sessions, bytes forwarded)
  • Subsystem status (storage, compute, DNS, LoRa, faucet)
  • Recent blocks (last 10)
  • Live activity log

Mount via: app.register_blueprint(make_dashboard_blueprint())
"""

from flask import Blueprint, Response

_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Border Node Dashboard</title>
<style>
  :root {
    --bg: #0d1117; --surface: #161b22; --border: #30363d;
    --green: #3fb950; --yellow: #d29922; --red: #f85149;
    --blue: #58a6ff; --text: #e6edf3; --muted: #8b949e;
    --font: 'SF Mono', 'Fira Code', monospace;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: var(--font);
         font-size: 13px; padding: 16px; }
  h1 { font-size: 18px; color: var(--blue); margin-bottom: 16px; }
  h2 { font-size: 13px; color: var(--muted); text-transform: uppercase;
       letter-spacing: 1px; margin-bottom: 8px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px,1fr));
          gap: 12px; margin-bottom: 12px; }
  .card { background: var(--surface); border: 1px solid var(--border);
          border-radius: 6px; padding: 14px; }
  .row { display: flex; justify-content: space-between; align-items: center;
         padding: 4px 0; border-bottom: 1px solid var(--border); }
  .row:last-child { border-bottom: none; }
  .label { color: var(--muted); }
  .val { color: var(--text); font-weight: 600; }
  .green { color: var(--green); }
  .yellow { color: var(--yellow); }
  .red { color: var(--red); }
  .blue { color: var(--blue); }
  .dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block;
         margin-right: 6px; }
  .dot-green { background: var(--green); }
  .dot-red   { background: var(--red); }
  .dot-yellow{ background: var(--yellow); }
  #log { background: var(--surface); border: 1px solid var(--border);
         border-radius: 6px; padding: 10px; height: 160px; overflow-y: auto;
         font-size: 12px; color: var(--muted); }
  #log .entry { padding: 2px 0; border-bottom: 1px solid var(--border); }
  .blocks-table { width: 100%; border-collapse: collapse; font-size: 12px; }
  .blocks-table th { color: var(--muted); text-align: left; padding: 4px 8px;
                     border-bottom: 1px solid var(--border); }
  .blocks-table td { padding: 4px 8px; border-bottom: 1px solid var(--border); }
  .hash { color: var(--blue); font-size: 11px; }
  .badge { display: inline-block; padding: 1px 6px; border-radius: 10px;
           font-size: 11px; margin: 1px; }
  .badge-on  { background: #1a3a1a; color: var(--green); border: 1px solid var(--green); }
  .badge-off { background: #2a1a1a; color: var(--muted); border: 1px solid var(--border); }
  #refresh-bar { font-size: 11px; color: var(--muted); margin-bottom: 12px; }
  #countdown { color: var(--blue); }
  .addr { font-size: 11px; color: var(--muted); word-break: break-all; }
  .peer-entry { padding: 3px 0; border-bottom: 1px solid var(--border);
                font-size: 12px; }
  .peer-entry:last-child { border-bottom: none; }
</style>
</head>
<body>
<h1>⬡ Border Node Dashboard</h1>
<div id="refresh-bar">Auto-refresh in <span id="countdown">5</span>s
  &nbsp;|&nbsp; <span id="last-update">—</span></div>

<div class="grid">
  <!-- Chain -->
  <div class="card">
    <h2>Chain</h2>
    <div class="row"><span class="label">Height</span><span class="val" id="height">—</span></div>
    <div class="row"><span class="label">Difficulty</span><span class="val" id="difficulty">—</span></div>
    <div class="row"><span class="label">Mempool TXs</span><span class="val" id="mempool">—</span></div>
    <div class="row"><span class="label">Block reward</span><span class="val" id="block-reward">—</span></div>
    <div class="row"><span class="label">DB size</span><span class="val" id="db-size">—</span></div>
  </div>
  <!-- Wallet -->
  <div class="card">
    <h2>Node Wallet</h2>
    <div class="row"><span class="label">Balance</span><span class="val green" id="balance">—</span></div>
    <div class="row"><span class="label">Staked</span><span class="val yellow" id="staked">—</span></div>
    <div class="row" style="flex-direction:column;align-items:flex-start">
      <span class="label" style="margin-bottom:4px">Address</span>
      <span class="addr" id="wallet-addr">—</span>
    </div>
  </div>
  <!-- Relay -->
  <div class="card">
    <h2>Relay</h2>
    <div class="row"><span class="label">Active sessions</span><span class="val" id="sessions">—</span></div>
    <div class="row"><span class="label">Bytes forwarded</span><span class="val blue" id="bytes-fwd">—</span></div>
    <div class="row"><span class="label">Proofs submitted</span><span class="val" id="proofs">—</span></div>
    <div class="row"><span class="label">Blocks mined</span><span class="val green" id="blocks-mined">—</span></div>
  </div>
  <!-- Network -->
  <div class="card">
    <h2>Network</h2>
    <div class="row"><span class="label">Connected peers</span><span class="val" id="peer-count">—</span></div>
    <div class="row"><span class="label">Node ID</span><span class="val" id="node-id" style="font-size:11px">—</span></div>
    <div id="peer-list" style="margin-top:8px;max-height:80px;overflow-y:auto"></div>
  </div>
</div>

<!-- Subsystems -->
<div class="card" style="margin-bottom:12px">
  <h2>Subsystems</h2>
  <div style="margin-top:6px" id="subsystems">—</div>
</div>

<!-- Recent blocks -->
<div class="card" style="margin-bottom:12px">
  <h2>Recent Blocks</h2>
  <table class="blocks-table">
    <thead><tr>
      <th>#</th><th>Hash</th><th>Miner</th><th>TXs</th><th>BW Proofs</th><th>Time</th>
    </tr></thead>
    <tbody id="blocks-body"></tbody>
  </table>
</div>

<!-- Activity log -->
<div class="card">
  <h2>Activity Log</h2>
  <div id="log"></div>
</div>

<script>
const API = '';   // same origin
let countdown = 5;
let timer;

function fmt(n) {
  if (n === undefined || n === null) return '—';
  return n.toLocaleString();
}
function fmtBytes(b) {
  if (!b) return '0 B';
  const u = ['B','KB','MB','GB','TB'];
  let i = 0; while (b >= 1024 && i < u.length-1) { b /= 1024; i++; }
  return b.toFixed(2) + ' ' + u[i];
}
function fmtTime(ts) {
  if (!ts) return '—';
  return new Date(ts * 1000).toLocaleTimeString();
}
function truncate(s, n=16) { return s ? s.slice(0,n)+'…' : '—'; }

function log(msg, cls='') {
  const el = document.getElementById('log');
  const d = document.createElement('div');
  d.className = 'entry';
  d.innerHTML = `<span style="color:var(--muted)">${new Date().toLocaleTimeString()}</span>  <span class="${cls}">${msg}</span>`;
  el.prepend(d);
  while (el.children.length > 100) el.removeChild(el.lastChild);
}

async function fetchStatus() {
  try {
    const r = await fetch(API + '/status');
    if (!r.ok) throw new Error(r.status);
    return await r.json();
  } catch(e) {
    log('Failed to fetch /status: ' + e, 'red');
    return null;
  }
}

async function fetchBlocks(height) {
  if (height < 0) return [];
  const start = Math.max(0, height - 9);
  try {
    const r = await fetch(`${API}/p2p/blocks?start=${start}&end=${height}`);
    if (!r.ok) return [];
    return await r.json();
  } catch { return []; }
}

function renderStatus(s) {
  if (!s) return;
  const chain  = s.chain  || {};
  const node   = s.node   || {};
  const relay  = s.relay  || {};
  const subs   = s.subsystems || {};

  // Chain card
  document.getElementById('height').textContent       = fmt(chain.height);
  document.getElementById('difficulty').textContent   = fmt(chain.difficulty);
  document.getElementById('mempool').textContent      = fmt(chain.mempool_size);
  document.getElementById('block-reward').textContent = (chain.block_reward ?? '—') + ' BC';
  document.getElementById('db-size').textContent      = (chain.db_size_kb ?? '—') + ' KB';

  // Wallet card
  document.getElementById('balance').textContent  = (s.chain?.balance ?? chain.balance ?? '—') + ' BC';
  document.getElementById('staked').textContent   = (s.chain?.staked  ?? '—') + ' BC';
  document.getElementById('wallet-addr').textContent = s.wallet_address || '—';

  // Relay card
  document.getElementById('sessions').textContent    = fmt(relay.active_sessions);
  document.getElementById('bytes-fwd').textContent   = fmtBytes(relay.total_bytes_forwarded);
  document.getElementById('proofs').textContent      = fmt(relay.proofs_submitted);
  document.getElementById('blocks-mined').textContent= fmt(relay.blocks_mined);

  // Network card
  const peers = node.peers || [];
  document.getElementById('peer-count').textContent = peers.length;
  document.getElementById('node-id').textContent    = truncate(node.node_id || '', 20);
  const peerList = document.getElementById('peer-list');
  peerList.innerHTML = peers.length
    ? peers.map(p => `<div class="peer-entry"><span class="dot dot-green"></span>${p}</div>`).join('')
    : '<div class="peer-entry" style="color:var(--muted)">No peers connected</div>';

  // Subsystems
  const subNames = ['storage','compute','dns','lora','faucet'];
  document.getElementById('subsystems').innerHTML = subNames.map(k => {
    const on = subs[k];
    return `<span class="badge ${on ? 'badge-on' : 'badge-off'}">${k}</span>`;
  }).join(' ');
}

function renderBlocks(blocks) {
  const tbody = document.getElementById('blocks-body');
  if (!blocks || !blocks.length) {
    tbody.innerHTML = '<tr><td colspan="6" style="color:var(--muted);padding:8px">No blocks yet</td></tr>';
    return;
  }
  tbody.innerHTML = [...blocks].reverse().map(b => `
    <tr>
      <td class="val">${b.index}</td>
      <td class="hash">${(b.block_hash||'').slice(0,12)}…</td>
      <td class="addr">${(b.miner_address||'').slice(0,18)}…</td>
      <td>${(b.transactions||[]).length}</td>
      <td>${(b.bandwidth_proofs||[]).length}</td>
      <td style="color:var(--muted)">${fmtTime(b.timestamp)}</td>
    </tr>`).join('');
}

async function refresh() {
  document.getElementById('last-update').textContent = 'Updating…';
  const s = await fetchStatus();
  renderStatus(s);

  const height = s?.chain?.height ?? -1;
  const blocks = await fetchBlocks(height);
  renderBlocks(blocks);

  if (s) log(`Height=${height}  peers=${(s.node?.peers||[]).length}  mempool=${s.chain?.mempool_size ?? 0}`);
  document.getElementById('last-update').textContent = 'Updated ' + new Date().toLocaleTimeString();

  clearInterval(timer);
  countdown = 5;
  timer = setInterval(() => {
    countdown--;
    document.getElementById('countdown').textContent = countdown;
    if (countdown <= 0) { clearInterval(timer); refresh(); }
  }, 1000);
}

refresh();
</script>
</body>
</html>"""


def make_dashboard_blueprint() -> "Blueprint":
    bp = Blueprint("dashboard", __name__)

    @bp.route("/dashboard")
    def dashboard():
        return Response(_HTML, mimetype="text/html")

    return bp
