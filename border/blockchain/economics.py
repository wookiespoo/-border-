"""
border.blockchain.economics — BorderCoin token economics

Supply schedule
---------------
  MAX_SUPPLY           21_000_000 BC  (hard cap, like Bitcoin)
  INITIAL_BLOCK_REWARD 50.0 BC
  HALVING_INTERVAL     210_000 blocks (~4 years at 1 block/10 min)

  reward(height) = INITIAL_BLOCK_REWARD / 2^(height // HALVING_INTERVAL)
  Reward rounds to 8 decimal places; once < 1 satoshi (0.00000001) -> 0.

Fee market
----------
  MIN_FEE_PER_TX  0.0001 BC  -- absolute floor, node rejects below this
  Miners order mempool txns by fee descending (highest fee first).

Service rewards (per block, on top of block reward)
--------------------------------------------------------------
  BC_PER_GB              1.0   -- bandwidth forwarded
  BC_PER_COMPUTE_HOUR    2.0   -- GPU compute
  BC_PER_GB_PER_DAY      0.01  -- storage kept per day

These constants are imported by block.py and chain.py so the whole
stack uses a single source of truth.

Utility functions
-----------------
  block_reward(height)       -> float    current coinbase for this height
  cumulative_supply(height)  -> float    total BC issued through height
  fee_sort_key(tx)           -> float    sort key for mempool ordering
  validate_fee(tx)           -> bool     True if tx meets MIN_FEE_PER_TX
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .transaction import Transaction

# ---------------------------------------------------------------------------
# Supply constants
# ---------------------------------------------------------------------------

MAX_SUPPLY: float            = 21_000_000.0
INITIAL_BLOCK_REWARD: float  = 50.0
HALVING_INTERVAL: int        = 210_000        # blocks

# Service rewards per unit
BC_PER_GB: float             = 1.0
BC_PER_COMPUTE_HOUR: float   = 2.0
BC_PER_GB_PER_DAY: float     = 0.01

# Fee market floor
MIN_FEE_PER_TX: float        = 0.0001
SATOSHI: float               = 0.00000001    # minimum unit

# ---------------------------------------------------------------------------
# Block reward schedule
# ---------------------------------------------------------------------------

def block_reward(height: int) -> float:
    """
    Coinbase reward at a given block height.

    Halves every HALVING_INTERVAL blocks.
    Returns 0.0 once reward falls below one satoshi.

        height=0..209_999   -> 50.0 BC
        height=210_000..    -> 25.0 BC
        height=420_000..    -> 12.5 BC  ... etc.
    """
    halvings = height // HALVING_INTERVAL
    if halvings >= 64:          # 2^64 shift would underflow to zero
        return 0.0
    # Check raw value BEFORE rounding — values like 5.8e-9 round UP to
    # 1 satoshi but are genuinely sub-satoshi and should return 0.
    reward = INITIAL_BLOCK_REWARD / (2 ** halvings)
    if reward < SATOSHI:
        return 0.0
    return round(reward, 8)


def cumulative_supply(height: int) -> float:
    """
    Total BC ever emitted as block rewards through `height` (inclusive),
    ignoring service/fee bonuses.  Useful for checking MAX_SUPPLY headroom.
    """
    total = 0.0
    era_start = 0
    current_reward = INITIAL_BLOCK_REWARD
    remaining = height + 1      # number of blocks to account for

    while remaining > 0 and current_reward >= SATOSHI:
        blocks_in_era = min(HALVING_INTERVAL, remaining)
        total += blocks_in_era * current_reward
        remaining -= blocks_in_era
        current_reward = round(current_reward / 2, 8)

    return round(min(total, MAX_SUPPLY), 8)


# ---------------------------------------------------------------------------
# Fee market helpers
# ---------------------------------------------------------------------------

def fee_sort_key(tx: "Transaction") -> float:
    """Descending sort key — higher fee = higher priority."""
    return -tx.fee


def validate_fee(tx: "Transaction") -> bool:
    """
    Return True if the transaction meets the minimum fee requirement.
    Coinbase transactions are always exempt.
    """
    from .transaction import Transaction as Tx
    if tx.from_address == Tx.COINBASE_ADDRESS:
        return True
    return tx.fee >= MIN_FEE_PER_TX


def supply_headroom(current_supply: float) -> float:
    """How many BC can still be minted before hitting MAX_SUPPLY."""
    return max(0.0, round(MAX_SUPPLY - current_supply, 8))
