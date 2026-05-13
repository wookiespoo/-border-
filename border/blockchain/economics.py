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

Difficulty / block-time targeting
----------------------------------
  TARGET_BLOCK_TIME                600 s   (10 minutes)
  DIFFICULTY_ADJUSTMENT_INTERVAL  2016 blocks  (~2 weeks)
  MIN_DIFFICULTY                  1 MB
  MAX_DIFFICULTY                  10 GB
  RETARGET_CLAMP                  4×

These constants are imported by block.py and chain.py so the whole
stack uses a single source of truth.
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
# Difficulty / block-time targeting
# ---------------------------------------------------------------------------

# Target 10-minute block intervals (same as Bitcoin)
TARGET_BLOCK_TIME: float          = 600.0        # seconds

# Retarget every 2016 blocks (~2 weeks at 10 min/block)
DIFFICULTY_ADJUSTMENT_INTERVAL: int = 2016

# Hard floor: 1 MB — even empty-ish testnet blocks must prove *something*
MIN_DIFFICULTY: int = 1 * 1024 * 1024            # 1 MB

# Hard ceiling: 10 GB — prevents runaway upward retargets
MAX_DIFFICULTY: int = 10 * 1024 * 1024 * 1024    # 10 GB

# Clamp factor: difficulty may not change by more than 4x per interval
RETARGET_CLAMP: float = 4.0

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


# ---------------------------------------------------------------------------
# Difficulty adjustment
# ---------------------------------------------------------------------------

def calculate_next_difficulty(
    current_difficulty: int,
    interval_start_ts: float,
    interval_end_ts: float,
) -> int:
    """
    Bitcoin-style difficulty retarget.

    Computes how long the last DIFFICULTY_ADJUSTMENT_INTERVAL blocks actually
    took, then scales current_difficulty proportionally to hit TARGET_BLOCK_TIME
    per block.  Change is clamped to [1/RETARGET_CLAMP, RETARGET_CLAMP] and
    the result is bounded by [MIN_DIFFICULTY, MAX_DIFFICULTY].

    Args:
        current_difficulty:  bytes threshold currently in use.
        interval_start_ts:   timestamp of the block at the START of the window
                             (block at height n - DIFFICULTY_ADJUSTMENT_INTERVAL).
        interval_end_ts:     timestamp of the most-recently-added block.

    Returns:
        New difficulty (bytes threshold) as an int.
    """
    expected_time = DIFFICULTY_ADJUSTMENT_INTERVAL * TARGET_BLOCK_TIME
    actual_time   = max(1.0, interval_end_ts - interval_start_ts)

    # Clamp: no more than 4x adjustment in either direction
    actual_time = max(actual_time, expected_time / RETARGET_CLAMP)
    actual_time = min(actual_time, expected_time * RETARGET_CLAMP)

    new_difficulty = int(current_difficulty * actual_time / expected_time)

    # Enforce hard floor / ceiling
    return max(MIN_DIFFICULTY, min(MAX_DIFFICULTY, new_difficulty))
