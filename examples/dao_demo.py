#!/usr/bin/env python3
"""
BorderDAO Demo
==============
Full end-to-end governance demo.

What this shows:
  1. Protocol earns treasury fees from all services
  2. BC holders (miners, workers, storers) get voting power
  3. 4 proposals submitted:
     a) Raise block reward from 1.0 → 1.5 BC           (PASSES)
     b) Fund a developer grant from treasury             (PASSES)
     c) Enable borderless DNS feature flag               (PASSES)
     d) Slash a misbehaving node                         (REJECTED — no quorum)
  4. Votes cast, tallied, and passed proposals executed
  5. Protocol parameters update on-chain
  6. Treasury spends to dev grant recipient

No running servers. Everything in-process.
"""

import sys, os, time, uuid
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from border.blockchain import BorderWallet, BorderChain, BandwidthProof
from border.ledger     import BandwidthLedger
from border.dao import (
    GovernanceEngine, Proposal, ProposalType, ProposalStatus,
    Vote, VoteChoice, BorderTreasury, MIN_PROPOSAL_STAKE,
)

# ── Colours ───────────────────────────────────────────────
GREEN  = "\033[92m"; YELLOW = "\033[93m"; CYAN   = "\033[96m"
MAGENTA= "\033[95m"; BOLD   = "\033[1m";  RESET  = "\033[0m"
RED    = "\033[91m"
def h(t):    print(f"\n{BOLD}{CYAN}{'─'*60}{RESET}\n{BOLD}{t}{RESET}")
def ok(t):   print(f"  {GREEN}✓{RESET} {t}")
def info(t): print(f"  {YELLOW}→{RESET} {t}")
def fail(t): print(f"  {RED}✗{RESET} {t}")
def vote_(t):print(f"  {MAGENTA}◆{RESET} {t}")


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
    print(f"\n{BOLD}🏛️  BorderDAO — Community Governance Demo{RESET}")
    print(f"   BC holders vote. No corporation decides.\n")

    # ──────────────────────────────────────────────────────
    # 1. Create ecosystem participants
    # ──────────────────────────────────────────────────────
    h("Step 1: Create network participants (BC holders)")

    # Wallets with different roles and balances
    relay_wallet   = BorderWallet.create()    # 500 BC — big relay operator
    gpu_wallet     = BorderWallet.create()    # 800 BC — GPU farm owner (you)
    storage_wallet = BorderWallet.create()    # 300 BC — storage node
    client_wallet  = BorderWallet.create()    # 100 BC — regular user
    dev_wallet     = BorderWallet.create()    # 50 BC  — developer (grant recipient)
    bad_wallet     = BorderWallet.create()    # 20 BC  — alleged bad actor

    # Simulate balances (in production: from chain)
    balances = {
        relay_wallet.address:   500.0,
        gpu_wallet.address:     800.0,
        storage_wallet.address: 300.0,
        client_wallet.address:  100.0,
        dev_wallet.address:      50.0,
        bad_wallet.address:      20.0,
    }
    TOTAL_SUPPLY = sum(balances.values())  # 1770 BC

    for addr, bal in balances.items():
        ok(f"{addr[:32]}...  {bal:>8.1f} BC")
    info(f"Total supply: {TOTAL_SUPPLY:.1f} BC | 10% quorum = {TOTAL_SUPPLY*0.10:.0f} BC needed")

    # ──────────────────────────────────────────────────────
    # 2. Treasury collects protocol fees
    # ──────────────────────────────────────────────────────
    h("Step 2: Protocol fee collection into treasury")

    treasury = BorderTreasury()
    gov      = GovernanceEngine(treasury=treasury)

    # Simulate a week of network activity
    treasury.collect_tx_fee(500.0,   relay_wallet.address)     # 5.0 BC
    treasury.collect_compute_fee(200.0, gpu_wallet.address)    # 1.0 BC
    treasury.collect_storage_fee(80.0,  storage_wallet.address)# 0.4 BC
    treasury.collect_infer_fee(50.0,    gpu_wallet.address)    # 0.25 BC
    treasury.collect_render_fee(120.0,  gpu_wallet.address)    # 0.6 BC

    t_stats = treasury.stats
    ok(f"Treasury balance: {t_stats['balance']:.4f} BC")
    ok(f"Income by source: " + " | ".join(
        f"{k}={v:.4f}" for k, v in t_stats['by_source'].items()))

    # ──────────────────────────────────────────────────────
    # 3. Submit proposals
    # ──────────────────────────────────────────────────────
    h("Step 3: Proposals submitted by BC holders")

    # DIDs (simplified — just use addresses as DID for demo)
    relay_did   = f"did:border:{relay_wallet.address}"
    gpu_did     = f"did:border:{gpu_wallet.address}"
    storage_did = f"did:border:{storage_wallet.address}"

    proposals = [
        Proposal.create(
            ProposalType.PARAMETER, relay_did,
            "Raise block reward to 1.5 BC",
            "Current 1.0 BC block reward doesn't incentivise enough relay nodes. "
            "Raising to 1.5 BC will attract more operators and strengthen the network.",
            {"key": "block_reward", "value": 1.5},
            voting_period=1,   # 1 second for demo
        ),
        Proposal.create(
            ProposalType.TREASURY, gpu_did,
            "Fund core protocol developer grant",
            "Allocate 3.0 BC from treasury to fund development of BorderDNS "
            "and the Border mobile relay client.",
            {"recipient": dev_wallet.address, "amount_bc": 3.0,
             "note": "Q3 development grant — BorderDNS + mobile client"},
            voting_period=1,
        ),
        Proposal.create(
            ProposalType.PROTOCOL, storage_did,
            "Enable BorderDNS feature flag",
            "Activate the BorderDNS module so nodes can register human-readable "
            "names (alice.border) on-chain without a central registrar.",
            {"feature_flag": "border_dns"},
            voting_period=1,
        ),
        Proposal.create(
            ProposalType.SLASH, relay_did,
            "Slash node for repeated fake bandwidth proofs",
            "Node BC_bad... submitted 47 invalid bandwidth proofs in 24 hours. "
            "Propose slashing 15 BC from their stake.",
            {"target_address": bad_wallet.address, "slash_amount_bc": 15.0},
            voting_period=1,
        ),
    ]

    for p in proposals:
        ok_flag, reason = gov.submit(p, proposer_balance=balances[relay_wallet.address])
        ok(f"[{p.proposal_type:<10}] {p.title[:45]:<45} → {reason}")

    # ──────────────────────────────────────────────────────
    # 4. Cast votes
    # ──────────────────────────────────────────────────────
    h("Step 4: BC holders cast votes")

    # Voting scenario:
    # Proposal 0 (block reward): relay + gpu + storage vote YES → quorum met, passes
    # Proposal 1 (treasury):     gpu + relay YES, client NO     → passes
    # Proposal 2 (DNS feature):  all 3 big holders YES          → passes
    # Proposal 3 (slash):        only relay votes YES           → NO quorum, rejected

    voting_plan = [
        # (proposal_idx, wallet, choice, reason)
        (0, relay_wallet,   VoteChoice.YES,     "More reward = more nodes"),
        (0, gpu_wallet,     VoteChoice.YES,     "Good for network growth"),
        (0, storage_wallet, VoteChoice.YES,     "Supports relay operators"),
        (0, client_wallet,  VoteChoice.ABSTAIN, "Not sure yet"),

        (1, gpu_wallet,     VoteChoice.YES,     "Dev grants grow the ecosystem"),
        (1, relay_wallet,   VoteChoice.YES,     "BorderDNS is needed"),
        (1, storage_wallet, VoteChoice.YES,     "Support developers"),
        (1, client_wallet,  VoteChoice.NO,      "Treasury should stay larger"),

        (2, relay_wallet,   VoteChoice.YES,     "DNS is critical infrastructure"),
        (2, gpu_wallet,     VoteChoice.YES,     "Enable borderless internet"),
        (2, storage_wallet, VoteChoice.YES,     "Store DNS records on Border"),
        (2, client_wallet,  VoteChoice.YES,     "Want alice.border to work"),

        # Only bad_wallet votes against their own slash — 20/1770 = 1.1%, below 10% quorum
        (3, bad_wallet,     VoteChoice.NO,      "I did nothing wrong!"),
    ]

    for prop_idx, wallet, choice, reason in voting_plan:
        p = proposals[prop_idx]
        v = Vote.create(p.proposal_id, f"did:border:{wallet.address}",
                        wallet.address, choice,
                        weight=balances[wallet.address], reason=reason)
        v.sign(wallet)
        ok_flag, msg = gov.cast_vote(v)
        status = "✓" if ok_flag else "✗"
        vote_(f"P{prop_idx} | {choice:<7} | w={balances[wallet.address]:>6.0f} BC | "
              f"{wallet.address[:16]}... | {reason[:35]}")

    # ──────────────────────────────────────────────────────
    # 5. Tally and display results
    # ──────────────────────────────────────────────────────
    h("Step 5: Tally votes")

    time.sleep(1.1)  # let the 1-second voting period expire

    for i, p in enumerate(proposals):
        status, result = gov.tally(p.proposal_id, TOTAL_SUPPLY)
        quorum_bc = p.total_votes
        quorum_pct = (quorum_bc / TOTAL_SUPPLY) * 100

        colour = GREEN if status == ProposalStatus.PASSED else (
                 RED if status == ProposalStatus.REJECTED else YELLOW)
        print(f"\n  {colour}[{status.upper()}]{RESET} P{i}: {p.title}")
        print(f"    YES={p.votes_yes:.0f} BC  NO={p.votes_no:.0f} BC  "
              f"ABSTAIN={p.votes_abstain:.0f} BC")
        print(f"    Quorum: {quorum_bc:.0f}/{TOTAL_SUPPLY:.0f} BC ({quorum_pct:.1f}%)  "
              f"Yes%: {p.yes_pct*100:.1f}%")

    # ──────────────────────────────────────────────────────
    # 6. Execute passed proposals
    # ──────────────────────────────────────────────────────
    h("Step 6: Execute passed proposals")

    for i, p in enumerate(proposals):
        if p.status == ProposalStatus.PASSED:
            ok_flag, result = gov.execute(p.proposal_id)
            status_str = "EXECUTED" if ok_flag else "FAILED"
            colour = GREEN if ok_flag else RED
            ok(f"P{i} [{colour}{status_str}{RESET}]: {p.title[:45]} → {result}")
        else:
            fail(f"P{i} [{p.status}]: {p.title[:45]} — not executed")

    # ──────────────────────────────────────────────────────
    # 7. Verify parameter changes took effect
    # ──────────────────────────────────────────────────────
    h("Step 7: Verify protocol parameters updated")

    ok(f"block_reward       = {gov.parameters['block_reward']:.1f} BC  "
       f"(was 1.0, now {gov.parameters['block_reward']:.1f})")
    ok(f"feature_border_dns = {gov.parameters.get('feature_border_dns', 0):.0f}  "
       f"(0=disabled, 1=enabled)")
    ok(f"Treasury balance   = {treasury.balance:.4f} BC  "
       f"(spent 3.0 on dev grant)")

    # ──────────────────────────────────────────────────────
    # 8. Mine a block to anchor DAO state to chain
    # ──────────────────────────────────────────────────────
    h("Step 8: Anchor governance state to BorderChain")

    chain  = BorderChain()
    ledger = BandwidthLedger(node_id="relay_dao")

    for cid, mb in [("u1", 50.0), ("u2", 35.0), ("u3", 20.0)]:
        chain.add_proof(bw_proof(ledger, relay_wallet, cid, mb))

    block = chain.create_block(miner_address=relay_wallet.address)
    assert block is not None
    ok_flag, reason = chain.add_block(block)
    assert ok_flag

    ok(f"Block #{block.index} mined | chain valid: {chain.validate_chain()[1]}")
    info(f"DAO governance hash: {proposals[0].hash()[:32]}...")

    # ──────────────────────────────────────────────────────
    # 9. Final stats
    # ──────────────────────────────────────────────────────
    h("Step 9: DAO stats")

    stats = gov.stats
    ok(f"Total proposals  : {stats['total_proposals']}")
    ok(f"Passed           : {stats['by_status'].get('passed', 0) + stats['by_status'].get('executed', 0)}")
    ok(f"Rejected         : {stats['by_status'].get('rejected', 0)}")
    ok(f"Treasury balance : {treasury.balance:.4f} BC")
    ok(f"Treasury total in: {treasury.total_collected:.4f} BC")
    ok(f"Dev grant paid   : {treasury.total_spent:.4f} BC → {dev_wallet.address[:20]}...")

    # ──────────────────────────────────────────────────────
    # Assertions
    # ──────────────────────────────────────────────────────
    assert gov.parameters["block_reward"] == 1.5,          "Block reward must be 1.5"
    assert gov.parameters.get("feature_border_dns") == 1.0,"DNS feature must be enabled"
    assert treasury.balance < treasury.total_collected,    "Treasury must have spent"
    assert proposals[3].status == ProposalStatus.REJECTED, "Slash must be rejected"
    assert proposals[0].status == ProposalStatus.EXECUTED, "Block reward must execute"
    assert proposals[1].status == ProposalStatus.EXECUTED, "Treasury grant must execute"
    assert proposals[2].status == ProposalStatus.EXECUTED, "DNS flag must execute"
    assert chain.height == 1,                              "One block mined"

    print(f"\n{BOLD}{GREEN}{'═'*60}")
    print(f"  ALL TESTS PASSED ✓")
    print(f"  BorderDAO is working end-to-end!")
    print(f"")
    print(f"  Block reward raised to 1.5 BC — governance worked.")
    print(f"  Dev grant paid. BorderDNS enabled. Bad actor rejected.")
    print(f"  No company decided. BC holders decided.")
    print(f"{'═'*60}{RESET}\n")

    print(f"{BOLD}Governance Summary:{RESET}")
    print(f"  Total supply     : {TOTAL_SUPPLY:.0f} BC")
    print(f"  Proposals passed : {stats['by_status'].get('executed', 0)}")
    print(f"  Proposals rejected: {stats['by_status'].get('rejected', 0)}")
    print(f"  Treasury in/out  : {treasury.total_collected:.4f} / {treasury.total_spent:.4f} BC")
    print(f"  block_reward     : {gov.parameters['block_reward']} BC (was 1.0)")
    print(f"  BorderDNS        : {'ENABLED' if gov.parameters.get('feature_border_dns') else 'DISABLED'}")
    print()


if __name__ == "__main__":
    main()
