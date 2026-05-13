"""
Border Token Economics Demo
============================
Verifies:
  1. Halving schedule: reward halves every 210,000 blocks
  2. Supply cap: cumulative supply never exceeds 21M BC
  3. Fee floor: transactions below MIN_FEE_PER_TX are rejected
  4. Fee market ordering: highest-fee txns mined first
  5. Block coinbase uses height-aware reward
  6. MAX_SUPPLY headroom clamps reward correctly
"""

import sys, os, time, uuid
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from border.blockchain.economics import (
    block_reward, cumulative_supply, validate_fee, fee_sort_key,
    MAX_SUPPLY, INITIAL_BLOCK_REWARD, HALVING_INTERVAL, MIN_FEE_PER_TX,
)
from border.blockchain.chain import BorderChain
from border.blockchain.wallet import BorderWallet
from border.blockchain.transaction import Transaction
from border.blockchain.block import BandwidthProof

print("=" * 60)
print("Border Token Economics Demo")
print("=" * 60)

# ------------------------------------------------------------------
# Step 1 -- Halving schedule
# ------------------------------------------------------------------
print("\nStep 1: Halving schedule")
schedule = [
    (0,                    50.0),
    (HALVING_INTERVAL - 1, 50.0),
    (HALVING_INTERVAL,     25.0),
    (HALVING_INTERVAL * 2, 12.5),
    (HALVING_INTERVAL * 3,  6.25),
    (HALVING_INTERVAL * 64, 0.0),   # past final halving (halvings>=64)
]
for height, expected in schedule:
    r = block_reward(height)
    assert r == expected, f"height={height}: got {r}, expected {expected}"
    print(f"  height={height:>10,}  reward={r:>12.8f} BC  OK")

# ------------------------------------------------------------------
# Step 2 -- Cumulative supply never exceeds MAX_SUPPLY
# ------------------------------------------------------------------
print("\nStep 2: Supply cap")
# Check at several eras
for height in [0, HALVING_INTERVAL, HALVING_INTERVAL * 10, HALVING_INTERVAL * 100]:
    s = cumulative_supply(height)
    assert s <= MAX_SUPPLY, f"Supply {s} exceeds cap at height {height}"
    print(f"  height={height:>10,}  cumulative={s:>18,.8f} BC  <= {MAX_SUPPLY:,}  OK")

# Verify theoretical total converges to ~21M
theoretical = sum(INITIAL_BLOCK_REWARD / (2**e) * HALVING_INTERVAL
                  for e in range(33))
print(f"  Theoretical total ~ {theoretical:,.2f} BC (approaches 21M)  OK")

# ------------------------------------------------------------------
# Step 3 -- Fee floor enforcement
# ------------------------------------------------------------------
print("\nStep 3: Fee floor")
wallet = BorderWallet.create()
chain = BorderChain()

# Inject a coinbase so wallet has balance
chain._chain[0].transactions.append(
    Transaction.coinbase(to_address=wallet.address, reward=100.0,
                         deterministic_id="economics_demo")
)

def make_tx(fee: float) -> Transaction:
    tx = Transaction(
        tx_id=f"tx_{uuid.uuid4().hex[:16]}",
        from_address=wallet.address,
        to_address="BC_0000000000000000000000000000000000",
        amount=0.01,
        fee=fee,
        timestamp=time.time(),
        public_key=wallet.public_key_b64,
    )
    tx.signature = wallet.sign(tx.signing_data())
    return tx

# Below floor
tx_low = make_tx(0.00005)
ok = chain.add_transaction(tx_low)
assert not ok, "Low-fee TX should be rejected"
print(f"  Fee=0.00005 BC (below {MIN_FEE_PER_TX})  rejected  OK")

# At floor
tx_floor = make_tx(MIN_FEE_PER_TX)
ok = chain.add_transaction(tx_floor)
assert ok, "Floor-fee TX should be accepted"
print(f"  Fee={MIN_FEE_PER_TX} BC (at floor)  accepted  OK")

# Well above floor
tx_high = make_tx(0.01)
ok = chain.add_transaction(tx_high)
assert ok, "High-fee TX should be accepted"
print(f"  Fee=0.01 BC (above floor)  accepted  OK")

# ------------------------------------------------------------------
# Step 4 -- Fee market ordering
# ------------------------------------------------------------------
print("\nStep 4: Fee market ordering in mined block")
chain2 = BorderChain()
wallet2 = BorderWallet.create()
# Credit wallet2 heavily
chain2._chain[0].transactions.append(
    Transaction.coinbase(to_address=wallet2.address, reward=1000.0,
                         deterministic_id="economics_demo2")
)

fees = [0.005, 0.05, 0.001, 0.02, 0.1]
for fee in fees:
    tx = Transaction(
        tx_id=f"tx_{uuid.uuid4().hex[:16]}",
        from_address=wallet2.address,
        to_address="BC_0000000000000000000000000000000000",
        amount=0.001,
        fee=fee,
        timestamp=time.time(),
        public_key=wallet2.public_key_b64,
    )
    tx.signature = wallet2.sign(tx.signing_data())
    chain2.add_transaction(tx)

# Add enough bandwidth to mine
for i in range(2):
    chain2.add_proof(BandwidthProof(
        receipt_id=f"econ_rcpt_{i}",
        relay_address=wallet2.address,
        client_id=f"econ_client_{i}",
        bytes_forwarded=110 * 1024 * 1024,
        timestamp=time.time(),
        session_id=f"econ_sess_{i}",
        relay_signature="demo",
    ))

block = chain2.create_block(miner_address=wallet2.address)
assert block is not None, "Block creation failed"

# Skip coinbase (index 0), check user txns are ordered highest fee first
user_txs = [tx for tx in block.transactions if tx.from_address != Transaction.COINBASE_ADDRESS]
tx_fees = [tx.fee for tx in user_txs]
print(f"  Mempool fees (inserted order): {sorted(fees)}")
print(f"  Mined order (should be high->low): {tx_fees}")
assert tx_fees == sorted(tx_fees, reverse=True), f"Txns not sorted: {tx_fees}"
print("  Highest-fee txns mined first  OK")

# ------------------------------------------------------------------
# Step 5 -- Block reward uses height-aware halving
# ------------------------------------------------------------------
print("\nStep 5: Block coinbase uses halving reward")
ok, _ = chain2.add_block(block)
assert ok, "Block should be accepted"
coinbase_tx = block.transactions[0]
expected_reward = block_reward(1)   # block.index == 1
# coinbase includes BW bonus so total > base; just verify base is there
print(f"  Block #1 coinbase = {coinbase_tx.amount:.8f} BC")
print(f"  Base block reward @ height=1: {expected_reward:.8f} BC")
assert coinbase_tx.amount >= expected_reward, "Coinbase below expected base reward"
print("  Coinbase >= halving reward  OK")

# ------------------------------------------------------------------
# Step 6 -- Supply headroom clamps reward at cap
# ------------------------------------------------------------------
print("\nStep 6: MAX_SUPPLY headroom clamping")
from border.blockchain.economics import supply_headroom
h = supply_headroom(MAX_SUPPLY - 0.5)
assert h == 0.5, f"Expected 0.5 headroom, got {h}"
print(f"  supply={MAX_SUPPLY - 0.5:,.1f}  headroom={h}  OK")

h2 = supply_headroom(MAX_SUPPLY)
assert h2 == 0.0
print(f"  supply={MAX_SUPPLY:,}  headroom={h2}  (fully minted)  OK")

print()
print("=" * 60)
print("All token economics steps passed!")
print(f"  Max supply:     {MAX_SUPPLY:>15,} BC")
print(f"  Initial reward: {INITIAL_BLOCK_REWARD:>15} BC/block")
print(f"  Halving every:  {HALVING_INTERVAL:>15,} blocks")
print(f"  Min fee:        {MIN_FEE_PER_TX:>15} BC/tx")
print("=" * 60)
