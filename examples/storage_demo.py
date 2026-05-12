#!/usr/bin/env python3
"""
BorderStore Demo
=================
Full end-to-end demo of the BorderStore network.

What this shows:
  1. Create wallets (owner + storage node operator)
  2. Start 3 in-process storage nodes with different capacities
  3. Upload a file — split into chunks, encrypted, distributed
  4. Challenge all nodes — prove they hold the data
  5. Build StorageProofs from challenge results
  6. Submit proofs to BorderChain
  7. Mine a block — storage rewards included
  8. Node operators earned BorderCoin passively
  9. Download + verify file integrity

No running servers — everything in-process.
"""

import sys
import os
import asyncio
import hashlib
import json
import time
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from phantom.blockchain import (
    BorderWallet, BorderChain, BandwidthProof, StorageProofRecord,
    BLOCK_REWARD, BC_PER_GB, BC_PER_GB_PER_DAY, MIN_BYTES_PER_BLOCK,
)
from phantom.storage import (
    FileChunker, FileManifest, StorageChallenge, StorageProof,
    BorderStorageNode, BC_PER_GB_PER_DAY as STORAGE_RATE,
)
from phantom.ledger import BandwidthLedger

GREEN  = "\033[92m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
MAGENTA= "\033[95m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def h(text):   print(f"\n{BOLD}{CYAN}{'─'*60}{RESET}\n{BOLD}{text}{RESET}")
def ok(text):  print(f"  {GREEN}✓{RESET} {text}")
def info(text):print(f"  {YELLOW}→{RESET} {text}")
def store(text):print(f"  {MAGENTA}💾{RESET} {text}")


def make_bandwidth_proof(relay_wallet, client_id, mb):
    import uuid as _u
    from phantom.ledger import BandwidthLedger
    ledger = BandwidthLedger(node_id="relay_demo")
    bytes_fwd = int(mb * 1024 * 1024)
    session_id = f"sess_{_u.uuid4().hex[:8]}"
    receipt = ledger.record(client_id=client_id, bytes_forwarded=bytes_fwd, session_id=session_id)
    return BandwidthProof(
        receipt_id=receipt.receipt_id, relay_address=relay_wallet.address,
        client_id=client_id, bytes_forwarded=bytes_fwd,
        timestamp=receipt.timestamp, session_id=session_id,
        relay_signature=receipt.signature or "demo_sig",
    )


async def main():
    print(f"\n{BOLD}💾 BorderStore — Decentralised Encrypted Storage Demo{RESET}")
    print(f"   Store files across the network. Earn BC. Never see plaintext.\n")

    # ──────────────────────────────────────────────────────
    # 1. Wallets
    # ──────────────────────────────────────────────────────
    h("Step 1: Create wallets")

    owner_wallet  = BorderWallet.create()   # file owner
    node1_wallet  = BorderWallet.create()   # storage node 1 operator
    node2_wallet  = BorderWallet.create()   # storage node 2 operator
    node3_wallet  = BorderWallet.create()   # storage node 3 operator
    miner_wallet  = node1_wallet            # node1 also mines

    ok(f"Owner  wallet : {owner_wallet.address}")
    ok(f"Node 1 wallet : {node1_wallet.address}")
    ok(f"Node 2 wallet : {node2_wallet.address}")
    ok(f"Node 3 wallet : {node3_wallet.address}")
    info("Node operators earn BC passively — just for keeping data")

    # ──────────────────────────────────────────────────────
    # 2. Start storage nodes (in-process)
    # ──────────────────────────────────────────────────────
    h("Step 2: Start 3 storage nodes")

    import tempfile, pathlib
    tmpdir = tempfile.mkdtemp()

    nodes = [
        BorderStorageNode(
            node_id=f"snode_{i+1}",
            wallet=w,
            storage_path=f"{tmpdir}/node{i+1}",
            capacity_gb=cap,
            endpoint=f"http://localhost:{9999+i}",
            region=region,
        )
        for i, (w, cap, region) in enumerate([
            (node1_wallet, 500.0, "US-EAST"),
            (node2_wallet, 250.0, "EU-WEST"),
            (node3_wallet, 100.0, "ASIA"),
        ])
    ]

    for node in nodes:
        store(f"{node.node_id} | {node.capacity_gb}GB | {node.region} | {node.wallet.address[:20]}...")

    ok(f"3 storage nodes online | total capacity: {sum(n.capacity_gb for n in nodes):.0f}GB")
    info(f"Earning rate: {STORAGE_RATE} BC per GB per day")

    # ──────────────────────────────────────────────────────
    # 3. Create a test file and chunk it
    # ──────────────────────────────────────────────────────
    h("Step 3: Chunk + encrypt a file")

    # Simulate a 9MB file (3 chunks of 3MB each for demo)
    from phantom.storage.chunk import CHUNK_SIZE
    chunk_size  = 3 * 1024 * 1024  # 3MB chunks for demo
    import os as _os; file_data = _os.urandom(9 * 1024 * 1024)  # 9MB random
    file_id     = f"file_{uuid.uuid4().hex[:16]}"
    filename    = "border_testfile.bin"
    content_hash = hashlib.sha256(file_data).hexdigest()

    chunker = FileChunker(chunk_size=chunk_size)
    chunks, ch = chunker.split_file.__func__(chunker, None, file_id) if False else (
        chunker.split(file_data, file_id), content_hash
    )

    ok(f"File: {filename} | {len(file_data)/(1024*1024):.1f}MB")
    ok(f"Content hash: {content_hash[:32]}...")
    ok(f"Chunks: {len(chunks)} × {chunk_size/(1024*1024):.0f}MB each")
    info("Encryption keys stay with the client — nodes only see ciphertext")

    for i, chunk in enumerate(chunks):
        info(f"  Chunk {i}: {chunk.chunk_id[:20]}... | {chunk.size/(1024*1024):.1f}MB | encrypted ✓")

    # ──────────────────────────────────────────────────────
    # 4. Distribute chunks across nodes (replicate to all 3)
    # ──────────────────────────────────────────────────────
    h("Step 4: Distribute chunks to storage nodes")

    node_map = {}
    for chunk in chunks:
        placed = []
        for node in nodes:
            meta = {
                "file_id":       chunk.file_id,
                "index":         chunk.index,
                "owner_address": owner_wallet.address,
            }
            ok_stored = node.store_chunk(chunk.chunk_id, chunk.ciphertext, meta)
            if ok_stored:
                placed.append(node.endpoint)
                store(f"Chunk {chunk.chunk_id[:16]}... → {node.node_id} ({node.region})")
        node_map[chunk.chunk_id] = placed

    manifest = chunker.build_manifest(
        file_id=file_id, filename=filename, chunks=chunks,
        owner_address=owner_wallet.address,
        content_hash=content_hash, node_map=node_map,
    )

    ok(f"File distributed | {len(chunks)} chunks × {len(nodes)} replicas = {len(chunks)*len(nodes)} total copies")
    ok(f"Manifest: file_id={file_id} | {manifest.size_gb*1024:.1f}MB | {manifest.chunk_count} chunks")

    # ──────────────────────────────────────────────────────
    # 5. Challenge nodes — prove they have the data
    # ──────────────────────────────────────────────────────
    h("Step 5: Challenge all nodes — prove possession")

    storage_proofs_for_chain = []
    challenges_passed = 0

    for chunk in chunks:
        for node in nodes:
            # Issue challenge
            challenge = StorageChallenge.issue(
                chunk_id=chunk.chunk_id,
                node_address=node.wallet.address,
            )

            # Node responds
            response = node.respond_to_challenge(challenge)
            if response is None:
                ok(f"  ✗ {node.node_id} doesn't have chunk {chunk.chunk_id[:12]}...")
                continue

            # Verify response
            ciphertext = node.retrieve_chunk(chunk.chunk_id)
            expected   = challenge.expected_response(ciphertext)
            passed     = (response == expected)

            if passed:
                challenges_passed += 1

                # Build proof
                proof = StorageProof.from_challenge(
                    challenge=challenge,
                    node_address=node.wallet.address,
                    owner_address=owner_wallet.address,
                    file_id=file_id,
                    bytes_stored=len(ciphertext),
                    response_hash=response,
                    expected_hash=expected,
                )
                proof.node_signature = node.wallet.sign(proof.hash().encode())

                bc_earned = proof.reward_bc()
                ok(f"  {node.node_id} passed | chunk {chunk.chunk_id[:16]}... | +{bc_earned:.8f} BC")

                # Convert to on-chain record
                chain_record = StorageProofRecord(
                    proof_id=proof.proof_id,
                    proof_type=proof.proof_type,
                    node_address=proof.node_address,
                    owner_address=proof.owner_address,
                    chunk_id=proof.chunk_id,
                    file_id=proof.file_id,
                    bytes_stored=proof.bytes_stored,
                    duration_seconds=proof.duration_seconds,
                    timestamp=proof.timestamp,
                    reward_bc=bc_earned,
                )
                storage_proofs_for_chain.append(chain_record)
            else:
                ok(f"  ✗ {node.node_id} FAILED challenge for {chunk.chunk_id[:12]}...")

    ok(f"Challenges: {challenges_passed}/{len(chunks)*len(nodes)} passed")

    # ──────────────────────────────────────────────────────
    # 6. Submit to blockchain
    # ──────────────────────────────────────────────────────
    h("Step 6: Submit storage proofs to BorderChain")

    chain = BorderChain()

    # Add bandwidth proofs (needed to mine a block)
    relay_wallet = miner_wallet
    bw_sessions = [("user_tehran_01", 40.0), ("user_beijing_02", 35.0), ("user_moscow_03", 30.0)]
    for client_id, mb in bw_sessions:
        bp = make_bandwidth_proof(relay_wallet, client_id, mb)
        chain.add_proof(bp)

    # Add storage proofs
    for sp in storage_proofs_for_chain:
        chain.add_storage_proof(sp)

    total_storage_bc = sum(p.reward_bc for p in storage_proofs_for_chain)
    ok(f"Storage proofs queued: {len(storage_proofs_for_chain)}")
    ok(f"Pending storage rewards: {total_storage_bc:.8f} BC")
    info(f"Bandwidth pool: {chain.pending_bandwidth_mb:.1f}MB")

    # ──────────────────────────────────────────────────────
    # 7. Mine a block
    # ──────────────────────────────────────────────────────
    h("Step 7: Mine a block — bandwidth + storage proofs included")

    block = chain.create_block(miner_address=miner_wallet.address)
    assert block is not None, "Not enough bandwidth!"

    accepted, reason = chain.add_block(block)
    assert accepted, f"Block rejected: {reason}"

    bw_reward   = block.total_bandwidth_pc
    stor_reward = block.total_storage_bc
    total_reward = BLOCK_REWARD + bw_reward + stor_reward

    ok(f"Block #{block.index} mined! ⛏")
    ok(f"  Bandwidth proofs  : {len(block.bandwidth_proofs)} | {block.total_bytes/(1024*1024):.1f}MB")
    ok(f"  Storage proofs    : {len(block.storage_proofs)} challenges passed")
    ok(f"  Block reward      : {BLOCK_REWARD:.1f} BC")
    ok(f"  Bandwidth reward  : {bw_reward:.4f} BC")
    ok(f"  Storage reward    : {stor_reward:.8f} BC")
    ok(f"  TOTAL reward      : {total_reward:.6f} BC → {miner_wallet.address[:20]}...")

    # ──────────────────────────────────────────────────────
    # 8. Check balances
    # ──────────────────────────────────────────────────────
    h("Step 8: Balances — nodes earned BC for storing data")

    miner_balance = chain.get_balance(miner_wallet.address)
    ok(f"Node 1 balance : {miner_balance:.8f} BC  ← earned by storing + mining!")
    ok(f"Total supply   : {chain.total_supply:.8f} BC")

    # ──────────────────────────────────────────────────────
    # 9. Download + verify file
    # ──────────────────────────────────────────────────────
    h("Step 9: Download file + verify integrity")

    # Reconstruct from the first node (could use any)
    chunk_data = []
    for entry in manifest.chunk_entries:
        chunk_id = entry["chunk_id"]
        key      = bytes.fromhex(entry["key"])
        # Fetch from node 1
        ciphertext = nodes[0].retrieve_chunk(chunk_id)
        assert ciphertext is not None, f"Missing chunk {chunk_id}"
        plaintext = bytes(a ^ b for a, b in zip(ciphertext, key))
        assert hashlib.sha256(plaintext).hexdigest() == chunk_id, "Chunk integrity fail!"
        chunk_data.append((entry["index"], plaintext))

    reassembled = chunker.reassemble(chunk_data)
    actual_hash = hashlib.sha256(reassembled).hexdigest()

    ok(f"Downloaded: {len(reassembled)/(1024*1024):.1f}MB")
    ok(f"Content hash matches: {actual_hash == content_hash}")
    ok(f"File integrity: ✓ bit-perfect reconstruction")

    # ──────────────────────────────────────────────────────
    # 10. Chain validation + stats
    # ──────────────────────────────────────────────────────
    h("Step 10: Chain stats + validation")

    valid, reason = chain.validate_chain()
    stats = chain.stats

    ok(f"Chain height       : {stats['height']}")
    ok(f"Chain valid        : {valid} — {reason}")
    ok(f"Total supply       : {stats['total_supply']:.8f} BC")
    ok(f"Spent bw proofs    : {stats['spent_receipts']}")
    ok(f"Spent storage proofs: {stats['spent_storage_proofs']}")

    # Assertions
    assert valid,                "Chain must be valid"
    assert actual_hash == content_hash, "File must survive round-trip"
    assert miner_balance > 0,   "Node must have earned BC"
    assert challenges_passed == len(chunks) * len(nodes), "All challenges must pass"

    print(f"\n{BOLD}{GREEN}{'═'*60}")
    print(f"  ALL TESTS PASSED ✓")
    print(f"  BorderStore working end-to-end!")
    print(f"")
    print(f"  Upload → encrypt → distribute → challenge → prove → earn BC")
    print(f"  Files are safe. Nodes are paid. No central server.")
    print(f"{'═'*60}{RESET}\n")

    print(f"{BOLD}Storage Summary:{RESET}")
    print(f"  File size       : {len(file_data)/(1024*1024):.1f}MB")
    print(f"  Chunks          : {len(chunks)} × {chunk_size/(1024*1024):.0f}MB")
    print(f"  Replicas        : {len(nodes)} nodes")
    print(f"  Challenges passed: {challenges_passed}")
    print(f"  Storage BC earned: {total_storage_bc:.8f} BC")
    print(f"  Earning rate    : {STORAGE_RATE} BC/GB/day")
    print()


if __name__ == "__main__":
    asyncio.run(main())
