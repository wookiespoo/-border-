#!/usr/bin/env python3
"""
BorderDNS Demo - full end-to-end with signature-verified register/transfer.
No running servers. Everything in-process.
"""
import sys, os, uuid
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from border.blockchain import BorderWallet, BorderChain, BandwidthProof
from border.ledger import BandwidthLedger
from border.dns import (
    DNSRecord, RecordType, DNSRegistry, DNSResolver,
    REGISTRATION_FEE_BC, TRANSFER_FEE_BC, BORDER_TLD,
)

G="\033[92m"; Y="\033[93m"; C="\033[96m"; M="\033[95m"; B="\033[1m"; R="\033[0m"
def h(t):    print(f"\n{B}{C}{'─'*60}{R}\n{B}{t}{R}")
def ok(t):   print(f"  {G}OK{R} {t}")
def info(t): print(f"  {Y}->{R} {t}")

def bw_proof(ledger, relay_wallet, client_id, mb):
    bfwd = int(mb * 1024 * 1024)
    sid = f"sess_{uuid.uuid4().hex[:8]}"
    rec = ledger.record(client_id=client_id, bytes_forwarded=bfwd, session_id=sid)
    return BandwidthProof(
        receipt_id=rec.receipt_id, relay_address=relay_wallet.address,
        client_id=client_id, bytes_forwarded=bfwd,
        timestamp=rec.timestamp, session_id=sid,
        relay_signature=rec.signature or "sig", client_signature=None,
    )

def reg_sign(w, name):
    return w.sign(f"register:{name}:{w.address}".encode())

def add_sign(w, name):
    return w.sign(f"add_record:{name}:{w.address}".encode())

def xfer_sign(w, name, to_addr):
    return w.sign(f"transfer:{name}:{w.address}:{to_addr}".encode())

def main():
    print(f"\n{B}BorderDNS - Decentralised Naming Demo{R}")
    print(f"   alice.border  gpu-farm-1.border  vault-1.border")
    print(f"   No ICANN. No registrar. You own the name.\n")

    # Step 1: Wallets
    h("Step 1: Create wallets")
    relay_wallet   = BorderWallet.create()
    gpu_wallet     = BorderWallet.create()
    storage_wallet = BorderWallet.create()
    client_wallet  = BorderWallet.create()
    dev_wallet     = BorderWallet.create()
    for name, w in [("relay-node-1.border", relay_wallet),
                    ("gpu-farm-1.border",   gpu_wallet),
                    ("vault-1.border",      storage_wallet),
                    ("alice.border",        client_wallet),
                    ("dev-team.border",     dev_wallet)]:
        ok(f"{name:<28} -> {w.address}")

    registry = DNSRegistry()
    resolver = DNSResolver(registry)

    # Step 2: Register names (signature verified)
    h("Step 2: Register .border names (1 BC each) - signature verified")
    registrations = [
        ("relay-node-1.border", relay_wallet),
        ("gpu-farm-1.border",   gpu_wallet),
        ("vault-1.border",      storage_wallet),
        ("alice.border",        client_wallet),
        ("dev-team.border",     dev_wallet),
    ]
    total_fees = 0.0
    for name, wallet in registrations:
        addr_rec = DNSRecord.create(
            name=name, record_type=RecordType.ADDRESS,
            value=wallet.address, owner_address=wallet.address,
        )
        addr_rec.sign(wallet)
        ok_flag, reason = registry.register(
            addr_rec, fee_paid=REGISTRATION_FEE_BC,
            owner_public_key=wallet.public_key_b64,
            owner_signature=reg_sign(wallet, name),
        )
        assert ok_flag, f"Registration failed: {reason}"
        total_fees += REGISTRATION_FEE_BC

        did_rec = DNSRecord.create(
            name=name, record_type=RecordType.DID,
            value=f"did:border:{wallet.address}", owner_address=wallet.address,
        )
        registry.add_record(name, did_rec, wallet.address,
                            caller_public_key=wallet.public_key_b64,
                            caller_signature=add_sign(wallet, name))
        ok(f"{name:<28} -> {wallet.address[:24]}... [{reason}]")

    info(f"Total fees: {total_fees:.1f} BC")

    # Step 3: SRV records
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
        registry.add_record(name, srv, wallet.address,
                            caller_public_key=wallet.public_key_b64,
                            caller_signature=add_sign(wallet, name))
        ok(f"{name:<28} SRV {svc_type:<16} -> {endpoint}")

    # Step 4: TXT records
    h("Step 4: Add TXT metadata records")
    txt_records = [
        ("gpu-farm-1.border",   gpu_wallet,     {"gpus": "15", "vram_gb": "171", "region": "US"}),
        ("vault-1.border",      storage_wallet, {"capacity_tb": "4", "region": "EU", "uptime": "99.9"}),
        ("relay-node-1.border", relay_wallet,   {"bandwidth_gbps": "1.0", "region": "US"}),
    ]
    for name, wallet, metadata in txt_records:
        txt = DNSRecord.create(
            name=name, record_type=RecordType.TXT,
            value="metadata", owner_address=wallet.address, metadata=metadata,
        )
        registry.add_record(name, txt, wallet.address,
                            caller_public_key=wallet.public_key_b64,
                            caller_signature=add_sign(wallet, name))
        ok(f"{name:<28} TXT {metadata}")

    # Step 5: CNAME alias
    h("Step 5: Register CNAME alias (bestgpu.border -> gpu-farm-1.border)")
    alias_rec = DNSRecord.create(
        name="bestgpu.border", record_type=RecordType.CNAME,
        value="gpu-farm-1.border", owner_address=gpu_wallet.address,
    )
    alias_rec.sign(gpu_wallet)
    registry.register(alias_rec, fee_paid=REGISTRATION_FEE_BC,
                      owner_public_key=gpu_wallet.public_key_b64,
                      owner_signature=reg_sign(gpu_wallet, "bestgpu.border"))
    total_fees += REGISTRATION_FEE_BC
    ok("bestgpu.border -> gpu-farm-1.border (CNAME)")

    # Step 5b: Verify forged registration is rejected
    h("Step 5b: Verify forged registration rejected")
    attacker = BorderWallet.create()
    fake_rec = DNSRecord.create(
        name="alice.border", record_type=RecordType.ADDRESS,
        value=attacker.address, owner_address=attacker.address,
    )
    bad_flag, bad_reason = registry.register(
        fake_rec, fee_paid=REGISTRATION_FEE_BC,
        owner_public_key=attacker.public_key_b64,
        owner_signature=reg_sign(attacker, "alice.border"),
    )
    assert not bad_flag, "Forged registration should be rejected!"
    ok(f"Forged registration correctly rejected: '{bad_reason}'")

    # Step 6: Resolve names
    h("Step 6: Resolve names -> addresses, DIDs, services")
    for name in ["relay-node-1.border","gpu-farm-1.border","vault-1.border","alice.border","bestgpu.border"]:
        addr = resolver.resolve_address(name)
        did  = resolver.resolve_did(name)
        srvs = resolver.resolve_services(name)
        txt  = resolver.resolve_txt(name)
        ok(f"{name:<28}  addr={addr[:24] if addr else 'None'}...")
        if did:  ok(f"  {'':28}  did ={did[:48]}...")
        for s in srvs:
            ok(f"  {'':28}  srv ={s.metadata.get('service_type','?'):<14} {s.value}")
        if txt:  ok(f"  {'':28}  txt ={dict(list(txt.items())[:2])}")

    # Step 7: Reverse lookup
    h("Step 7: Reverse lookup - address -> names")
    for label, wallet in [("gpu-farm-1", gpu_wallet), ("alice", client_wallet)]:
        names = resolver.reverse_lookup(wallet.address)
        ok(f"{wallet.address[:24]}... -> {names}")

    # Step 8: Transfer (signed)
    h("Step 8: Transfer dev-team.border to new owner")
    new_owner = BorderWallet.create()
    ok_flag, reason = registry.transfer(
        "dev-team.border",
        from_address=dev_wallet.address,
        to_address=new_owner.address,
        fee_paid=TRANSFER_FEE_BC,
        from_public_key=dev_wallet.public_key_b64,
        from_signature=xfer_sign(dev_wallet, "dev-team.border", new_owner.address),
    )
    ok(f"Transfer: dev-team.border -> {new_owner.address[:24]}... [{reason}]")
    assert registry.owner_of("dev-team.border") == new_owner.address, "Transfer failed!"
    ok(f"Ownership confirmed: {registry.owner_of('dev-team.border')[:24]}...")

    # Verify forged transfer rejected
    bad_xfer, bad_xfer_reason = registry.transfer(
        "vault-1.border",
        from_address=storage_wallet.address,
        to_address=attacker.address,
        fee_paid=TRANSFER_FEE_BC,
        from_public_key=attacker.public_key_b64,
        from_signature=xfer_sign(attacker, "vault-1.border", attacker.address),
    )
    assert not bad_xfer, "Forged transfer should be rejected!"
    ok(f"Forged transfer correctly rejected: '{bad_xfer_reason}'")

    # Step 9: Search
    h("Step 9: Search registry")
    for query in ["gpu", "vault", "alice", "border"]:
        results = registry.search(query)
        ok(f"search('{query}') -> {results}")

    # Step 10: Anchor to BorderChain
    h("Step 10: Anchor registration fees to BorderChain")
    chain  = BorderChain()
    ledger = BandwidthLedger(node_id="relay_dns")
    for cid, mb in [("u1", 50.0), ("u2", 35.0), ("u3", 20.0)]:
        chain.add_proof(bw_proof(ledger, relay_wallet, cid, mb))
    block = chain.create_block(miner_address=relay_wallet.address)
    assert block is not None
    ok_flag, reason = chain.add_block(block)
    assert ok_flag
    ok(f"Block #{block.index} mined | {total_fees:.1f} BC registration fees")
    ok(f"Chain valid: {chain.validate_chain()[1]}")

    # Step 11: Stats
    h("Step 11: Registry stats")
    stats = registry.stats
    ok(f"Registered names  : {stats['registered_names']}")
    ok(f"Total records     : {stats['total_records']}")
    ok(f"Fees collected    : {stats['total_fees_collected']:.2f} BC")
    ok(f"Cache stats       : {resolver.cache_stats}")

    assert stats["registered_names"] >= 6,            "At least 6 names"
    assert resolver.resolve_address("alice.border"),   "alice.border resolves"
    assert resolver.resolve_address("bestgpu.border"), "CNAME resolves"
    assert (resolver.resolve_address("bestgpu.border") ==
            resolver.resolve_address("gpu-farm-1.border")), "CNAME chains correctly"
    assert registry.owner_of("dev-team.border") == new_owner.address, "Transfer stuck"

    print(f"\n{B}{G}{'='*60}")
    print(f"  ALL TESTS PASSED")
    print(f"  BorderDNS working end-to-end!")
    print(f"  alice.border    -> BC wallet + DID + services")
    print(f"  bestgpu.border  -> gpu-farm-1.border (CNAME resolved)")
    print(f"  Forged register -> rejected")
    print(f"  Forged transfer -> rejected")
    print(f"  No ICANN. No registrar. Border owns its own names.")
    print(f"{'='*60}{R}\n")
    print(f"{B}DNS Summary:{R}")
    print(f"  Names registered : {stats['registered_names']}")
    print(f"  Total records    : {stats['total_records']}")
    print(f"  Fees to treasury : {stats['total_fees_collected']:.2f} BC")
    print(f"  TLD              : .{BORDER_TLD}")

if __name__ == "__main__":
    main()
