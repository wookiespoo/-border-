#!/usr/bin/env python3
"""
Border Protocol — Economic Simulation Model
============================================
Simulates token supply, miner revenue, staking yields, and network health
over 10 years at three network-size scenarios: small (100 nodes),
medium (10,000 nodes), and large (1,000,000 nodes).

Run:
    python3 examples/economic_simulation.py

Outputs a summary table + CSV files for each scenario.
"""

import csv
import math
import os
from dataclasses import dataclass, field
from typing import List

# ── Protocol constants ─────────────────────────────────────────────────────────

GENESIS_REWARD      = 50.0          # BC per block at genesis
HALVING_INTERVAL    = 210_000       # blocks between halvings
MAX_SUPPLY          = 21_000_000.0  # BC
TARGET_BLOCK_TIME   = 600           # seconds (10 min)
BLOCKS_PER_YEAR     = 365 * 24 * 3600 // TARGET_BLOCK_TIME   # ≈ 52,560
TX_FEE_PCT          = 0.001         # 0.1% of TX volume as fees
BC_PER_GB           = 0.1           # relay bandwidth reward
BC_PER_COMPUTE_HOUR = 0.05
BC_PER_GB_STORAGE   = 0.001         # per GB per day
OPEN_CHANNEL_FEE    = 0.01          # payment channel anti-spam

MIN_STAKE = {
    "RELAY":   1.0,
    "COMPUTE": 5.0,
    "STORAGE": 2.0,
}

# ── Scenario parameters ────────────────────────────────────────────────────────

@dataclass
class Scenario:
    name: str
    node_count: int
    relay_pct: float       # fraction of nodes that are relays
    compute_pct: float
    storage_pct: float
    gb_per_node_day: float     # traffic each relay forwards per day
    tx_volume_bc_day: float    # on-chain TX volume per day (BC)
    compute_hours_day: float   # total compute hours sold per day
    storage_gb: float          # total GB stored
    channel_opens_day: int     # payment channels opened per day
    adoption_growth: float     # annual growth multiplier for usage metrics


SCENARIOS = [
    Scenario(
        name="Small (100 nodes)",
        node_count=100,
        relay_pct=0.7, compute_pct=0.2, storage_pct=0.1,
        gb_per_node_day=10.0,
        tx_volume_bc_day=500.0,
        compute_hours_day=50.0,
        storage_gb=500.0,
        channel_opens_day=20,
        adoption_growth=2.0,
    ),
    Scenario(
        name="Medium (10,000 nodes)",
        node_count=10_000,
        relay_pct=0.65, compute_pct=0.25, storage_pct=0.1,
        gb_per_node_day=50.0,
        tx_volume_bc_day=100_000.0,
        compute_hours_day=5_000.0,
        storage_gb=500_000.0,
        channel_opens_day=5_000,
        adoption_growth=1.5,
    ),
    Scenario(
        name="Large (1M nodes)",
        node_count=1_000_000,
        relay_pct=0.6, compute_pct=0.3, storage_pct=0.1,
        gb_per_node_day=200.0,
        tx_volume_bc_day=50_000_000.0,
        compute_hours_day=500_000.0,
        storage_gb=1_000_000_000.0,
        channel_opens_day=1_000_000,
        adoption_growth=1.3,
    ),
]


# ── Model ─────────────────────────────────────────────────────────────────────

@dataclass
class YearResult:
    year: int
    blocks_mined: int
    block_reward: float
    total_supply: float
    miner_income_bc: float          # block rewards + TX fees + BW rewards
    relay_income_per_node_bc: float
    compute_income_per_node_bc: float
    storage_income_per_node_bc: float
    total_staked_bc: float
    staking_yield_pct: float        # annual yield on staked capital
    tx_fee_income_bc: float
    bandwidth_reward_bc: float
    compute_reward_bc: float
    storage_reward_bc: float
    channel_fee_income_bc: float
    network_health: float           # composite 0–1 score


def _block_reward(block_index: int) -> float:
    halvings = block_index // HALVING_INTERVAL
    reward = GENESIS_REWARD / (2 ** halvings)
    return max(reward, 0.0)


def simulate_year(
    scenario: Scenario,
    year: int,
    start_block: int,
    supply_so_far: float,
) -> YearResult:
    growth = scenario.adoption_growth ** (year - 1)

    # Scale usage metrics by growth
    gb_day       = scenario.gb_per_node_day * growth
    tx_vol       = scenario.tx_volume_bc_day * growth
    comp_hours   = scenario.compute_hours_day * growth
    store_gb     = scenario.storage_gb * growth
    chan_opens   = scenario.channel_opens_day * growth

    blocks = BLOCKS_PER_YEAR

    # Block rewards this year
    total_block_reward = 0.0
    for i in range(blocks):
        r = _block_reward(start_block + i)
        total_block_reward += r
        if supply_so_far + total_block_reward >= MAX_SUPPLY:
            total_block_reward = MAX_SUPPLY - supply_so_far
            break
    avg_block_reward = total_block_reward / blocks

    # TX fee income (annual)
    tx_fee_annual = tx_vol * TX_FEE_PCT * 365

    # Bandwidth reward (relay nodes earn per GB forwarded)
    relay_nodes  = int(scenario.node_count * scenario.relay_pct)
    bw_annual    = relay_nodes * gb_day * BC_PER_GB * 365

    # Compute reward
    compute_nodes   = int(scenario.node_count * scenario.compute_pct)
    compute_annual  = comp_hours * BC_PER_COMPUTE_HOUR * 365

    # Storage reward
    storage_nodes   = int(scenario.node_count * scenario.storage_pct)
    storage_annual  = store_gb * BC_PER_GB_STORAGE * 365

    # Payment channel fee income (open fee × opens)
    channel_annual  = chan_opens * OPEN_CHANNEL_FEE * 365

    # Total miner income = block rewards + TX fees (fees go to miners)
    miner_income = total_block_reward + tx_fee_annual

    # Per-node incomes
    relay_per_node   = bw_annual / max(relay_nodes, 1)
    compute_per_node = compute_annual / max(compute_nodes, 1)
    storage_per_node = storage_annual / max(storage_nodes, 1)

    # Total staked BC
    total_staked = (
        relay_nodes   * MIN_STAKE["RELAY"] +
        compute_nodes * MIN_STAKE["COMPUTE"] +
        storage_nodes * MIN_STAKE["STORAGE"]
    )

    # Staking yield = (all node income from work) / total_staked
    total_node_income = bw_annual + compute_annual + storage_annual
    staking_yield = (total_node_income / max(total_staked, 1)) * 100

    # Supply
    new_supply = min(supply_so_far + total_block_reward, MAX_SUPPLY)

    # Network health score (0–1): nodes > 10, supply < 90% max, yield > 1%
    health_nodes   = min(scenario.node_count / 1000, 1.0)
    health_supply  = 1.0 - (new_supply / MAX_SUPPLY)
    health_yield   = min(staking_yield / 20.0, 1.0)
    network_health = (health_nodes * 0.4 + health_supply * 0.3 + health_yield * 0.3)

    return YearResult(
        year=year,
        blocks_mined=blocks,
        block_reward=avg_block_reward,
        total_supply=new_supply,
        miner_income_bc=miner_income,
        relay_income_per_node_bc=relay_per_node,
        compute_income_per_node_bc=compute_per_node,
        storage_income_per_node_bc=storage_per_node,
        total_staked_bc=total_staked,
        staking_yield_pct=staking_yield,
        tx_fee_income_bc=tx_fee_annual,
        bandwidth_reward_bc=bw_annual,
        compute_reward_bc=compute_annual,
        storage_reward_bc=storage_annual,
        channel_fee_income_bc=channel_annual,
        network_health=network_health,
    )


def run_scenario(scenario: Scenario, years: int = 10) -> List[YearResult]:
    results = []
    supply  = 0.0
    block   = 0
    for y in range(1, years + 1):
        r = simulate_year(scenario, y, block, supply)
        results.append(r)
        supply = r.total_supply
        block += r.blocks_mined
    return results


# ── Output ────────────────────────────────────────────────────────────────────

def print_summary(scenario: Scenario, results: List[YearResult]) -> None:
    print(f"\n{'='*80}")
    print(f"  {scenario.name}")
    print(f"  Nodes: {scenario.node_count:,}  |  "
          f"Relay: {int(scenario.relay_pct*100)}%  "
          f"Compute: {int(scenario.compute_pct*100)}%  "
          f"Storage: {int(scenario.storage_pct*100)}%")
    print(f"{'='*80}")
    print(f"{'Year':>4}  {'Supply':>12}  {'Blk Reward':>10}  "
          f"{'Staking Yield':>13}  {'Relay/node':>10}  "
          f"{'Health':>6}")
    print(f"{'-'*4}  {'-'*12}  {'-'*10}  {'-'*13}  {'-'*10}  {'-'*6}")
    for r in results:
        print(f"{r.year:>4}  {r.total_supply:>12,.0f}  "
              f"{r.block_reward:>10.4f}  "
              f"{r.staking_yield_pct:>12.1f}%  "
              f"{r.relay_income_per_node_bc:>10.2f}  "
              f"{r.network_health:>6.2f}")


def save_csv(scenario: Scenario, results: List[YearResult], out_dir: str) -> str:
    fname = os.path.join(out_dir, f"sim_{scenario.name.split()[0].lower()}.csv")
    fields = [
        "year", "total_supply", "block_reward", "miner_income_bc",
        "tx_fee_income_bc", "bandwidth_reward_bc", "compute_reward_bc",
        "storage_reward_bc", "total_staked_bc", "staking_yield_pct",
        "relay_income_per_node_bc", "compute_income_per_node_bc",
        "storage_income_per_node_bc", "network_health",
    ]
    with open(fname, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in results:
            w.writerow({k: getattr(r, k) for k in fields})
    return fname


def print_key_findings(all_results: dict) -> None:
    print(f"\n{'='*80}")
    print("  KEY FINDINGS")
    print(f"{'='*80}")

    for name, results in all_results.items():
        r10 = results[-1]
        r1  = results[0]
        print(f"\n  [{name}]")
        print(f"    Year-1  staking yield: {r1.staking_yield_pct:.1f}%  "
              f"relay income/node: {r1.relay_income_per_node_bc:.2f} BC")
        print(f"    Year-10 staking yield: {r10.staking_yield_pct:.1f}%  "
              f"relay income/node: {r10.relay_income_per_node_bc:.2f} BC")
        print(f"    Supply after 10y: {r10.total_supply:,.0f} BC "
              f"({r10.total_supply/MAX_SUPPLY*100:.1f}% of max)")
        print(f"    Total staked: {r10.total_staked_bc:,.0f} BC  "
              f"Network health: {r10.network_health:.2f}")

    print(f"\n  OBSERVATIONS")
    print("  • Block rewards halve every ~4 years; fee + BW rewards must compensate.")
    print("  • Staking yields are usage-driven: more traffic = higher relay income.")
    print("  • At 1M nodes, total relay BW rewards dwarf block subsidies by year 5.")
    print("  • The 21M cap is not reached within 10 years at any scenario.")
    print("  • Small networks are healthy only if adoption growth is sustained.")


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    out_dir = os.path.dirname(os.path.abspath(__file__))
    all_results = {}

    for scenario in SCENARIOS:
        results = run_scenario(scenario, years=10)
        print_summary(scenario, results)
        csv_path = save_csv(scenario, results, out_dir)
        print(f"  → CSV saved: {csv_path}")
        all_results[scenario.name] = results

    print_key_findings(all_results)
    print(f"\nDone. CSVs written to {out_dir}/sim_*.csv\n")
