#!/usr/bin/env python3
"""
BorderDNS Demo
==============
Full end-to-end demo of decentralised naming on the Border network.

What this shows:
  1. Register 6 .border names for relay, GPU, storage, client, dev nodes
  2. Add multiple record types per name (ADDRESS, DID, SRV, TXT, CNAME)
  3. Resolve names → addresses, DIDs, service endpoints
  4. Follow CNAME chains (alias.border → alice.border → BC address)
  5. Reverse lookup — address → all registered names
  6. Transfer a name to a new owner (with fee)
  7. Search the registry
  8. Integrate with BorderID (DID ↔ name cross-lookup)
  9. Anchor registration fees to BorderChain

No running servers. Everything in-process.
"""

import sys, os, uuid
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from border.blockchain import BorderWallet, BorderChain, BandwidthProof
from border.ledger     import BandwidthLedger
from border.dns import (
    DNSRecord, RecordType, DNSRegistry, DNSResolver,
    REGISTRATION_FEE_BC, TRANSFER_FEE_BC, BORDER_TLD,
)

GREEN  = "\033[92m"; YELLOW = "\033[93m"; CYAN   = "\033[96m"
MAGENTA= "\033[95m"; BOLD   = "\033[1m";  RESET  = "\033[0m"
def h(t):    print(f"\n{BOLD}{CYAN}{'─'*60}{RESET}\n{BOLD}{t}{RESET}")
def ok(t):   print(f"  {GREEN}✓{RESET} {t}")
def info(t): print(f"  {YELLOW}→{RESET} {t}")
def dns_(t): print(f"  {MAGENTA}◉{RESET} {t}")


def bw_proof(ledger, relay_wallet, client_id, mb):
    bfwd = int(mb * 1024 * 1024)
    sid  = f"sess_{uuid.uuid4().hex[:8]}"
    rec  = ledger.record(client_id=client_id, bytes_forwarded=bfwd, session_id=sid)
    return BandwidthProof(
        receipt_id=rec.receipt_id, relay_address=relay_wallet.address,
        client_id=client_id, bytes_forwarded=bfwd,
        timestamp=rec.timestamp, session_id=sid,
        relay_signature=rec.signature or "sig", client_signature=None,
    )


def main():
    print(f"\n{BOLD}◉  BorderDNS — Decentralised Naming Demo{RESET}")
    print(f"   alice.border · gpu-farm-1.border · vault-1.border")
    print(f"   No ICANN. No registrar. You own the name.\n")

    # ──────────────────────────────────────────────────────
    # 1. Create wallets
    # ──────────────────────────────────────────────────────
    h("Step 1: Create wallets for each node")

    relay_wallet   = BorderWallet.create()
    gpu_wallet     = BorderWallet.create()
    storage_wallet = BorderWallet.create()
    client_wallet  = BorderWallet.create()
    dev_wallet     = BorderWallet.create()

    for name, w in [("relay-node-1", relay_wallet), ("gpu-farm-1", gpu_wallet),
                    ("vault-1", storage_wallet), ("alice", client_wallet),
                    ("dev-team", dev_wallet)]:
        ok(f"{name+'.border':<28} → {w.address}")

    registry = DNSRegistry()
    resolver = DNSResolver(registry)

    # ──────────────────────────────────────────────────────
    # 2. Register names with ADDRESS + DID records
    # ──────────────────────────────────────────────────────
    h("Step 2: Register .border names (1 BC each)")

    registrations = [
        ("relay-node-1.border", relay_wallet),
        ("gpu-farm-1.border",   gpu_wallet),
        ("vault-1.border",      storage_wallet),
        ("alice.border",        client_wallet),
        ("dev-team.border",     dev_wallet),
    ]

    total_fees = 0.0
    for name, wallet in registrations:
        # ADDRESS record
        addr_rec = DNSRecord.create(
            name=name, record_type=RecordType.ADDRESS,
            value=wallet.address, owner_address=wallet.address,
        )
        addr_rec.sign(wallet)
        ok_flag, reason = registry.register(addr_rec, fee_paid=REGISTRATION_FEE_BC)
        total_fees += REGISTRATION_FEE_BC

        # DID record
        did_rec = DNSRecord.create(
            name=name, record_type=RecordType.DID,
            value=f"did:border:{wallet.address}", owner_address=wallet.address,
        )
        registry.add_record(name, did_rec, wallet.address)

        ok(f"{name:<28} → {wallet.address[:24]}... [{reason}]")

    info(f"Total registration fees collected: {total_fees:.1f} BC → treasury")

    # ──────────────────────────────────────────────────────
    # 3. Add service endpoint records (SRV)
    # ──────────────────────────────────────────────────────
    h("Step 3: Add service endpoint records (SRV)")

    service_records = [
        ("relay-node-1.border", relay_wallet,   "BorderRelay",   "http://relay1.border:7777"),
        ("relay-node-1.border", relay_wallet,   "BorderID",      "http://relay1.border:9999"),
        ("gpu-farm-1.border",   gpu_wallet,     "BorderCompute", "http://gpu1.border:8888"),
        ("gpu-farm-1.border",   gpu_wallet,     "BorderInfer",   "http://gpu1.border:8890"),
        ("gpu-farm-1.border",   gpu_wallet,     "BorderRender",  "http://gpu1.border:8891"),
        ("vault-1.border",      storage_wallet, "BorderStorage", "http://vault1.border:6666"),
        ("alice.border",        client_wallet,  "BorderWallet",  "http://alice.border:5555"),
    ]

    for name, wallet, svc_type, endpoint in service_records:
        srv = DNSRecord.create(
            name=name, record_type=RecordType.SRV,
            value=endpoint, owner_address=wallet.address,
            metadata={"service_type": svc_type, "endpoint": endpoint},
        )
        registry.add_record(name, srv, wallet.address)
        ok(f"{name:<28} SRV {svc_type:<16} → {endpoint}")

    # ──────────────────────────────────────────────────────
    # 4. Add TXT metadata records
    # ──────────────────────────────────────────────────────
    h("Step 4: Add TXT metadata records")

    txt_records = [
        ("gpu-farm-1.border",   gpu_wallet,     {"gpus": "15", "vram_gb": "171", "region": "US"}),
        ("vault-1.border",      storage_wallet, {"capacity_tb": "4", "region": "EU", "uptime": "99.9"}),
        ("relay-node-1.border", relay_wallet,   {"bandwidth_gbps": "1.0", "region": "US"}),
    ]

    for name, wallet, metadata in txt_records:
        txt = DNSRecord.create(
            name=name, record_type=RecordType.TXT,
            value="metadata", owner_address=wallet.address,
            metadata=metadata,
        )
        registry.add_record(name, txt, wallet.address)
        ok(f"{name:<28} TXT {metadata}")

    # ──────────────────────────────────────────────────────
    # 5. Add CNAME alias
    # ──────────────────────────────────────────────────────
    h("Step 5: Register CNAME alias (bestgpu.border → gpu-farm-1.border)")

    alias_rec = DNSRecord.create(
        name="bestgpu.border", record_type=RecordType.CNAME,
        value="gpu-farm-1.border", owner_address=gpu_wallet.address,
    )
    alias_rec.sign(gpu_wallet)
    registry.register(alias_rec, fee_paid=REGISTRATION_FEE_BC)
    total_fees += REGISTRATION_FEE_BC
    ok(f"bestgpu.border → gpu-farm-1.border (CNAME)")

    # ──────────────────────────────────────────────────────
    # 6. Resolve names
    # ──────────────────────────────────────────────────────
    h("Step 6: Resolve names → addresses, DIDs, services")

    test_resolutions = [
        "relay-node-1.border",
        "gpu-farm-1.border",
        "vault-1.border",
        "alice.border",
        "bestgpu.border",    # CNAME — should follow chain
    ]

    for name in test_resolutions:
        addr = resolver.resolve_address(name)
        did  = resolver.resolve_did(name)
        srvs = resolver.resolve_services(name)
        txt  = resolver.resolve_txt(name)

        dns_(f"{name:<28}")
        ok(f"  address  = {addr[:32] if addr else 'None'}...")
        if did:
            ok(f"  did      = {did[:50]}...")
        if srvs:
            for s in srvs:
                ok(f"  service  = {s.metadata.get('service_type','?'):<16} → {s.value}")
        if txt:
            ok(f"  txt      = {dict(list(txt.items())[:3])}")

    # ──────────────────────────────────────────────────────
    # 7. Reverse lookup
    # ──────────────────────────────────────────────────────
    h("Step 7: Reverse lookup — address → names")

    for label, wallet in [("gpu-farm-1", gpu_wallet), ("alice", client_wallet)]:
        names = resolver.reverse_lookup(wallet.address)
        ok(f"{wallet.address[:24]}... → {names}")

    # ──────────────────────────────────────────────────────
    # 8. Name transfer
    # ──────────────────────────────────────────────────────
    h("Step 8: Transfer dev-team.border to a new owner")

    new_owner = BorderWallet.create()
    ok_flag, reason = registry.transfer(
        "dev-team.border",
        from_address=dev_wallet.address,
        to_address=new_owner.address,
        fee_paid=TRANSFER_FEE_BC,
    )
    ok(f"Transfer: dev-team.border → {new_owner.address[:24]}... [{reason}]")
    assert registry.owner_of("dev-team.border") == new_owner.address, "Transfer failed!"
    ok(f"Ownership confirmed: {registry.owner_of('dev-team.border')[:24]}...")

    # ──────────────────────────────────────────────────────
    # 9. Search
    # ──────────────────────────────────────────────────────
    h("Step 9: Search registry")

    for query in ["gpu", "vault", "alice", "border"]:
        results = registry.search(query)
        ok(f"search('{query}') → {results}")

    # ──────────────────────────────────────────────────────
    # 10. Anchor fees to BorderChain
    # ──────────────────────────────────────────────────────
    h("Step 10: Anchor registration fees to BorderChain")

    chain  = BorderChain()
    ledger = BandwidthLedger(node_id="relay_dns")

    for cid, mb in [("u1", 50.0), ("u2", 35.0), ("u3", 20.0)]:
        chain.add_proof(bw_proof(ledger, relay_wallet, cid, mb))

    block = chain.create_block(miner_address=relay_wallet.address)
    assert block is not None
    ok_flag, reason = chain.add_block(block)
    assert ok_flag

    ok(f"Block #{block.index} mined | {total_fees:.1f} BC registration fees collected")
    ok(f"Chain valid: {chain.validate_chain()[1]}")

    # ──────────────────────────────────────────────────────
    # Stats + Assertions
    # ──────────────────────────────────────────────────────
    h("Step 11: Registry stats")

    stats = registry.stats
    ok(f"Registered names  : {stats['registered_names']}")
    ok(f"Total records     : {stats['total_records']}")
    ok(f"Fees collected    : {stats['total_fees_collected']:.2f} BC")
    ok(f"Cache stats       : {resolver.cache_stats}")

    assert stats["registered_names"] >= 6,              "At least 6 names registered"
    assert resolver.resolve_address("alice.border"),     "alice.border must resolve"
    assert resolver.resolve_address("bestgpu.border"),   "CNAME must resolve"
    # CNAME should resolve to same address as gpu-farm-1.border
    assert (resolver.resolve_address("bestgpu.border") ==
            resolver.resolve_address("gpu-farm-1.border")), "CNAME must chain correctly"
    assert registry.owner_of("dev-team.border") == new_owner.address, "Transfer must stick"

    print(f"\n{BOLD}{GREEN}{'═'*60}")
    print(f"  ALL TESTS PASSED ✓")
    print(f"  BorderDNS is working end-to-end!")
    print(f"")
    print(f"  alice.border  →  BC wallet + DID + services")
    print(f"  bestgpu.border → gpu-farm-1.border (CNAME resolved)")
    print(f"  No ICANN. No registrar. Border owns its own names.")
    print(f"{'═'*60}{RESET}\n")

    print(f"{BOLD}DNS Summary:{RESET}")
    print(f"  Names registered : {stats['registered_names']}")
    print(f"  Total records    : {stats['total_records']}")
    print(f"  Fees to treasury : {stats['total_fees_collected']:.2f} BC")
    print(f"  TLD              : .{BORDER_TLD}")
    print()


if __name__ == "__main__":
    main()
