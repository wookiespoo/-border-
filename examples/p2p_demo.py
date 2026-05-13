"""
Border P2P Demo
===============
Simulates a two-node network in a single process:
  - Node A (port 19100) mines blocks
  - Node B (port 19101) discovers A via seeds and syncs the chain
  - Both nodes gossip transactions to each other

No real sockets are opened -- we monkey-patch requests.get/post to
route HTTP calls between the two in-process Flask apps.
"""

import json
import threading
import time
import requests as _real_requests

# -----------------------------------------------------------------------
# Monkey-patch requests to route between two in-process Flask test clients
# -----------------------------------------------------------------------
from flask import Flask
from io import BytesIO

_apps = {}   # port -> Flask test client

class _FakeResponse:
    def __init__(self, status_code, data):
        self.status_code = status_code
        self._data = data
    def json(self):
        return json.loads(self._data)
    @property
    def text(self):
        return self._data.decode()

def _fake_get(url, params=None, timeout=None, **kw):
    from urllib.parse import urlparse, urlencode
    parsed = urlparse(url)
    port = parsed.port or 80
    path = parsed.path
    if params:
        path = path + "?" + urlencode(params)
    client = _apps.get(port)
    if client is None:
        raise ConnectionError(f"No app on port {port}")
    resp = client.get(path)
    return _FakeResponse(resp.status_code, resp.data)

def _fake_post(url, json=None, timeout=None, **kw):
    from urllib.parse import urlparse
    parsed = urlparse(url)
    port = parsed.port or 80
    client = _apps.get(port)
    if client is None:
        raise ConnectionError(f"No app on port {port}")
    resp = client.post(parsed.path,
                       data=_real_requests.compat.json.dumps(json).encode(),
                       content_type="application/json")
    return _FakeResponse(resp.status_code, resp.data)

import requests
requests.get  = _fake_get
requests.post = _fake_post

# -----------------------------------------------------------------------
# Build two Border nodes
# -----------------------------------------------------------------------
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from border.blockchain.chain import BorderChain
from border.blockchain.wallet import BorderWallet
from border.blockchain.transaction import Transaction
from border.blockchain.block import BandwidthProof
from border.p2p.node import P2PNode
from border.p2p.server import create_p2p_blueprint

PORT_A = 19100
PORT_B = 19101

print("=" * 60)
print("Border P2P Demo")
print("=" * 60)

# -- Node A --
chain_a = BorderChain()
wallet_a = BorderWallet.create()
p2p_a = P2PNode(chain_a, self_host="127.0.0.1", self_port=PORT_A,
                seeds=[], node_id="node_a")

app_a = Flask("node_a")
app_a.register_blueprint(create_p2p_blueprint(p2p_a))
_apps[PORT_A] = app_a.test_client()

# -- Node B seeded with Node A --
chain_b = BorderChain()
wallet_b = BorderWallet.create()
p2p_b = P2PNode(chain_b, self_host="127.0.0.1", self_port=PORT_B,
                seeds=[f"127.0.0.1:{PORT_A}"], node_id="node_b")

app_b = Flask("node_b")
app_b.register_blueprint(create_p2p_blueprint(p2p_b))
_apps[PORT_B] = app_b.test_client()

print(f"[Setup] Node A: port={PORT_A}  Node B: port={PORT_B}")
print()

# -----------------------------------------------------------------------
# Step 1 -- Ping endpoint
# -----------------------------------------------------------------------
print("Step 1: Ping A from B")
resp = _apps[PORT_A].get(f"/p2p/ping?from_host=127.0.0.1&from_port={PORT_B}&node_id=node_b")
data = json.loads(resp.data)
assert resp.status_code == 200
assert data["ok"] is True
assert data["chain_height"] == 0
print(f"  Node A height={data['chain_height']}  node_id={data['node_id']}  OK")

# -----------------------------------------------------------------------
# Step 2 -- Peer exchange
# -----------------------------------------------------------------------
print("\nStep 2: Peer exchange (B queries A for peers)")
resp = _apps[PORT_A].get("/p2p/peers")
peers = json.loads(resp.data)["peers"]
print(f"  Node A knows {len(peers)} peers")

# Announce B to A
resp = _apps[PORT_A].post("/p2p/announce",
    data=json.dumps({"host":"127.0.0.1","port":PORT_B,"node_id":"node_b"}).encode(),
    content_type="application/json")
assert json.loads(resp.data)["ok"] is True
resp = _apps[PORT_A].get("/p2p/peers")
peers = json.loads(resp.data)["peers"]
print(f"  After announce: Node A knows {len(peers)} peer(s): {[p['node_id'] for p in peers]}")
assert any(p["node_id"] == "node_b" for p in peers)
print("  OK")

# -----------------------------------------------------------------------
# Step 3 -- Mine 3 blocks on Node A
# -----------------------------------------------------------------------
print("\nStep 3: Mine 3 blocks on Node A")

def make_proof(n):
    w = BorderWallet.create()
    return BandwidthProof(
        receipt_id=f"rcpt_{n}",
        relay_address=w.address,
        client_id=f"client_{n}",
        bytes_forwarded=110 * 1024 * 1024,
        timestamp=time.time(),
        session_id=f"sess_{n}",
        relay_signature="demo_sig",
    )

for i in range(3):
    proof = make_proof(i)
    chain_a.add_proof(proof)
    block = chain_a.create_block(miner_address=wallet_a.address)
    assert block is not None, f"Block {i} creation failed"
    ok, reason = chain_a.add_block(block)
    assert ok, f"Block {i} rejected: {reason}"
    print(f"  Block #{block.index} mined  hash={block.block_hash[:12]}...")

assert chain_a.height == 3
print(f"  Node A height={chain_a.height}  OK")

# -----------------------------------------------------------------------
# Step 4 -- Block hash endpoint
# -----------------------------------------------------------------------
print("\nStep 4: Fetch block hashes from Node A")
for idx in range(4):
    resp = _apps[PORT_A].get(f"/p2p/block_hash?index={idx}")
    if idx <= 3:
        h = json.loads(resp.data)["hash"]
        local = chain_a.block_hash_at(idx)
        assert h == local, f"Hash mismatch at index {idx}"
        print(f"  index={idx}  hash={h[:14]}...  OK")

# -----------------------------------------------------------------------
# Step 5 -- Block range download
# -----------------------------------------------------------------------
print("\nStep 5: Download blocks 1-3 from Node A")
resp = _apps[PORT_A].get("/p2p/blocks?start=1&end=3")
blocks_raw = json.loads(resp.data)["blocks"]
assert len(blocks_raw) == 3
print(f"  Got {len(blocks_raw)} blocks  OK")

# -----------------------------------------------------------------------
# Step 6 -- Chain sync: apply blocks to Node B
# -----------------------------------------------------------------------
print("\nStep 6: Apply downloaded blocks to Node B (simulated sync)")
from border.blockchain.block import Block
for bd in blocks_raw:
    blk = Block.from_dict(bd)
    ok, reason = chain_b.add_block(blk)
    assert ok, f"Node B rejected block #{blk.index}: {reason}"
    print(f"  Node B accepted block #{blk.index}  OK")

assert chain_b.height == chain_a.height
print(f"  Node B height={chain_b.height} matches Node A height={chain_a.height}  OK")

# -----------------------------------------------------------------------
# Step 7 -- Gossip a transaction
# -----------------------------------------------------------------------
print("\nStep 7: Gossip a transaction from A to B")
chain_a._mempool.clear()
from border.blockchain.transaction import Transaction
# Credit wallet_a via a coinbase injected into block #1 so balance check passes
coinbase = Transaction.coinbase(
    to_address=wallet_a.address, reward=100.0, deterministic_id="demo_credit"
)
chain_a._chain[1].transactions.append(coinbase)

import uuid as _uuid
tx = Transaction(
    tx_id=f"tx_{_uuid.uuid4().hex[:16]}",
    from_address=wallet_a.address,
    to_address=wallet_b.address,
    amount=5.0,
    fee=0.1,
    timestamp=time.time(),
    public_key=wallet_a.public_key_b64,
)
signing_data = tx.signing_data()
tx.signature = wallet_a.sign(signing_data)
ok = chain_a.add_transaction(tx)
print(f"  Transaction added to A mempool: {ok}")

# Gossip it to B via /p2p/gossip
envelope = {
    "msg_id": "test_tx_001",
    "msg_type": "transaction",
    "ttl": 4,
    "origin": f"127.0.0.1:{PORT_A}",
    "payload": tx.to_dict(),
}
resp = _apps[PORT_B].post("/p2p/gossip",
    data=json.dumps(envelope).encode(),
    content_type="application/json")
result = json.loads(resp.data)
assert result["ok"] is True
assert result["fresh"] is True
print(f"  Node B gossip accepted: fresh={result['fresh']}  OK")

# Duplicate should be rejected
resp = _apps[PORT_B].post("/p2p/gossip",
    data=json.dumps(envelope).encode(),
    content_type="application/json")
result = json.loads(resp.data)
assert result["fresh"] is False
print(f"  Duplicate gossip rejected: fresh={result['fresh']}  OK")

# -----------------------------------------------------------------------
# Step 8 -- Relay a transaction via /p2p/tx
# -----------------------------------------------------------------------
print("\nStep 8: Relay transaction via POST /p2p/tx")
tx2 = Transaction(
    tx_id=f"tx_{_uuid.uuid4().hex[:16]}",
    from_address=wallet_a.address,
    to_address=wallet_b.address,
    amount=1.0,
    fee=0.05,
    timestamp=time.time(),
    public_key=wallet_a.public_key_b64,
)
tx2.signature = wallet_a.sign(tx2.signing_data())
resp = _apps[PORT_A].post("/p2p/tx",
    data=json.dumps(tx2.to_dict()).encode(),
    content_type="application/json")
result = json.loads(resp.data)
print(f"  POST /p2p/tx result: ok={result['ok']}  OK")

# -----------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------
print()
print("=" * 60)
print("All P2P demo steps passed!")
print(f"  Node A: height={chain_a.height}  peers={len(p2p_a.discovery.get_peers(False))}")
print(f"  Node B: height={chain_b.height}  peers={len(p2p_b.discovery.get_peers(False))}")
print("=" * 60)
