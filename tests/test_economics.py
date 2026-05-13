"""Tests for border.blockchain.economics — supply schedule, fees, difficulty."""
import pytest
from border.blockchain.economics import (
    block_reward, cumulative_supply, validate_fee, supply_headroom,
    calculate_next_difficulty,
    HALVING_INTERVAL, INITIAL_BLOCK_REWARD, MAX_SUPPLY, MIN_FEE_PER_TX,
    SATOSHI, MIN_DIFFICULTY, MAX_DIFFICULTY, DIFFICULTY_ADJUSTMENT_INTERVAL,
    TARGET_BLOCK_TIME,
)
from border.blockchain.transaction import Transaction
from border.blockchain.wallet import BorderWallet


class TestBlockReward:
    def test_initial_reward(self):
        assert block_reward(0) == 50.0

    def test_first_halving(self):
        assert block_reward(HALVING_INTERVAL) == 25.0

    def test_second_halving(self):
        assert block_reward(HALVING_INTERVAL * 2) == 12.5

    def test_satoshi_floor(self):
        # Once reward falls below 1 satoshi it should return 0
        assert block_reward(HALVING_INTERVAL * 100) == 0.0

    def test_never_negative(self):
        for h in [0, 1, 100, 210_000, 420_000, 10_000_000]:
            assert block_reward(h) >= 0.0


class TestCumulativeSupply:
    def test_zero_blocks(self):
        assert cumulative_supply(0) == INITIAL_BLOCK_REWARD

    def test_caps_at_max(self):
        assert cumulative_supply(100_000_000) <= MAX_SUPPLY

    def test_monotonically_increasing(self):
        prev = 0.0
        for h in range(0, 500_000, 50_000):
            curr = cumulative_supply(h)
            assert curr >= prev
            prev = curr


class TestValidateFee:
    def test_coinbase_exempt(self):
        tx = Transaction.coinbase(to_address="addr", reward=50.0)
        assert validate_fee(tx) is True

    def test_below_floor_rejected(self):
        w = BorderWallet.create()
        tx = Transaction.create(w.address, "other", 1.0, w.public_key_b64, fee=0.0)
        assert validate_fee(tx) is False

    def test_at_floor_accepted(self):
        w = BorderWallet.create()
        tx = Transaction.create(w.address, "other", 1.0, w.public_key_b64, fee=MIN_FEE_PER_TX)
        assert validate_fee(tx) is True

    def test_above_floor_accepted(self):
        w = BorderWallet.create()
        tx = Transaction.create(w.address, "other", 1.0, w.public_key_b64, fee=0.01)
        assert validate_fee(tx) is True


class TestSupplyHeadroom:
    def test_full_headroom(self):
        assert supply_headroom(0.0) == MAX_SUPPLY

    def test_no_headroom_at_cap(self):
        assert supply_headroom(MAX_SUPPLY) == 0.0

    def test_clamped_to_zero(self):
        assert supply_headroom(MAX_SUPPLY + 1) == 0.0


class TestDifficultyAdjustment:
    def test_retarget_clamp_down(self):
        """Blocks arriving 4x faster than target -> difficulty halved (clamped at /4)."""
        d = calculate_next_difficulty(
            100 * 1024 * 1024,
            0.0,
            DIFFICULTY_ADJUSTMENT_INTERVAL * TARGET_BLOCK_TIME / 4,
        )
        expected = 100 * 1024 * 1024 // 4
        assert d == expected

    def test_retarget_clamp_up(self):
        """Blocks arriving 4x slower than target -> difficulty quadrupled (clamped at x4)."""
        d = calculate_next_difficulty(
            100 * 1024 * 1024,
            0.0,
            DIFFICULTY_ADJUSTMENT_INTERVAL * TARGET_BLOCK_TIME * 4,
        )
        expected = 100 * 1024 * 1024 * 4
        assert d == expected

    def test_floor_enforced(self):
        d = calculate_next_difficulty(MIN_DIFFICULTY, 0.0, 1.0)  # effectively zero time
        assert d == MIN_DIFFICULTY

    def test_ceiling_enforced(self):
        d = calculate_next_difficulty(MAX_DIFFICULTY, 0.0, 1e10)
        assert d == MAX_DIFFICULTY

    def test_accurate_retarget(self):
        """Blocks took exactly 2x longer -> difficulty doubles."""
        d = calculate_next_difficulty(
            100 * 1024 * 1024,
            0.0,
            DIFFICULTY_ADJUSTMENT_INTERVAL * TARGET_BLOCK_TIME * 2,
        )
        assert d == 200 * 1024 * 1024
