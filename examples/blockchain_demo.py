#!/usr/bin/env python3
"""
BorderCoin Blockchain Demo
===========================
Full end-to-end demo of the Proof of Bandwidth blockchain.

What this shows:
  1. Create relay wallet (earns BorderCoin for forwarding traffic)
  2. Create client wallet (sends/receives PC)
  3. Simulate bandwidth sessions (relay forwards traffic, signs receipts)
  4. Convert receipts → BandwidthProofs and submit to chain
  5. Mine a block once 100MB threshold is crossed
  6. Check balances — relay earned BorderCoin!
  7. Send BorderCoin between wallets
  8. Print final chain stats

No running servers needed — everything runs in-process.
"""

import sys
import os
import time
import uuid
import hashlib

# Allow running from examples/ directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from phantom.blockchain import (
    BorderWallet,
    BorderChain,
    BandwidthProof,
    Transaction,
    MIN_BYTES_PER_BLOCK,
    BLOCK_REWARD,
    BC_PER_GB,
)
from phantom.ledger import BandwidthLedger

# ──────────────────────────────────────────────────────────────────────
# ANSI colours for pretty output
# ──────────────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def h(text): print(f"\n{BOLD}{CYAN}{'─'*60}{RESET}")
def ok(text): print(f"  {GREEN}✓{RESET} {text}")
def info(text): print(f"  {YELLOW}→{RESET} {text}")


def simulate_bandwidth_session(
    ledger: BandwidthLedger,
    relay_wallet: BorderWallet,
    client_id: str,
    mb: float,
) -> BandwidthProof:
    """
    Simulate a relay session: relay forwards `mb` MB to `client_id`.
    Returns a BandwidthProof ready for blockchain submission.
    """
    bytes_fwd = int(mb * 1024 * 1024)
    session_id = f"sess_{uuid.uuid4().hex[:8]}"

    # Record in the relay's bandwidth ledger
    receipt = ledger.record(
        client_id=client_id,
        bytes_forwarded=bytes_fwd,
        session_id=session_id,
    )

    # Convert receipt → BandwidthProof for the chain
    proof = BandwidthProof(
        receipt_id=receipt.receipt_id,
        relay_address=relay_wallet.address,
        client_id=client_id,
        bytes_forwarded=bytes_fwd,
        timestamp=receipt.timestamp,
        session_id=session_id,
        relay_signature=receipt.signature or "demo_sig",
        client_signature=None,
    )
    return proof


# ══════════════════════════════════════════════════════════════════════
# DEMO
# ══════════════════════════════════════════════════════════════════════

def main():
    print(f"\n{BOLD}💎 BorderCoin — Proof of Bandwidth Blockchain Demo{RESET}")
    print(f"   The work is real: mining = forwarding internet to censored users\n")

    # ──────────────────────────────────────────────────────────────────
    # 1. Create wallets
    # ──────────────────────────────────────────────────────────────────
    h("Step 1: Create wallets")

    relay_wallet  = BorderWallet.create()
    client_wallet = BorderWallet.create()
    miner_wallet  = relay_wallet   # relay is also the miner (same node)

    ok(f"Relay  wallet: {relay_wallet.address}")
    ok(f"Client wallet: {client_wallet.address}")
    info("Relay will earn BorderCoin by forwarding traffic AND mining blocks")

    # ──────────────────────────────────────────────────────────────────
    # 2. Initialise blockchain
    # ──────────────────────────────────────────────────────────────────
    h("Step 2: Initialise blockchain")

    chain = BorderChain()
    ok(f"Chain initialised | height={chain.height} | genesis block ✓")
    info(f"Rules: ≥{MIN_BYTES_PER_BLOCK/(1024*1024):.0f}MB bandwidth per block | "
         f"{BLOCK_REWARD} BC block reward | {BC_PER_GB} BC per GB forwarded")

    # ──────────────────────────────────────────────────────────────────
    # 3. Simulate bandwidth sessions
    # ──────────────────────────────────────────────────────────────────
    h("Step 3: Simulate relay sessions (censored users getting internet)")

    ledger = BandwidthLedger(node_id="relay_01")
    proofs = []

    sessions = [
        ("user_tehran_01",   35.0),   # someone in Iran
        ("user_beijing_02",  28.5),   # someone in China
        ("user_moscow_03",   22.0),   # someone in Russia
        ("user_havana_04",   15.0),   # someone in Cuba
        ("user_pyongyang_05", 8.0),   # someone in North Korea
    ]

    total_mb = 0.0
    for client_id, mb in sessions:
        proof = simulate_bandwidth_session(ledger, relay_wallet, client_id, mb)
        proofs.append(proof)
        total_mb += mb
        ok(f"Session: {client_id:<25} → {mb:>5.1f} MB forwarded | receipt: {proof.receipt_id}")

    info(f"Total bandwidth this round: {total_mb:.1f} MB")
    info(f"Minimum needed to mine a block: {MIN_BYTES_PER_BLOCK/(1024*1024):.0f} MB")

    if total_mb < MIN_BYTES_PER_BLOCK / (1024 * 1024):
        print(f"\n  {YELLOW}Not enough bandwidth yet ({total_mb:.1f}MB < 100MB). "
              f"Adding more sessions...{RESET}")
        # Add more sessions to cross threshold
        extra_sessions = [
            ("user_dubai_06",    12.0),
            ("user_caracas_07",  10.0),
        ]
        for client_id, mb in extra_sessions:
            proof = simulate_bandwidth_session(ledger, relay_wallet, client_id, mb)
            proofs.append(proof)
            total_mb += mb
            ok(f"Session: {client_id:<25} → {mb:>5.1f} MB forwarded | receipt: {proof.receipt_id}")
        info(f"Total bandwidth now: {total_mb:.1f} MB")

    # ──────────────────────────────────────────────────────────────────
    # 4. Submit proofs to the chain's pending pool
    # ──────────────────────────────────────────────────────────────────
    h("Step 4: Submit bandwidth proofs to chain")

    accepted = 0
    for proof in proofs:
        if chain.add_proof(proof):
            accepted += 1

    ok(f"Proofs submitted: {accepted}/{len(proofs)} accepted")
    info(f"Pending bandwidth in pool: {chain.pending_bandwidth_mb:.1f} MB")

    # ──────────────────────────────────────────────────────────────────
    # 5. Mine a block
    # ──────────────────────────────────────────────────────────────────
    h("Step 5: Mine a block (no wasted energy — work already done!)")

    block = chain.create_block(miner_address=relay_wallet.address)

    if block is None:
        print(f"  ✗ Not enough bandwidth to mine yet "
              f"({chain.pending_bandwidth_mb:.1f}MB < 100MB needed)")
        sys.exit(1)

    accepted, reason = chain.add_block(block)

    if accepted:
        total_bytes_gb = block.total_bytes / (1024 ** 3)
        bandwidth_reward = block.total_bandwidth_pc
        total_reward = BLOCK_REWARD + bandwidth_reward
        ok(f"Block #{block.index} mined! ⛏")
        ok(f"  Bandwidth included : {block.total_bytes/(1024*1024):.1f} MB across {len(block.bandwidth_proofs)} sessions")
        ok(f"  Block reward       : {BLOCK_REWARD:.1f} BC  (for producing the block)")
        ok(f"  Bandwidth reward   : {bandwidth_reward:.4f} BC  ({total_bytes_gb:.4f} GB × {BC_PER_GB} BC/GB)")
        ok(f"  Total miner reward : {total_reward:.4f} BC")
        ok(f"  Block hash         : {block.block_hash[:32]}...")
    else:
        print(f"  ✗ Block rejected: {reason}")
        sys.exit(1)

    # ──────────────────────────────────────────────────────────────────
    # 6. Check balances
    # ──────────────────────────────────────────────────────────────────
    h("Step 6: Check balances")

    relay_balance  = chain.get_balance(relay_wallet.address)
    client_balance = chain.get_balance(client_wallet.address)

    ok(f"Relay  balance: {relay_balance:.8f} BC  ← earned by forwarding internet traffic!")
    ok(f"Client balance: {client_balance:.8f} BC")
    info(f"Total supply  : {chain.total_supply:.8f} BC")

    # ──────────────────────────────────────────────────────────────────
    # 7. Send BorderCoin from relay to client
    # ──────────────────────────────────────────────────────────────────
    h("Step 7: Send BorderCoin (relay tips the client)")

    send_amount = round(relay_balance * 0.1, 8)  # send 10%
    info(f"Relay sends {send_amount:.8f} BC to client as a tip")

    tx = Transaction.create(
        from_address=relay_wallet.address,
        to_address=client_wallet.address,
        amount=send_amount,
        public_key=relay_wallet.public_key_b64,
    )
    tx.sign(relay_wallet)

    # Verify signature
    assert tx.verify(), "Transaction signature failed!"
    ok(f"Transaction signed and verified ✓ | tx_id={tx.tx_id}")

    # Add to mempool
    accepted = chain.add_transaction(tx)
    ok(f"Transaction added to mempool: {accepted}")

    # Mine another block to confirm it (need 100MB again — simulate it)
    info("Mining confirmation block with more bandwidth proofs...")

    more_proofs = []
    more_sessions = [
        ("user_tehran_01",    40.0),
        ("user_beijing_02",   35.0),
        ("user_moscow_03",    30.0),
    ]
    for client_id, mb in more_sessions:
        proof = simulate_bandwidth_session(ledger, relay_wallet, client_id, mb)
        more_proofs.append(proof)
        chain.add_proof(proof)

    block2 = chain.create_block(miner_address=relay_wallet.address)
    if block2:
        accepted2, _ = chain.add_block(block2)
        if accepted2:
            ok(f"Block #{block2.index} mined! Transaction confirmed ✓")

    # Final balances
    relay_final  = chain.get_balance(relay_wallet.address)
    client_final = chain.get_balance(client_wallet.address)

    ok(f"Relay  final balance: {relay_final:.8f} BC")
    ok(f"Client final balance: {client_final:.8f} BC  ← received tip!")

    # ──────────────────────────────────────────────────────────────────
    # 8. Chain stats
    # ──────────────────────────────────────────────────────────────────
    h("Step 8: Final chain stats")

    stats = chain.stats
    valid, reason = chain.validate_chain()

    ok(f"Chain height         : {stats['height']} blocks")
    ok(f"Total supply         : {stats['total_supply']:.8f} BC")
    ok(f"Block reward         : {stats['block_reward_bc']} BC per block")
    ok(f"Rate                 : {stats['bc_per_gb']} BC per GB forwarded")
    ok(f"Spent receipts       : {stats['spent_receipts']} (double-spend protected)")
    ok(f"Chain valid          : {valid} — {reason}")

    # Verify chain integrity
    assert valid, f"Chain validation failed: {reason}"
    assert chain.height == 2, f"Expected 2 blocks, got {chain.height}"
    assert relay_final > 0, "Relay should have a positive balance"
    assert client_final > 0, "Client should have received tip"

    print(f"\n{BOLD}{GREEN}{'═'*60}")
    print(f"  ALL TESTS PASSED ✓")
    print(f"  BorderCoin blockchain is working end-to-end!")
    print(f"  Mining = real internet access for censored users")
    print(f"{'═'*60}{RESET}\n")

    # Summary
    ledger_summary = ledger.get_summary()
    print(f"{BOLD}Ledger Summary:{RESET}")
    print(f"  Total bandwidth forwarded : {ledger_summary.total_bytes/(1024**2):.1f} MB")
    print(f"  Total receipts generated  : {ledger_summary.total_receipts}")
    print(f"  Unique clients served     : {ledger_summary.unique_clients}")
    print(f"  BorderCoin earned        : {relay_final:.6f} BC")
    print()


if __name__ == "__main__":
    main()
