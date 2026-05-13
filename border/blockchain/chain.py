"""
BorderCoin Blockchain — Chain
Proof of Bandwidth + Proof of Compute + Proof of Storage.
"""

from __future__ import annotations

import json
from .store import ChainStore
import logging
import time
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from . import block as _block_module
from .block import Block, BandwidthProof, ComputeProofRecord, StorageProofRecord, BLOCK_REWARD, BC_PER_GB, BC_PER_COMPUTE_HOUR, BC_PER_GB_PER_DAY
from .economics import (
    validate_fee, fee_sort_key, MAX_SUPPLY,
    calculate_next_difficulty, DIFFICULTY_ADJUSTMENT_INTERVAL, MIN_DIFFICULTY,
)
from .transaction import Transaction

logger = logging.getLogger("border.blockchain")


class BorderChain:
    def __init__(self, persist_path: Optional[str] = None):
        self._chain: List[Block] = [Block.genesis()]
        self._mempool: List[Transaction] = []
        self._pending_proofs: List[BandwidthProof] = []
        self._pending_compute_proofs: List[ComputeProofRecord] = []
        self._pending_storage_proofs: List[StorageProofRecord] = []
        self._spent_receipts: Set[str] = set()
        self._spent_compute_proofs: Set[str] = set()
        self._spent_storage_proofs: Set[str] = set()
        self._persist_path = Path(persist_path) if persist_path else None
        # SQLite store — used when persist_path ends with .db (or is a dir)
        self._store: Optional[ChainStore] = None
        if persist_path:
            p = Path(persist_path)
            if p.suffix == ".json":
                pass  # legacy JSON path; _store stays None
            else:
                db_path = p if p.suffix == ".db" else p.with_suffix(".db")
                self._store = ChainStore(db_path)
        # Tracks pending outflows per address: address -> total BC reserved in mempool
        self._mempool_reserved: Dict[str, float] = {}
        # Balance cache: address -> confirmed balance (updated incrementally)
        self._balance_cache: Dict[str, float] = {}
        # Staking: address -> {"amount": float, "role": str, "locked_at": float}
        self._stakes: Dict[str, dict] = {}
        # Slash log: list of {"address", "amount", "reason", "timestamp"}
        self._slash_log: List[dict] = []

        if self._store is not None:
            # SQLite persistence path
            if self._store.count() > 0:
                self._load()   # warm start from SQLite
            else:
                self._rebuild_balance_cache()
                self._store.append_block(self._chain[0].to_dict())   # persist genesis
                logger.info("[Chain] Initialized with genesis block (SQLite)")
        elif self._persist_path and self._persist_path.exists():
            # Legacy JSON path
            self._load()
        else:
            self._rebuild_balance_cache()
            logger.info("[Chain] Initialized with genesis block")

    # -------------------------------------------------------------------
    # Difficulty targeting
    # -------------------------------------------------------------------

    def _compute_next_difficulty(self) -> int:
        """
        Return the difficulty (min-bytes threshold) for the next block to be mined.

        Retargets every DIFFICULTY_ADJUSTMENT_INTERVAL blocks using a Bitcoin-style
        calculation: scale current difficulty by (actual_time / expected_time),
        clamped to 4x in either direction.
        """
        next_index = len(self._chain)   # index of the block we're about to produce

        # Carry forward previous difficulty between retarget windows
        if next_index == 0 or next_index % DIFFICULTY_ADJUSTMENT_INTERVAL != 0:
            return self.latest_block.difficulty

        # Boundary: compute new difficulty from the last interval
        start_block = self._chain[next_index - DIFFICULTY_ADJUSTMENT_INTERVAL]
        end_block   = self.latest_block
        new_diff = calculate_next_difficulty(
            current_difficulty  = end_block.difficulty,
            interval_start_ts   = start_block.timestamp,
            interval_end_ts     = end_block.timestamp,
        )
        logger.info(
            f"[Chain] Difficulty retarget at #{next_index}: "
            f"{end_block.difficulty // (1024*1024)}MB -> {new_diff // (1024*1024)}MB"
        )
        return new_diff

    # -------------------------------------------------------------------
    # Block production
    # -------------------------------------------------------------------

    def create_block(self, miner_address: str,
                     proofs: Optional[List[BandwidthProof]] = None,
                     transactions: Optional[List[Transaction]] = None) -> Optional[Block]:
        next_difficulty = self._compute_next_difficulty()
        available_proofs = proofs or self._pending_proofs
        valid_proofs = [p for p in available_proofs if p.receipt_id not in self._spent_receipts]
        total_bytes = sum(p.bytes_forwarded for p in valid_proofs)

        if total_bytes < next_difficulty:
            logger.info(
                f"[Chain] Not enough bandwidth: "
                f"{total_bytes/(1024*1024):.1f}MB / {next_difficulty/(1024*1024):.0f}MB required"
            )
            return None

        valid_compute  = [p for p in self._pending_compute_proofs if p.proof_id not in self._spent_compute_proofs]
        valid_storage  = [p for p in self._pending_storage_proofs if p.proof_id not in self._spent_storage_proofs]
        # Fee market: reject below floor, then sort highest-fee first
        pending_txs = [
            tx for tx in (transactions or self._mempool)
            if tx.verify() and validate_fee(tx)
        ]
        pending_txs.sort(key=fee_sort_key)

        block = Block(
            index=len(self._chain), timestamp=time.time(),
            previous_hash=self.latest_block.block_hash,
            miner_address=miner_address,
            bandwidth_proofs=valid_proofs,
            compute_proofs=valid_compute,
            storage_proofs=valid_storage,
            transactions=pending_txs,
            difficulty=next_difficulty,
        )
        block.finalize(current_supply=self.total_supply)
        return block

    def add_block(self, block: Block) -> Tuple[bool, str]:
        valid, reason = self._validate_block(block)
        if not valid:
            return False, reason

        self._chain.append(block)

        for p in block.bandwidth_proofs:
            self._spent_receipts.add(p.receipt_id)
        for p in block.compute_proofs:
            self._spent_compute_proofs.add(p.proof_id)
        for p in block.storage_proofs:
            self._spent_storage_proofs.add(p.proof_id)

        spent_bw = {p.receipt_id for p in block.bandwidth_proofs}
        self._pending_proofs = [p for p in self._pending_proofs if p.receipt_id not in spent_bw]

        spent_cp = {p.proof_id for p in block.compute_proofs}
        self._pending_compute_proofs = [p for p in self._pending_compute_proofs if p.proof_id not in spent_cp]

        spent_sp = {p.proof_id for p in block.storage_proofs}
        self._pending_storage_proofs = [p for p in self._pending_storage_proofs if p.proof_id not in spent_sp]

        confirmed_ids = {tx.tx_id for tx in block.transactions}
        # Release mempool reservations for confirmed transactions
        for tx in block.transactions:
            if tx.from_address in self._mempool_reserved:
                cost = round(tx.amount + tx.fee, 8)
                self._mempool_reserved[tx.from_address] = max(
                    0.0, round(self._mempool_reserved[tx.from_address] - cost, 8)
                )
                if self._mempool_reserved[tx.from_address] == 0.0:
                    del self._mempool_reserved[tx.from_address]
        self._mempool = [tx for tx in self._mempool if tx.tx_id not in confirmed_ids]

        # Update balance cache for confirmed transactions
        for tx in block.transactions:
            self._apply_tx_to_cache(tx, sign=1)

        logger.info(
            f"[Chain] Block #{block.index} | "
            f"bw={len(block.bandwidth_proofs)} | "
            f"compute={len(block.compute_proofs)} | "
            f"storage={len(block.storage_proofs)} | "
            f"+{block.total_bandwidth_pc:.4f}+{block.total_compute_bc:.4f}+{block.total_storage_bc:.4f} BC"
        )

        if self._persist_path:
            self._save()
        return True, "accepted"

    # -------------------------------------------------------------------
    # Proof / transaction intake
    # -------------------------------------------------------------------

    def add_proof(self, proof: BandwidthProof) -> bool:
        if proof.receipt_id in self._spent_receipts:
            return False
        if any(p.receipt_id == proof.receipt_id for p in self._pending_proofs):
            return False
        self._pending_proofs.append(proof)
        return True

    def add_compute_proof(self, proof: ComputeProofRecord) -> bool:
        """Accept a compute proof. Rejects if worker has not staked the minimum."""
        if proof.proof_id in self._spent_compute_proofs:
            return False
        if any(p.proof_id == proof.proof_id for p in self._pending_compute_proofs):
            return False
        if not self.has_minimum_stake(proof.worker_address, "compute"):
            logger.warning(
                f"[Chain] Compute proof rejected — insufficient stake: "
                f"{proof.worker_address[:20]}... "
                f"staked={self.get_staked(proof.worker_address):.2f} BC"
            )
            return False
        self._pending_compute_proofs.append(proof)
        logger.info(f"[Chain] Compute proof queued: {proof.proof_id} +{proof.compute_reward_bc:.6f} BC")
        return True

    def add_storage_proof(self, proof: StorageProofRecord) -> bool:
        if proof.proof_id in self._spent_storage_proofs:
            return False
        if any(p.proof_id == proof.proof_id for p in self._pending_storage_proofs):
            return False
        if not proof.verify_signature():
            logger.warning(f"[Chain] Storage proof rejected — invalid signature: {proof.proof_id}")
            return False
        self._pending_storage_proofs.append(proof)
        logger.info(f"[Chain] Storage proof queued: {proof.proof_id} +{proof.reward_bc:.8f} BC -> {proof.node_address[:16]}...")
        return True

    def add_transaction(self, tx: Transaction) -> bool:
        if not tx.verify():
            return False
        if not validate_fee(tx):
            logger.warning(f"[Chain] TX {tx.tx_id} fee {tx.fee} below minimum")
            return False
        # Double-spend protection: confirmed balance minus already-pending outflows
        confirmed   = self.get_balance(tx.from_address)
        pending_out = self._mempool_reserved.get(tx.from_address, 0.0)
        available   = round(confirmed - pending_out, 8)
        cost        = round(tx.amount + tx.fee, 8)
        if available < cost:
            logger.warning(
                f"[Chain] TX {tx.tx_id} insufficient funds: "
                f"available={available} cost={cost} "
                f"(confirmed={confirmed} pending_out={pending_out})"
            )
            return False
        self._mempool.append(tx)
        self._mempool_reserved[tx.from_address] = round(pending_out + cost, 8)
        return True

    # -------------------------------------------------------------------
    # Validation
    # -------------------------------------------------------------------

    def _validate_block(self, block: Block) -> Tuple[bool, str]:
        if block.index != len(self._chain):
            return False, f"wrong index: expected {len(self._chain)}, got {block.index}"
        if block.previous_hash != self.latest_block.block_hash:
            return False, "previous_hash mismatch"
        # Verify the difficulty stamped on this block matches what we compute
        expected_diff = self._compute_next_difficulty()
        if block.difficulty != expected_diff:
            return False, (
                f"difficulty mismatch: block has {block.difficulty}, "
                f"expected {expected_diff}"
            )
        if block.compute_hash() != block.block_hash:
            return False, "hash invalid"
        if block.total_bytes < block.difficulty:
            return False, (
                f"insufficient bandwidth: {block.total_bytes} bytes "
                f"< difficulty {block.difficulty}"
            )
        for proof in block.bandwidth_proofs:
            if proof.receipt_id in self._spent_receipts:
                return False, f"double-spend: receipt {proof.receipt_id}"
        for proof in block.storage_proofs:
            if not proof.verify_signature():
                return False, f"storage proof signature invalid: {proof.proof_id}"
        for tx in block.transactions:
            if tx.from_address == Transaction.COINBASE_ADDRESS:
                continue
            if not tx.verify():
                return False, f"invalid tx: {tx.tx_id}"
        return True, "valid"

    def validate_chain(self) -> Tuple[bool, str]:
        """Full chain re-validation including per-block difficulty checks."""
        for i in range(1, len(self._chain)):
            block = self._chain[i]
            prev  = self._chain[i - 1]
            if block.previous_hash != prev.block_hash:
                return False, f"Block #{i}: broken link"
            if block.compute_hash() != block.block_hash:
                return False, f"Block #{i}: hash invalid"
            if block.total_bytes < block.difficulty:
                return False, f"Block #{i}: insufficient bandwidth for difficulty"
            # Recompute expected difficulty at this index
            if i % DIFFICULTY_ADJUSTMENT_INTERVAL == 0:
                start_block = self._chain[i - DIFFICULTY_ADJUSTMENT_INTERVAL]
                expected = calculate_next_difficulty(
                    current_difficulty=prev.difficulty,
                    interval_start_ts=start_block.timestamp,
                    interval_end_ts=prev.timestamp,
                )
            else:
                expected = prev.difficulty
            if block.difficulty != expected:
                return False, f"Block #{i}: difficulty mismatch (got {block.difficulty}, expected {expected})"
        return True, "chain valid"

    # -------------------------------------------------------------------
    # Balance cache
    # -------------------------------------------------------------------

    def get_balance(self, address: str) -> float:
        """O(1) balance lookup via incrementally-maintained cache."""
        return self._balance_cache.get(address, 0.0)

    def _apply_tx_to_cache(self, tx, sign: int = 1) -> None:
        """Update balance cache for one transaction.  sign=+1 to apply, -1 to undo."""
        if tx.to_address:
            self._balance_cache[tx.to_address] = round(
                self._balance_cache.get(tx.to_address, 0.0) + sign * tx.amount, 8
            )
        if tx.from_address and tx.from_address != Transaction.COINBASE_ADDRESS:
            self._balance_cache[tx.from_address] = round(
                self._balance_cache.get(tx.from_address, 0.0)
                - sign * (tx.amount + tx.fee), 8
            )

    def _rebuild_balance_cache(self) -> None:
        """Rebuild the full balance cache from scratch (used on load / reorg)."""
        self._balance_cache = {}
        for block in self._chain:
            for tx in block.transactions:
                self._apply_tx_to_cache(tx, sign=1)

    # -------------------------------------------------------------------
    # Chain replacement / reorg
    # -------------------------------------------------------------------

    def replace_chain(self, new_chain: List[Block]) -> bool:
        if len(new_chain) <= len(self._chain):
            return False
        temp = BorderChain()
        temp._chain = new_chain
        valid, _ = temp.validate_chain()
        if not valid:
            return False
        self._chain = new_chain
        self._rebuild_spent_set()
        return True

    def _rebuild_spent_set(self) -> None:
        self._spent_receipts = set()
        self._spent_compute_proofs = set()
        self._spent_storage_proofs = set()
        for block in self._chain:
            for p in block.bandwidth_proofs:
                self._spent_receipts.add(p.receipt_id)
            for p in block.compute_proofs:
                self._spent_compute_proofs.add(p.proof_id)
            for p in block.storage_proofs:
                self._spent_storage_proofs.add(p.proof_id)
        self._rebuild_balance_cache()

    # -------------------------------------------------------------------
    # Properties
    # -------------------------------------------------------------------

    @property
    def latest_block(self) -> Block:
        return self._chain[-1]

    @property
    def height(self) -> int:
        return len(self._chain) - 1

    @property
    def total_supply(self) -> float:
        return round(sum(
            tx.amount for block in self._chain
            for tx in block.transactions
            if tx.from_address == Transaction.COINBASE_ADDRESS
        ), 8)

    @property
    def current_difficulty(self) -> int:
        """Difficulty (min-bytes threshold) that will be used for the NEXT block."""
        return self._compute_next_difficulty()

    @property
    def pending_bandwidth_mb(self) -> float:
        return sum(p.bytes_forwarded for p in self._pending_proofs) / (1024 * 1024)

    @property
    def pending_compute_bc(self) -> float:
        return sum(p.compute_reward_bc for p in self._pending_compute_proofs)

    @property
    def pending_storage_bc(self) -> float:
        return sum(p.reward_bc for p in self._pending_storage_proofs)

    @property
    def stats(self) -> dict:
        return {
            "height":                     self.height,
            "total_supply":               self.total_supply,
            "pending_proofs":             len(self._pending_proofs),
            "pending_bandwidth_mb":       round(self.pending_bandwidth_mb, 2),
            "pending_compute_proofs":     len(self._pending_compute_proofs),
            "pending_compute_bc":         round(self.pending_compute_bc, 6),
            "pending_storage_proofs":     len(self._pending_storage_proofs),
            "pending_storage_bc":         round(self.pending_storage_bc, 8),
            "mempool_size":               len(self._mempool),
            "spent_receipts":             len(self._spent_receipts),
            "spent_compute_proofs":       len(self._spent_compute_proofs),
            "spent_storage_proofs":       len(self._spent_storage_proofs),
            "block_reward_bc":            BLOCK_REWARD,
            "max_supply":                 MAX_SUPPLY,
            "bc_per_gb":                  BC_PER_GB,
            "bc_per_compute_hour":        BC_PER_COMPUTE_HOUR,
            "bc_per_gb_per_day":          BC_PER_GB_PER_DAY,
            "current_difficulty_mb":      round(self.current_difficulty / (1024*1024), 2),
            "latest_block_difficulty_mb": round(self.latest_block.difficulty / (1024*1024), 2),
            "total_staked_bc":            self.total_staked,
            "active_stakers":             len(self._stakes),
            "slash_events":               len(self._slash_log),
        }


    # -------------------------------------------------------------------
    # Staking / slashing
    # -------------------------------------------------------------------

    # Minimum BC required per role
    STAKE_MINIMUMS = {
        "relay":   1.0,
        "compute": 1.0,
        "storage": 2.0,
        "infer":   5.0,
        "render":  10.0,
    }

    def stake(self, address: str, amount: float, role: str):
        """Lock BC as collateral for a service role."""
        role = role.lower()
        minimum = self.STAKE_MINIMUMS.get(role)
        if minimum is None:
            return False, f"Unknown role: {role}"
        if amount < minimum:
            return False, f"Stake too low for {role}: need >={minimum} BC, got {amount}"
        available = round(self.get_balance(address) - self.get_staked(address), 8)
        if available < amount:
            return False, f"Insufficient available balance: have {available:.8f} BC, need {amount:.8f}"
        existing = self._stakes.get(address)
        if existing:
            new_amount = round(existing["amount"] + amount, 8)
            self._stakes[address] = {"amount": new_amount, "role": role, "locked_at": existing["locked_at"]}
        else:
            self._stakes[address] = {"amount": round(amount, 8), "role": role, "locked_at": time.time()}
        logger.info(f"[Chain] Staked {amount:.8f} BC for {address[:20]}... role={role}")
        return True, "staked"

    def unstake(self, address: str):
        """Release a node's entire stake back to spendable balance."""
        if address not in self._stakes:
            return False, "No stake found"
        entry = self._stakes.pop(address)
        logger.info(f"[Chain] Unstaked {entry['amount']:.8f} BC for {address[:20]}...")
        return True, f"released {entry['amount']:.8f} BC"

    def slash(self, address: str, amount: float, reason: str = ""):
        """Slash a portion of a node's stake as a penalty for misbehavior."""
        entry = self._stakes.get(address)
        if not entry:
            return False, "Address has no stake to slash"
        slash_amount = round(min(amount, entry["amount"]), 8)
        entry["amount"] = round(entry["amount"] - slash_amount, 8)
        if entry["amount"] <= 0.0:
            del self._stakes[address]
            logger.warning(f"[Chain] FULL SLASH {address[:20]}... -{slash_amount:.8f} BC ({reason})")
        else:
            self._stakes[address] = entry
            logger.warning(
                f"[Chain] SLASH {address[:20]}... -{slash_amount:.8f} BC ({reason}) "
                f"remaining={entry['amount']:.8f}"
            )
        self._slash_log.append({
            "address":   address,
            "amount":    slash_amount,
            "reason":    reason,
            "timestamp": time.time(),
        })
        return True, f"slashed {slash_amount:.8f} BC"

    def get_staked(self, address: str) -> float:
        return self._stakes.get(address, {}).get("amount", 0.0)

    def get_stake_info(self, address: str):
        return self._stakes.get(address)

    def has_minimum_stake(self, address: str, role: str) -> bool:
        minimum = self.STAKE_MINIMUMS.get(role.lower(), 0.0)
        return self.get_staked(address) >= minimum

    @property
    def slash_log(self):
        return list(self._slash_log)

    @property
    def total_staked(self) -> float:
        return round(sum(e["amount"] for e in self._stakes.values()), 8)

    # -------------------------------------------------------------------
    # Persistence
    # -------------------------------------------------------------------

    def _save(self) -> None:
        if self._store is not None:
            # SQLite: append only the latest block (O(1))
            if self._chain:
                self._store.append_block(self._chain[-1].to_dict())
        elif self._persist_path:
            # Legacy JSON fallback
            self._persist_path.write_text(
                json.dumps({"chain": [b.to_dict() for b in self._chain]})
            )

    def _load(self) -> None:
        if self._store is not None:
            blocks_raw = self._store.load_all()
            if blocks_raw:
                self._chain = [Block.from_dict(b) for b in blocks_raw]
                self._rebuild_spent_set()
                logger.info(f"[Chain] Loaded {len(self._chain)} blocks from SQLite")
        elif self._persist_path and self._persist_path.exists():
            data = json.loads(self._persist_path.read_text())
            self._chain = [Block.from_dict(b) for b in data["chain"]]
            self._rebuild_spent_set()
            logger.info(f"[Chain] Loaded {len(self._chain)} blocks from JSON")

    # -------------------------------------------------------------------
    # P2P helpers
    # -------------------------------------------------------------------

    def block_hash_at(self, index: int) -> Optional[str]:
        """Return block_hash at a given index, or None if out of range."""
        if 0 <= index < len(self._chain):
            return self._chain[index].block_hash
        return None

    def blocks_range(self, start: int, end: int) -> List[Block]:
        """Return blocks[start:end+1] clamped to chain length."""
        hi = min(end + 1, len(self._chain))
        return self._chain[max(0, start):hi]

    def __len__(self) -> int:
        return len(self._chain)
