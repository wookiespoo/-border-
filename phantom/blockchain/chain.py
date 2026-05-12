"""
BorderCoin Blockchain
The chain itself. Validation. Consensus. Balances.

Proof of Bandwidth consensus:
- Blocks are valid when they contain verified bandwidth receipts
- No wasteful hashing — the "mining" IS giving people internet access
- Longest valid chain wins (same as Bitcoin)
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from .block import Block, BandwidthProof, MIN_BYTES_PER_BLOCK, BLOCK_REWARD, BC_PER_GB
from .transaction import Transaction

logger = logging.getLogger("border.blockchain")


class BorderChain:
    """
    The BorderCoin blockchain.

    Proof of Bandwidth: blocks must contain >= 100MB of verified
    bandwidth receipts. No receipt can be used twice.

    Usage:
        chain = BorderChain()
        block = chain.create_block(miner_address="BC_...", proofs=[...])
        accepted = chain.add_block(block)
    """

    def __init__(self, persist_path: Optional[str] = None):
        self._chain: List[Block] = [Block.genesis()]
        self._mempool: List[Transaction] = []
        self._pending_proofs: List[BandwidthProof] = []
        self._spent_receipts: Set[str] = set()
        self._persist_path = Path(persist_path) if persist_path else None

        if self._persist_path and self._persist_path.exists():
            self._load()
        else:
            logger.info("[Chain] Initialized with genesis block")

    # ------------------------------------------------------------------
    # Block production
    # ------------------------------------------------------------------

    def create_block(
        self,
        miner_address: str,
        proofs: Optional[List[BandwidthProof]] = None,
        transactions: Optional[List[Transaction]] = None,
    ) -> Optional[Block]:
        """
        Create a new block candidate.
        Returns None if not enough bandwidth proofs accumulated yet.
        """
        available_proofs = proofs or self._pending_proofs
        valid_proofs = [
            p for p in available_proofs
            if p.receipt_id not in self._spent_receipts
        ]

        total_bytes = sum(p.bytes_forwarded for p in valid_proofs)

        if total_bytes < MIN_BYTES_PER_BLOCK:
            mb_needed = (MIN_BYTES_PER_BLOCK - total_bytes) / (1024 * 1024)
            logger.info(
                f"[Chain] Not enough bandwidth yet: "
                f"{total_bytes / (1024*1024):.1f}MB / "
                f"{MIN_BYTES_PER_BLOCK / (1024*1024):.0f}MB needed "
                f"({mb_needed:.1f}MB remaining)"
            )
            return None

        pending_txs = [
            tx for tx in (transactions or self._mempool)
            if tx.verify()
        ]

        block = Block(
            index=len(self._chain),
            timestamp=time.time(),
            previous_hash=self.latest_block.block_hash,
            miner_address=miner_address,
            bandwidth_proofs=valid_proofs,
            transactions=pending_txs,
        )
        block.finalize()
        return block

    def add_block(self, block: Block) -> Tuple[bool, str]:
        """
        Validate and add a block to the chain.
        Returns (success, reason).
        """
        valid, reason = self._validate_block(block)
        if not valid:
            logger.warning(f"[Chain] Rejected block #{block.index}: {reason}")
            return False, reason

        self._chain.append(block)

        # Mark receipts as spent
        for proof in block.bandwidth_proofs:
            self._spent_receipts.add(proof.receipt_id)

        # Remove from pending
        spent_ids = {p.receipt_id for p in block.bandwidth_proofs}
        self._pending_proofs = [p for p in self._pending_proofs if p.receipt_id not in spent_ids]

        # Clear confirmed transactions from mempool
        confirmed_ids = {tx.tx_id for tx in block.transactions}
        self._mempool = [tx for tx in self._mempool if tx.tx_id not in confirmed_ids]

        logger.info(
            f"[Chain] ✓ Block #{block.index} accepted | "
            f"{len(block.bandwidth_proofs)} proofs | "
            f"{block.total_bytes / (1024*1024):.1f}MB | "
            f"{block.total_bandwidth_pc:.4f} PC earned by {block.miner_address[:20]}..."
        )

        if self._persist_path:
            self._save()

        return True, "accepted"

    def add_proof(self, proof: BandwidthProof) -> bool:
        """Add a bandwidth proof to the pending pool."""
        if proof.receipt_id in self._spent_receipts:
            return False
        if any(p.receipt_id == proof.receipt_id for p in self._pending_proofs):
            return False
        self._pending_proofs.append(proof)
        return True

    def add_transaction(self, tx: Transaction) -> bool:
        """Add a transaction to the mempool."""
        if not tx.verify():
            return False
        balance = self.get_balance(tx.from_address)
        if balance < tx.amount + tx.fee:
            return False
        self._mempool.append(tx)
        return True

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate_block(self, block: Block) -> Tuple[bool, str]:
        if block.index != len(self._chain):
            return False, f"wrong index: expected {len(self._chain)}, got {block.index}"

        if block.previous_hash != self.latest_block.block_hash:
            return False, "previous_hash mismatch"

        if block.compute_hash() != block.block_hash:
            return False, "hash invalid"

        if block.total_bytes < MIN_BYTES_PER_BLOCK:
            return False, f"insufficient bandwidth: {block.total_bytes} < {MIN_BYTES_PER_BLOCK}"

        for proof in block.bandwidth_proofs:
            if proof.receipt_id in self._spent_receipts:
                return False, f"double-spend: receipt {proof.receipt_id}"

        for tx in block.transactions:
            if tx.from_address == Transaction.COINBASE_ADDRESS:
                continue
            if not tx.verify():
                return False, f"invalid tx signature: {tx.tx_id}"

        return True, "valid"

    def validate_chain(self) -> Tuple[bool, str]:
        """Validate the entire chain from genesis."""
        for i in range(1, len(self._chain)):
            block = self._chain[i]
            prev = self._chain[i - 1]

            if block.previous_hash != prev.block_hash:
                return False, f"Block #{i}: broken link"
            if block.compute_hash() != block.block_hash:
                return False, f"Block #{i}: hash invalid"
            if block.total_bytes < MIN_BYTES_PER_BLOCK and i > 0:
                return False, f"Block #{i}: insufficient bandwidth"

        return True, "chain valid"

    # ------------------------------------------------------------------
    # Balances
    # ------------------------------------------------------------------

    def get_balance(self, address: str) -> float:
        """Calculate an address's current balance from the chain."""
        balance = 0.0
        for block in self._chain:
            for tx in block.transactions:
                if tx.to_address == address:
                    balance += tx.amount
                if tx.from_address == address:
                    balance -= (tx.amount + tx.fee)
        return round(balance, 8)

    def get_all_balances(self) -> Dict[str, float]:
        """Get balances for all addresses that have ever transacted."""
        balances: Dict[str, float] = defaultdict(float)
        for block in self._chain:
            for tx in block.transactions:
                balances[tx.to_address] += tx.amount
                if tx.from_address != Transaction.COINBASE_ADDRESS:
                    balances[tx.from_address] -= (tx.amount + tx.fee)
        return {addr: round(bal, 8) for addr, bal in balances.items() if bal != 0}

    # ------------------------------------------------------------------
    # Chain replacement (consensus)
    # ------------------------------------------------------------------

    def replace_chain(self, new_chain: List[Block]) -> bool:
        """
        Replace our chain if the new one is longer and valid.
        Longest valid chain wins — same rule as Bitcoin.
        """
        if len(new_chain) <= len(self._chain):
            return False

        temp = BorderChain()
        temp._chain = new_chain
        valid, reason = temp.validate_chain()
        if not valid:
            logger.warning(f"[Chain] Rejected longer chain: {reason}")
            return False

        self._chain = new_chain
        self._rebuild_spent_set()
        logger.info(f"[Chain] Chain replaced with longer valid chain ({len(new_chain)} blocks)")
        return True

    def _rebuild_spent_set(self) -> None:
        self._spent_receipts = set()
        for block in self._chain:
            for proof in block.bandwidth_proofs:
                self._spent_receipts.add(proof.receipt_id)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

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
    def pending_bandwidth_mb(self) -> float:
        return sum(p.bytes_forwarded for p in self._pending_proofs) / (1024 * 1024)

    @property
    def stats(self) -> dict:
        return {
            "height": self.height,
            "total_supply": self.total_supply,
            "pending_proofs": len(self._pending_proofs),
            "pending_bandwidth_mb": round(self.pending_bandwidth_mb, 2),
            "mempool_size": len(self._mempool),
            "spent_receipts": len(self._spent_receipts),
            "min_bandwidth_per_block_mb": MIN_BYTES_PER_BLOCK / (1024 * 1024),
            "block_reward_bc": BLOCK_REWARD,
            "bc_per_gb": BC_PER_GB,
        }

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _save(self) -> None:
        if not self._persist_path:
            return
        data = {"chain": [b.to_dict() for b in self._chain]}
        self._persist_path.write_text(json.dumps(data))

    def _load(self) -> None:
        if not self._persist_path or not self._persist_path.exists():
            return
        data = json.loads(self._persist_path.read_text())
        self._chain = [Block.from_dict(b) for b in data["chain"]]
        self._rebuild_spent_set()
        logger.info(f"[Chain] Loaded chain: {len(self._chain)} blocks")
