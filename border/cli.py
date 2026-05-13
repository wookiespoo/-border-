"""
border.cli — Light client / wallet CLI

Commands
--------
  border wallet new              Create a new wallet
  border wallet info             Show address + balance
  border wallet send <to> <amt>      Send BC tokens
  border wallet new-mnemonic         Create wallet with BIP-39 mnemonic phrase
  border wallet recover              Recover wallet from BIP-39 mnemonic

  border chain status            Show chain height + supply
  border chain balance <addr>    Check any address balance
  border chain block <index>     Inspect a block

  border dns register <name>     Register a .border name
  border dns lookup <name>       Resolve a .border name
  border dns transfer <name> <new_owner_addr>

  border storage upload <file>   Upload a file to storage
  border storage download <hash> <out_file>

  border compute submit <script> Submit a compute job
  border compute status <job_id> Check a job's status

  border node start              Start a full node (wraps node_runner)

Configuration (~/.border/config.json):
  node_url   : REST endpoint of your Border node  (default http://localhost:9000)
  wallet     : path to wallet.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Optional

import requests

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

DEFAULT_CONFIG = {
    "node_url": "http://localhost:9000",
    "wallet":   "~/.border/wallet.json",
    "data_dir": "~/.border",
}

def _config_path() -> Path:
    return Path(os.environ.get("BORDER_CONFIG", "~/.border/config.json")).expanduser()

def load_config() -> dict:
    p = _config_path()
    if p.exists():
        try:
            return {**DEFAULT_CONFIG, **json.loads(p.read_text())}
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)

def save_config(cfg: dict) -> None:
    p = _config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, indent=2))

def _node(cfg: dict) -> str:
    return cfg["node_url"].rstrip("/")


# ---------------------------------------------------------------------------
# Wallet helpers
# ---------------------------------------------------------------------------

def _load_wallet(cfg: dict, password: Optional[str] = None):
    from .blockchain.wallet import BorderWallet
    wp = Path(cfg["wallet"]).expanduser()
    if not wp.exists():
        print(f"No wallet found at {wp}. Run: border wallet new")
        sys.exit(1)
    return BorderWallet.load(str(wp), password=password)

def _make_tx(wallet, to_address: str, amount: float, fee: float,
             cfg: dict) -> dict:
    from .blockchain.transaction import Transaction
    tx = Transaction(
        tx_id=f"tx_{uuid.uuid4().hex[:16]}",
        from_address=wallet.address,
        to_address=to_address,
        amount=amount,
        fee=fee,
        timestamp=time.time(),
        public_key=wallet.public_key_b64,
    )
    tx.signature = wallet.sign(tx.signing_data())
    return tx.to_dict()


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def cmd_wallet_new(args, cfg):
    from .blockchain.wallet import BorderWallet
    wp = Path(cfg["wallet"]).expanduser()
    if wp.exists() and not args.force:
        print(f"Wallet already exists at {wp}. Use --force to overwrite.")
        sys.exit(1)
    wp.parent.mkdir(parents=True, exist_ok=True)
    w = BorderWallet.create()
    w.save(str(wp), password=args.password or None)
    print(f"New wallet created")
    print(f"  Address : {w.address}")
    print(f"  Saved to: {wp}")
    if args.password:
        print("  Encrypted with password")

def cmd_wallet_info(args, cfg):
    w = _load_wallet(cfg, password=args.password or None)
    print(f"Address : {w.address}")
    try:
        resp = requests.get(f"{_node(cfg)}/chain/balance/{w.address}", timeout=5)
        bal = resp.json().get("balance", "?")
        print(f"Balance : {bal} BC")
    except Exception:
        print("Balance : (node unreachable)")

def cmd_wallet_send(args, cfg):
    w = _load_wallet(cfg, password=args.password or None)
    tx_dict = _make_tx(w, args.to, float(args.amount), float(args.fee), cfg)
    resp = requests.post(f"{_node(cfg)}/chain/tx", json=tx_dict, timeout=10)
    d = resp.json()
    if d.get("ok"):
        print(f"Transaction submitted: {tx_dict['tx_id']}")
    else:
        print(f"Transaction failed: {d.get('error', d)}")
        sys.exit(1)

def cmd_chain_status(args, cfg):
    resp = requests.get(f"{_node(cfg)}/status", timeout=5)
    d = resp.json()
    chain = d.get("chain", {})
    node  = d.get("node", {})
    print(f"Chain height   : {chain.get('height')}")
    print(f"Total supply   : {chain.get('total_supply')} BC")
    print(f"Mempool txns   : {chain.get('mempool_size')}")
    print(f"Connected peers: {node.get('peers_reachable')}")
    print(f"Subsystems     : storage={d['subsystems']['storage']}  "
          f"compute={d['subsystems']['compute']}  dns={d['subsystems']['dns']}")

def cmd_chain_balance(args, cfg):
    resp = requests.get(f"{_node(cfg)}/chain/balance/{args.address}", timeout=5)
    d = resp.json()
    print(f"{d['address']}: {d['balance']} BC")

def cmd_chain_block(args, cfg):
    resp = requests.get(f"{_node(cfg)}/chain/block/{args.index}", timeout=5)
    if resp.status_code == 404:
        print(f"Block {args.index} not found")
        sys.exit(1)
    b = resp.json()
    print(f"Block #{b['index']}")
    print(f"  Hash        : {b.get('block_hash','')[:32]}...")
    print(f"  Miner       : {b.get('miner_address','')}")
    print(f"  Timestamp   : {b.get('timestamp','')}")
    print(f"  Transactions: {len(b.get('transactions', []))}")
    print(f"  BW proofs   : {len(b.get('bandwidth_proofs', []))}")
    print(f"  Storage pfs : {len(b.get('storage_proofs', []))}")

def cmd_dns_register(args, cfg):
    w = _load_wallet(cfg, password=args.password or None)
    import base64, json as _json
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    intent = _json.dumps({"action":"register","name":args.name,
                          "owner":w.address}, sort_keys=True)
    sig = w.sign(intent.encode())
    resp = requests.post(f"{_node(cfg)}/dns/register", json={
        "name": args.name,
        "owner": w.address,
        "records": [],
        "owner_public_key": w.public_key_b64,
        "owner_signature": sig,
    }, timeout=10)
    d = resp.json()
    if d.get("ok") or d.get("registered"):
        print(f"Registered: {args.name} -> {w.address}")
    else:
        print(f"Failed: {d}")

def cmd_dns_lookup(args, cfg):
    resp = requests.get(f"{_node(cfg)}/dns/resolve/{args.name}", timeout=5)
    if resp.status_code == 404:
        print(f"Name not found: {args.name}")
        sys.exit(1)
    d = resp.json()
    print(f"Name    : {args.name}")
    print(f"Owner   : {d.get('owner','?')}")
    print(f"Records : {d.get('records', [])}")

def cmd_dns_transfer(args, cfg):
    w = _load_wallet(cfg, password=args.password or None)
    import json as _json
    intent = _json.dumps({"action":"transfer","name":args.name,
                          "new_owner":args.new_owner}, sort_keys=True)
    sig = w.sign(intent.encode())
    resp = requests.post(f"{_node(cfg)}/dns/transfer", json={
        "name": args.name,
        "new_owner": args.new_owner,
        "owner_public_key": w.public_key_b64,
        "owner_signature": sig,
    }, timeout=10)
    d = resp.json()
    if d.get("ok") or d.get("transferred"):
        print(f"Transferred: {args.name} -> {args.new_owner}")
    else:
        print(f"Failed: {d}")

def cmd_storage_upload(args, cfg):
    from .storage.client import BorderStorageClient
    from .blockchain.wallet import BorderWallet
    w = _load_wallet(cfg, password=args.password or None)
    client = BorderStorageClient(
        node_url=f"{_node(cfg)}/storage",
        wallet=w,
    )
    file_hash = client.upload(args.file)
    print(f"Uploaded  : {args.file}")
    print(f"File hash : {file_hash}")
    print(f"Download  : border storage download {file_hash} <outfile>")

def cmd_storage_download(args, cfg):
    from .storage.client import BorderStorageClient
    w = _load_wallet(cfg, password=args.password or None)
    client = BorderStorageClient(
        node_url=f"{_node(cfg)}/storage",
        wallet=w,
    )
    client.download(args.hash, args.outfile)
    print(f"Downloaded: {args.outfile}")

def cmd_compute_submit(args, cfg):
    resp = requests.post(f"{_node(cfg)}/compute/job", json={
        "script": Path(args.script).read_text(),
        "requirements": {},
    }, timeout=10)
    d = resp.json()
    job_id = d.get("job_id", d.get("id", "?"))
    print(f"Job submitted: {job_id}")

def cmd_compute_status(args, cfg):
    resp = requests.get(f"{_node(cfg)}/compute/job/{args.job_id}", timeout=5)
    d = resp.json()
    print(f"Job {args.job_id}: status={d.get('status','?')}")
    if "result" in d:
        print(f"Result: {d['result']}")

def cmd_node_start(args, cfg):
    from .node_runner import main as node_main
    extra = []
    if args.port:     extra += ["--port", str(args.port)]
    if args.peers:    extra += ["--peers"] + args.peers
    if args.storage:  extra += ["--storage"]
    if args.compute:  extra += ["--compute"]
    if args.dns:      extra += ["--dns"]
    node_main(extra)

def cmd_config_set(args, cfg):
    cfg[args.key] = args.value
    save_config(cfg)
    print(f"Config updated: {args.key} = {args.value}")

def cmd_config_show(args, cfg):
    for k, v in cfg.items():
        print(f"  {k}: {v}")


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="border",
        description="Border protocol light client")
    p.add_argument("--node-url", help="Override node URL")
    p.add_argument("--password", help="Wallet password", default="")

    sub = p.add_subparsers(dest="group")

    # -- wallet --
    wallet = sub.add_parser("wallet")
    ws = wallet.add_subparsers(dest="cmd")
    wn = ws.add_parser("new");   wn.add_argument("--force", action="store_true")
    ws.add_parser("info")
    wsend = ws.add_parser("send")
    wsend.add_argument("to");    wsend.add_argument("amount")
    wsend.add_argument("--fee",  default="0.001")

    wnm = ws.add_parser("new-mnemonic", help="Create wallet with BIP-39 mnemonic")
    wnm.add_argument("--force",      action="store_true")
    wnm.add_argument("--24-words",   dest="words24", action="store_true")
    wnm.add_argument("--passphrase", default="", help="Optional BIP-39 passphrase")

    wrec = ws.add_parser("recover", help="Recover wallet from BIP-39 mnemonic")
    wrec.add_argument("--mnemonic",   default="", help="Mnemonic phrase (or leave blank to prompt)")
    wrec.add_argument("--passphrase", default="", help="Optional BIP-39 passphrase")
    wrec.add_argument("--force",      action="store_true")

    # -- chain --
    chain = sub.add_parser("chain")
    cs = chain.add_subparsers(dest="cmd")
    cs.add_parser("status")
    cb = cs.add_parser("balance"); cb.add_argument("address")
    cbl = cs.add_parser("block");  cbl.add_argument("index", type=int)

    # -- dns --
    dns = sub.add_parser("dns")
    ds = dns.add_subparsers(dest="cmd")
    dr = ds.add_parser("register"); dr.add_argument("name")
    dl = ds.add_parser("lookup");   dl.add_argument("name")
    dt = ds.add_parser("transfer"); dt.add_argument("name"); dt.add_argument("new_owner")

    # -- storage --
    store = sub.add_parser("storage")
    ss = store.add_subparsers(dest="cmd")
    su = ss.add_parser("upload");   su.add_argument("file")
    sd = ss.add_parser("download"); sd.add_argument("hash"); sd.add_argument("outfile")

    # -- compute --
    comp = sub.add_parser("compute")
    cos = comp.add_subparsers(dest="cmd")
    coj = cos.add_parser("submit"); coj.add_argument("script")
    cost = cos.add_parser("status"); cost.add_argument("job_id")

    # -- node --
    node = sub.add_parser("node")
    nos = node.add_subparsers(dest="cmd")
    ns = nos.add_parser("start")
    ns.add_argument("--port", type=int)
    ns.add_argument("--peers", nargs="*")
    ns.add_argument("--storage", action="store_true")
    ns.add_argument("--compute", action="store_true")
    ns.add_argument("--dns",     action="store_true")

    # -- config --
    config = sub.add_parser("config")
    cfgs = config.add_subparsers(dest="cmd")
    cfgs.add_parser("show")
    cset = cfgs.add_parser("set"); cset.add_argument("key"); cset.add_argument("value")

    return p


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def cmd_wallet_new_mnemonic(args, cfg):
    """Create a new wallet backed by a BIP-39 mnemonic seed phrase."""
    from .blockchain.wallet import BorderWallet
    wp = Path(cfg["wallet"]).expanduser()
    if wp.exists() and not getattr(args, "force", False):
        print(f"Wallet already exists at {wp}. Use --force to overwrite.")
        import sys; sys.exit(1)
    wp.parent.mkdir(parents=True, exist_ok=True)
    strength = 256 if getattr(args, "words24", False) else 128
    w = BorderWallet.create_with_mnemonic(strength=strength,
                                          passphrase=getattr(args, "passphrase", "") or "")
    w.save(str(wp), password=args.password or None)
    print("New wallet created with mnemonic recovery phrase")
    print(f"  Address : {w.address}")
    print(f"  Saved to: {wp}")
    print()
    print("  ╔══════════════════════════════════════════════════════╗")
    print("  ║  WRITE DOWN YOUR RECOVERY PHRASE — DO NOT SHARE IT  ║")
    print("  ╚══════════════════════════════════════════════════════╝")
    words = w.mnemonic.split()
    for i, word in enumerate(words, 1):
        print(f"  {i:2d}. {word}")
    print()
    print("  Store this phrase somewhere safe.  It is the ONLY way to")
    print("  recover your wallet if you lose the file or forget the password.")


def cmd_wallet_recover(args, cfg):
    """Recover a wallet from a BIP-39 mnemonic seed phrase."""
    from .blockchain.wallet import BorderWallet
    from .blockchain.mnemonic import validate_mnemonic
    import sys

    phrase = getattr(args, "mnemonic", None) or ""
    if not phrase:
        # Prompt interactively
        print("Enter your recovery phrase (12 or 24 words, space-separated):")
        phrase = input("> ").strip()

    if not validate_mnemonic(phrase):
        print("Error: invalid mnemonic phrase.")
        sys.exit(1)

    passphrase = getattr(args, "passphrase", "") or ""
    w = BorderWallet.from_mnemonic(phrase, passphrase=passphrase)

    wp = Path(cfg["wallet"]).expanduser()
    if wp.exists() and not getattr(args, "force", False):
        print(f"Wallet file already exists at {wp}. Use --force to overwrite.")
        sys.exit(1)
    wp.parent.mkdir(parents=True, exist_ok=True)
    w.save(str(wp), password=args.password or None)
    print("Wallet recovered successfully")
    print(f"  Address : {w.address}")
    print(f"  Saved to: {wp}")


DISPATCH = {
    ("wallet",  "new"):      cmd_wallet_new,
    ("wallet",  "info"):     cmd_wallet_info,
    ("wallet",  "send"):          cmd_wallet_send,
    ("wallet",  "new-mnemonic"):  cmd_wallet_new_mnemonic,
    ("wallet",  "recover"):       cmd_wallet_recover,
    ("chain",   "status"):   cmd_chain_status,
    ("chain",   "balance"):  cmd_chain_balance,
    ("chain",   "block"):    cmd_chain_block,
    ("dns",     "register"): cmd_dns_register,
    ("dns",     "lookup"):   cmd_dns_lookup,
    ("dns",     "transfer"): cmd_dns_transfer,
    ("storage", "upload"):   cmd_storage_upload,
    ("storage", "download"): cmd_storage_download,
    ("compute", "submit"):   cmd_compute_submit,
    ("compute", "status"):   cmd_compute_status,
    ("node",    "start"):    cmd_node_start,
    ("config",  "show"):     cmd_config_show,
    ("config",  "set"):      cmd_config_set,
}

def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.group:
        parser.print_help()
        sys.exit(0)

    cfg = load_config()
    if args.node_url:
        cfg["node_url"] = args.node_url
    if hasattr(args, "password") and args.password:
        pass   # passed through to sub-commands

    key = (args.group, getattr(args, "cmd", None))
    handler = DISPATCH.get(key)
    if handler is None:
        # Print sub-group help
        parser.parse_args([args.group, "--help"])
        sys.exit(0)

    try:
        handler(args, cfg)
    except requests.exceptions.ConnectionError:
        print(f"Cannot reach node at {cfg['node_url']}")
        print("Start one with: border node start")
        sys.exit(1)

if __name__ == "__main__":
    main()
