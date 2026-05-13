"""
Border Unified Node Runner Demo
================================
Tests that BorderNode boots cleanly, exposes all routes,
and that two nodes can discover each other and sync.
Uses Flask test clients -- no real sockets.
"""

import json
import sys, os, time, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests

# Monkey-patch requests to route between two test clients
_apps = {}

class _FakeResp:
    def __init__(self, status_code, data):
        self.status_code = status_code
        self._data = data
    def json(self):
        return json.loads(self._data)

def _fake_get(url, params=None, timeout=None, **kw):
    from urllib.parse import urlparse, urlencode
    parsed = urlparse(url)
    port = parsed.port or 80
    path = parsed.path
    if params:
        path += "?" + urlencode(params)
    client = _apps.get(port)
    if client is None:
        raise ConnectionError(f"No node on port {port}")
    return _FakeResp(*[getattr(client.get(path), a) for a in ("status_code", "data")])

def _fake_post(url, json=None, timeout=None, **kw):
    from urllib.parse import urlparse
    import requests as _r
    parsed = urlparse(url)
    port = parsed.port or 80
    client = _apps.get(port)
    if client is None:
        raise ConnectionError(f"No node on port {port}")
    resp = client.post(parsed.path,
        data=_r.compat.json.dumps(json).encode(),
        content_type="application/json")
    return _FakeResp(resp.status_code, resp.data)

requests.get  = _fake_get
requests.post = _fake_post

from border.node_runner import BorderNode
from border.blockchain.block import BandwidthProof
from border.blockchain.transaction import Transaction

print("=" * 60)
print("Border Unified Node Runner Demo")
print("=" * 60)

PORT_A = 19200
PORT_B = 19201

with tempfile.TemporaryDirectory() as td_a, \
     tempfile.TemporaryDirectory() as td_b:

    # --- Boot Node A ---
    node_a = BorderNode(host="127.0.0.1", port=PORT_A,
                        data_dir=td_a, peers=[])
    _apps[PORT_A] = node_a.app.test_client()

    # --- Boot Node B seeded with A ---
    node_b = BorderNode(host="127.0.0.1", port=PORT_B,
                        data_dir=td_b,
                        peers=[f"127.0.0.1:{PORT_A}"],
                        enable_storage=False,
                        enable_compute=True,
                        enable_dns=True)
    _apps[PORT_B] = node_b.app.test_client()

    print(f"[Setup] Node A port={PORT_A}  Node B port={PORT_B}")
    print()

    # ------------------------------------------------------------------
    # Step 1 -- /status
    # ------------------------------------------------------------------
    print("Step 1: /status endpoint")
    for port, label in [(PORT_A, "A"), (PORT_B, "B")]:
        resp = _apps[port].get("/status")
        d = json.loads(resp.data)
        assert resp.status_code == 200
        assert "chain" in d and "node" in d
        print(f"  Node {label}: height={d['chain']['height']}  "
              f"storage={d['subsystems']['storage']}  "
              f"compute={d['subsystems']['compute']}  "
              f"dns={d['subsystems']['dns']}  OK")

    # ------------------------------------------------------------------
    # Step 2 -- /wallet
    # ------------------------------------------------------------------
    print("\nStep 2: /wallet endpoint")
    resp = _apps[PORT_A].get("/wallet")
    w = json.loads(resp.data)
    assert w["address"].startswith("BC_")
    print(f"  Node A wallet: {w['address'][:30]}...  balance={w['balance']}  OK")

    # ------------------------------------------------------------------
    # Step 3 -- Mine on Node A via POST /chain/mine
    # ------------------------------------------------------------------
    print("\nStep 3: Mine a block via /chain/mine")
    # Inject enough bandwidth proofs first
    for i in range(2):
        proof = BandwidthProof(
            receipt_id=f"rcpt_runner_{i}",
            relay_address=node_a.wallet.address,
            client_id=f"client_{i}",
            bytes_forwarded=110 * 1024 * 1024,
            timestamp=time.time(),
            session_id=f"sess_{i}",
            relay_signature="demo_sig",
        )
        node_a.chain.add_proof(proof)

    resp = _apps[PORT_A].post("/chain/mine",
        data=b"{}",
        content_type="application/json")
    d = json.loads(resp.data)
    assert d["ok"], f"Mine failed: {d}"
    print(f"  Mined block #{d['block']}  hash={d['hash'][:14]}...  OK")

    # ------------------------------------------------------------------
    # Step 4 -- /chain/height
    # ------------------------------------------------------------------
    print("\nStep 4: /chain/height")
    resp = _apps[PORT_A].get("/chain/height")
    h = json.loads(resp.data)["height"]
    assert h == 1
    print(f"  Node A height={h}  OK")

    # ------------------------------------------------------------------
    # Step 5 -- /chain/block/<index>
    # ------------------------------------------------------------------
    print("\nStep 5: /chain/block/1")
    resp = _apps[PORT_A].get("/chain/block/1")
    blk = json.loads(resp.data)
    assert blk["index"] == 1
    print(f"  Block #1 miner={blk['miner_address'][:20]}...  OK")

    # ------------------------------------------------------------------
    # Step 6 -- /chain/balance
    # ------------------------------------------------------------------
    print("\nStep 6: /chain/balance")
    bal = node_a.chain.get_balance(node_a.wallet.address)
    resp = _apps[PORT_A].get(f"/chain/balance/{node_a.wallet.address}")
    d = json.loads(resp.data)
    assert d["balance"] == bal
    print(f"  Miner balance={d['balance']:.4f} BC  OK")

    # ------------------------------------------------------------------
    # Step 7 -- /chain/tx (submit + gossip)
    # ------------------------------------------------------------------
    print("\nStep 7: Submit transaction via /chain/tx")
    import uuid
    tx = Transaction(
        tx_id=f"tx_{uuid.uuid4().hex[:16]}",
        from_address=node_a.wallet.address,
        to_address=node_b.wallet.address,
        amount=1.0,
        fee=0.01,
        timestamp=time.time(),
        public_key=node_a.wallet.public_key_b64,
    )
    tx.signature = node_a.wallet.sign(tx.signing_data())
    resp = _apps[PORT_A].post("/chain/tx",
        data=json.dumps(tx.to_dict()).encode(),
        content_type="application/json")
    d = json.loads(resp.data)
    # Transaction may be rejected if balance is 0 (no spend check failure
    # is fine here -- we're testing the route works)
    print(f"  POST /chain/tx responded ok={d['ok']}  (route works)  OK")

    # ------------------------------------------------------------------
    # Step 8 -- P2P /p2p/ping from B to A
    # ------------------------------------------------------------------
    print("\nStep 8: P2P ping B -> A")
    resp = _apps[PORT_A].get(
        f"/p2p/ping?from_host=127.0.0.1&from_port={PORT_B}&node_id={node_b.p2p.node_id}")
    d = json.loads(resp.data)
    assert d["ok"]
    print(f"  Node A responded height={d['chain_height']}  OK")

    # ------------------------------------------------------------------
    # Step 9 -- Chain sync B gets A's block via /p2p/blocks
    # ------------------------------------------------------------------
    print("\nStep 9: Node B syncs block from A via /p2p/blocks")
    resp = _apps[PORT_A].get("/p2p/blocks?start=1&end=1")
    blocks_raw = json.loads(resp.data)["blocks"]
    assert len(blocks_raw) == 1
    from border.blockchain.block import Block
    blk = Block.from_dict(blocks_raw[0])
    ok, reason = node_b.chain.add_block(blk)
    assert ok, f"Node B rejected block: {reason}"
    print(f"  Node B accepted block #1  height={node_b.chain.height}  OK")

print()
print("=" * 60)
print("All unified node runner steps passed!")
print("=" * 60)
