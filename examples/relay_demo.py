"""
Border Relay Demo
=================
Tests the full relay -> proof -> chain -> mine pipeline:

  1. Open relay sessions and forward simulated traffic
  2. Verify BandwidthProofs are auto-submitted to the chain
  3. Mine a block via the relay's mine loop
  4. Verify miner wallet is credited
  5. Test the /relay/* HTTP endpoints
  6. Test session close emits a proof for un-flushed bytes
"""

import sys, os, time, json, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from border.blockchain.chain import BorderChain
from border.blockchain.wallet import BorderWallet
from border.relay import BorderRelay, PROOF_FLUSH_BYTES
from border.obfuscate import BorderSession

print("=" * 60)
print("Border Relay -> Chain Demo")
print("=" * 60)

chain  = BorderChain()
wallet = BorderWallet.create()
relay  = BorderRelay(wallet=wallet, chain=chain, mine_interval=5)

# ------------------------------------------------------------------
# Step 1 -- Open sessions and forward bytes
# ------------------------------------------------------------------
print("\nStep 1: Open relay sessions")
sessions = [relay.open_session() for _ in range(3)]
print(f"  Opened {len(sessions)} sessions  OK")
stats = relay.stats()
assert stats["open_sessions"] == 3
print(f"  relay.stats(): open_sessions={stats['open_sessions']}  OK")

# ------------------------------------------------------------------
# Step 2 -- Simulate traffic -> proof auto-emit at PROOF_FLUSH_BYTES
# ------------------------------------------------------------------
print(f"\nStep 2: Simulate {PROOF_FLUSH_BYTES//(1024*1024)}MB of traffic to trigger proof")
sess = sessions[0]

# Manually inject bytes into the session stats to simulate forwarded traffic
# (avoids needing real network I/O in the demo)
with relay._lock:
    s = relay._sessions[sess.session_id]
    # Simulate just over the flush threshold
    s.bytes_out = PROOF_FLUSH_BYTES + 1024

# Now record one more byte which triggers the flush check
relay._record_bytes(sess.session_id, sent=1)
time.sleep(0.1)  # let the emit happen

proofs_in_chain = len(chain._pending_proofs)
print(f"  Pending proofs in chain: {proofs_in_chain}")
assert proofs_in_chain >= 1, "Expected at least 1 proof after flush"
print(f"  Proof auto-emitted at {PROOF_FLUSH_BYTES//(1024*1024)}MB threshold  OK")

# ------------------------------------------------------------------
# Step 3 -- Close a session -> proof for remaining bytes
# ------------------------------------------------------------------
print("\nStep 3: Close session -> proof for remaining bytes")
sess2 = sessions[1]
# Inject some bytes below flush threshold
with relay._lock:
    s2 = relay._sessions[sess2.session_id]
    s2.bytes_out = 500 * 1024   # 500 KB

before = len(chain._pending_proofs)
relay.close_session(sess2.session_id)
after  = len(chain._pending_proofs)
assert after > before, "Close session should emit a proof"
print(f"  Proofs before close={before}  after={after}  OK")

# ------------------------------------------------------------------
# Step 4 -- Add enough proofs to mine a block
# ------------------------------------------------------------------
print("\nStep 4: Load enough bandwidth to mine a block")
import border.blockchain.block as _bm
needed = _bm.MIN_BYTES_PER_BLOCK
current = sum(p.bytes_forwarded for p in chain._pending_proofs)
print(f"  Current pending: {current//(1024*1024)}MB  needed: {needed//(1024*1024)}MB")

# Add proofs directly to fill the gap
from border.blockchain.block import BandwidthProof
gap = needed - current + (5 * 1024 * 1024)   # 5MB headroom
proof = BandwidthProof(
    receipt_id     = "rcpt_demo_fill",
    relay_address  = wallet.address,
    client_id      = "demo_client",
    bytes_forwarded= gap,
    timestamp      = time.time(),
    session_id     = "demo_fill_sess",
    relay_signature= wallet.sign(f"rcpt_demo_fill:{wallet.address}:{gap}".encode()),
)
chain.add_proof(proof)
current2 = sum(p.bytes_forwarded for p in chain._pending_proofs)
print(f"  After top-up: {current2//(1024*1024)}MB  OK")

# ------------------------------------------------------------------
# Step 5 -- Mine manually via relay's mine loop logic
# ------------------------------------------------------------------
print("\nStep 5: Mine block via relay mining logic")
block = chain.create_block(miner_address=wallet.address)
assert block is not None, "Should be able to mine now"
ok, reason = chain.add_block(block)
assert ok, f"Block rejected: {reason}"
miner_reward = block.transactions[0].amount
print(f"  Block #{block.index} mined  reward={miner_reward:.4f} BC  OK")

balance = chain.get_balance(wallet.address)
assert balance >= miner_reward
print(f"  Miner balance={balance:.4f} BC  OK")

# ------------------------------------------------------------------
# Step 6 -- /relay/* HTTP endpoints via node_runner
# ------------------------------------------------------------------
print("\nStep 6: /relay/* HTTP endpoints")
import requests
_app_client = None

class _FR:
    def __init__(self, sc, data): self.status_code=sc; self._d=data
    def json(self): return json.loads(self._d)

def _fake_get(url, **kw):
    from urllib.parse import urlparse, urlencode
    p = urlparse(url); path = p.path
    if kw.get("params"): path += "?" + urlencode(kw["params"])
    r = _app_client.get(path); return _FR(r.status_code, r.data)

def _fake_post(url, json=None, **kw):
    r = _app_client.post(
        __import__("urllib.parse", fromlist=["urlparse"]).urlparse(url).path,
        data=__import__("json").dumps(json).encode(),
        content_type="application/json"
    )
    return _FR(r.status_code, r.data)

requests.get = _fake_get; requests.post = _fake_post

from border.node_runner import BorderNode

with tempfile.TemporaryDirectory() as td:
    node = BorderNode("127.0.0.1", 19500, td, peers=[])
    _app_client = node.app.test_client()

    # /relay/status
    resp = _app_client.get("/relay/status")
    d = json.loads(resp.data)
    assert "open_sessions" in d
    print(f"  /relay/status: open_sessions={d['open_sessions']}  proof_queue={d['proof_queue']}  OK")

    # /relay/session/open
    resp = _app_client.post("/relay/session/open", data=b"{}",
                            content_type="application/json")
    d = json.loads(resp.data)
    assert "session_id" in d and "public_key" in d
    sid = d["session_id"]
    print(f"  /relay/session/open: session_id={sid[:20]}...  OK")

    # /relay/session/close
    resp = _app_client.post("/relay/session/close",
        data=json.dumps({"session_id": sid}).encode(),
        content_type="application/json")
    assert json.loads(resp.data)["ok"] is True
    print(f"  /relay/session/close: ok=True  OK")

    # /status includes relay stats
    resp = _app_client.get("/status")
    d = json.loads(resp.data)
    assert "relay" in d
    print(f"  /status includes relay section  OK")

# ------------------------------------------------------------------
# Step 7 -- Mining daemon thread auto-mines
# ------------------------------------------------------------------
print("\nStep 7: Mining daemon auto-mines when proofs accumulate")
chain2  = BorderChain()
wallet2 = BorderWallet.create()
relay2  = BorderRelay(wallet=wallet2, chain=chain2, mine_interval=1)

# Pre-load enough proofs
for i in range(2):
    chain2.add_proof(BandwidthProof(
        receipt_id     = f"auto_rcpt_{i}",
        relay_address  = wallet2.address,
        client_id      = f"auto_client_{i}",
        bytes_forwarded= 55 * 1024 * 1024,
        timestamp      = time.time(),
        session_id     = f"auto_sess_{i}",
        relay_signature= wallet2.sign(f"auto_rcpt_{i}:{wallet2.address}:57671680".encode()),
    ))

relay2.start()
# Wait for mining daemon to fire (mine_interval=1s)
time.sleep(3)
relay2.stop()

height = chain2.height
assert height >= 1, f"Expected mining daemon to mine at least 1 block, got height={height}"
print(f"  Mining daemon auto-mined {height} block(s)  OK")

print()
print("=" * 60)
print("All relay demo steps passed!")
print(f"  Relay -> BandwidthProof -> Chain pipeline verified")
print(f"  Auto-flush at {PROOF_FLUSH_BYTES//(1024*1024)}MB threshold verified")
print(f"  Mining daemon auto-mines when proofs accumulate verified")
print("=" * 60)
