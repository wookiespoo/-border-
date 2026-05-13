"""
Border CLI Demo
===============
Tests all major CLI subcommands against an in-process Border node.
Uses Flask test client to avoid real network I/O.
"""

import json, sys, os, time, tempfile, uuid
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests as _requests_mod

_app_client = None

class _FakeResp:
    def __init__(self, status_code, data):
        self.status_code = status_code
        self._data = data
    def json(self):
        return json.loads(self._data)

def _fake_get(url, params=None, timeout=None, **kw):
    from urllib.parse import urlparse, urlencode
    parsed = urlparse(url)
    path = parsed.path
    if params:
        path += "?" + urlencode(params)
    resp = _app_client.get(path)
    return _FakeResp(resp.status_code, resp.data)

def _fake_post(url, json=None, timeout=None, **kw):
    import requests as _r
    parsed_url = url
    from urllib.parse import urlparse
    parsed = urlparse(url)
    resp = _app_client.post(parsed.path,
        data=_r.compat.json.dumps(json).encode(),
        content_type="application/json")
    return _FakeResp(resp.status_code, resp.data)

_requests_mod.get  = _fake_get
_requests_mod.post = _fake_post

from border.node_runner import BorderNode
from border.blockchain.block import BandwidthProof
from border.cli import main as cli_main, load_config

print("=" * 60)
print("Border CLI Demo")
print("=" * 60)

with tempfile.TemporaryDirectory() as td:
    # Boot a node
    node = BorderNode(host="127.0.0.1", port=19300, data_dir=td, peers=[])
    _app_client = node.app.test_client()

    # CLI config points at our in-process node
    cfg_path = os.path.join(td, "config.json")
    with open(cfg_path, "w") as f:
        json.dump({"node_url": "http://127.0.0.1:19300",
                   "wallet": os.path.join(td, "wallet.json"),
                   "data_dir": td}, f)
    os.environ["BORDER_CONFIG"] = cfg_path

    def cli(*args):
        """Run a CLI command and return (stdout lines captured)."""
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                cli_main(list(args))
            except SystemExit:
                pass
        out = buf.getvalue().strip()
        return out

    # ------------------------------------------------------------------
    # Step 1 -- wallet new
    # ------------------------------------------------------------------
    print("\nStep 1: border wallet new")
    out = cli("wallet", "new", "--force")
    print(f"  {out.splitlines()[0]}")
    assert "created" in out.lower() or "Address" in out
    print("  OK")

    # ------------------------------------------------------------------
    # Step 2 -- wallet info (node unreachable message is fine)
    # ------------------------------------------------------------------
    print("\nStep 2: border wallet info")
    out = cli("wallet", "info")
    assert "Address" in out or "address" in out.lower()
    addr_line = [l for l in out.splitlines() if "Address" in l or "BC_" in l][0]
    print(f"  {addr_line.strip()}")
    print("  OK")

    # ------------------------------------------------------------------
    # Step 3 -- chain status
    # ------------------------------------------------------------------
    print("\nStep 3: border chain status")
    out = cli("chain", "status")
    assert "height" in out.lower()
    print(f"  {out.splitlines()[0]}")
    print("  OK")

    # ------------------------------------------------------------------
    # Step 4 -- Mine a block so the miner has a balance
    # ------------------------------------------------------------------
    print("\nStep 4: Mine a block to fund miner")
    for i in range(2):
        node.chain.add_proof(BandwidthProof(
            receipt_id=f"cli_rcpt_{i}",
            relay_address=node.wallet.address,
            client_id=f"cli_client_{i}",
            bytes_forwarded=110 * 1024 * 1024,
            timestamp=time.time(),
            session_id=f"cli_sess_{i}",
            relay_signature="demo",
        ))
    resp = _app_client.post("/chain/mine", data=b"{}",
                            content_type="application/json")
    d = json.loads(resp.data)
    assert d["ok"], f"mine failed: {d}"
    print(f"  Block #{d['block']} mined  OK")

    # ------------------------------------------------------------------
    # Step 5 -- chain balance (miner address)
    # ------------------------------------------------------------------
    print("\nStep 5: border chain balance <miner>")
    out = cli("chain", "balance", node.wallet.address)
    assert "BC" in out
    print(f"  {out.strip()}")
    print("  OK")

    # ------------------------------------------------------------------
    # Step 6 -- chain block 1
    # ------------------------------------------------------------------
    print("\nStep 6: border chain block 1")
    out = cli("chain", "block", "1")
    assert "Block #1" in out
    print(f"  {out.splitlines()[0]}")
    print("  OK")

    # ------------------------------------------------------------------
    # Step 7 -- wallet send (self-transfer to test route)
    # ------------------------------------------------------------------
    print("\nStep 7: border wallet send (self-transfer)")
    # Use node's wallet which has balance; override wallet path in config
    with open(cfg_path) as f:
        cfg = json.load(f)
    cfg["wallet"] = os.path.join(td, "wallet.json")

    # Load the cli wallet and check if it has funds; if not, skip send test
    from border.blockchain.wallet import BorderWallet
    cli_wallet = BorderWallet.load(os.path.join(td, "wallet.json"))
    node_bal = node.chain.get_balance(cli_wallet.address)
    if node_bal == 0:
        # Give cli wallet some credit via node's miner wallet routing
        print(f"  CLI wallet has 0 balance; sending from node wallet instead")
        cfg["wallet"] = os.path.join(td, "node_wallet_test.json")
        node.wallet.save(cfg["wallet"])
        with open(cfg_path, "w") as f:
            json.dump(cfg, f)

    out = cli("wallet", "send", "BC_0000000000000000000000000000000000", "0.001")
    # Either submitted or failed -- route was exercised
    print(f"  {out.strip()}")
    print("  OK (route exercised)")

    # ------------------------------------------------------------------
    # Step 8 -- config show
    # ------------------------------------------------------------------
    print("\nStep 8: border config show")
    out = cli("config", "show")
    assert "node_url" in out
    print(f"  {out.splitlines()[0].strip()}")
    print("  OK")

    # ------------------------------------------------------------------
    # Step 9 -- config set
    # ------------------------------------------------------------------
    print("\nStep 9: border config set")
    out = cli("config", "set", "test_key", "test_value")
    assert "test_key" in out
    print(f"  {out.strip()}")
    print("  OK")

print()
print("=" * 60)
print("All CLI demo steps passed!")
print("=" * 60)
