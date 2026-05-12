"""
BorderCoin Blockchain — Chain
Proof of Bandwidth + Proof of Compute consensus.
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from .block import Block, BandwidthProof, ComputeProofRecord, MIN_BYTES_PER_BLOCK, BLOCK_REWARD, BC_PER_GB, BC_PER_COMPUTE_HOUR
from .transaction import Transaction

logger = logging.getLogger("border.blockchain")


class BorderChain:
    def __init__(self, persist_path: Optional[str] = None):
        self._chain: List[Block] = [Block.genesis()]
        self._mempool: List[Transaction] = []
        self._pending_proofs: List[BandwidthProof] = []
        self._pending_compute_proofs: List[ComputeProofRecord] = []
        self._spent_receipts: Set[str] = set()
        self._spent_compute_proofs: Set[str] = set()
        self._persist_path = Path(persist_path) if persist_path else None

        if self._persist_path and self._persist_path.exists():
            self._load()
        else:
            logger.info("[Chain] Initialized with genesis block")

    def create_block(self, miner_address: str,
                     proofs: Optional[List[BandwidthProof]] = None,
                     transactions: Optional[List[Transaction]] = None) -> Optional[Block]:
        available_proofs = proofs or self._pending_proofs
        valid_proofs = [p for p in available_proofs if p.receipt_id not in self._spent_receipts]
        total_bytes = sum(p.bytes_forwarded for p in valid_proofs)

        if total_bytes < MIN_BYTES_PER_BLOCK:
            logger.info(f"[Chain] Not enough bandwidth: {total_bytes/(1024*1024):.1f}MB / {MIN_BYTES_PER_BLOCK/(1024*1024):.0f}MB")
            return None

        valid_compute = [p for p in self._pending_compute_proofs if p.proof_id not in self._spent_compute_proofs]
        pending_txs = [tx for tx in (transactions or self._mempool) if tx.verify()]

        block = Block(
            index=len(self._chain),
            timestamp=time.time(),
            previous_hash=self.latest_block.block_hash,
            miner_address=miner_address,
            bandwidth_proofs=valid_proofs,
            compute_proofs=valid_compute,
            transactions=pending_txs,
        )
        block.finalize()
        return block

    def add_block(self, block: Block) -> Tuple[bool, str]:
        valid, reason = self._validate_block(block)
        if not valid:
            logger.warning(f"[Chain] Rejected block #{block.index}: {reason}")
            return False, reason

        self._chain.append(block)

        for proof in block.bandwidth_proofs:
            self._spent_receipts.add(proof.receipt_id)
        for cproof in block.compute_proofs:
            self._spent_compute_proofs.add(cproof.proof_id)

        spent_ids = {p.receipt_id for p in block.bandwidth_proofs}
        self._pending_proofs = [p for p in self._pending_proofs if p.receipt_id not in spent_ids]

        spent_cids = {p.proof_id for p in block.compute_proofs}
        self._pending_compute_proofs = [p for p in self._pending_compute_proofs if p.proof_id not in spent_cids]

        confirmed_ids = {tx.tx_id for tx in block.transactions}
        self._mempool = [tx for tx in self._mempool if tx.tx_id not in confirmed_ids]

        logger.info(
            f"[Chain] ✓ Block #{block.index} | "
            f"{len(block.bandwidth_proofs)} bw-proofs | "
            f"{len(block.compute_proofs)} compute-proofs | "
            f"+{block.total_bandwidth_pc:.4f} BC bw | "
            f"+{block.total_compute_bc:.4f} BC compute"
        )

        if self._persist_path:
            self._save()
        return True, "accepted"

    def add_proof(self, proof: BandwidthProof) -> bool:
        if proof.receipt_id in self._spent_receipts:
            return False
        if any(p.receipt_id == proof.receipt_id for p in self._pending_proofs):
            return False
        self._pending_proofs.append(proof)
        return True

    def add_compute_proof(self, proof: ComputeProofRecord) -> bool:
        if proof.proof_id in self._spent_compute_proofs:
            return False
        if any(p.proof_id == proof.proof_id for p in self._pending_compute_proofs):
            return False
        self._pending_compute_proofs.append(proof)
        logger.info(f"[Chain] Compute proof queued: {proof.proof_id} +{proof.compute_reward_bc:.6f} BC")
        return True

    def add_transaction(self, tx: Transaction) -> bool:
        if not tx.verify():
            return False
        if self.get_balance(tx.from_address) < tx.amount + tx.fee:
            return False
        self._mempool.append(tx)
        return True

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
        for i in range(1, len(self._chain)):
            block = self._chain[i]
            prev  = self._chain[i - 1]
            if block.previous_hash != prev.block_hash:
                return False, f"Block #{i}: broken link"
            if block.compute_hash() != block.block_hash:
                return False, f"Block #{i}: hash invalid"
            if block.total_bytes < MIN_BYTES_PER_BLOCK:
                return False, f"Block #{i}: insufficient bandwidth"
        return True, "chain valid"

    def get_balance(self, address: str) -> float:
        balance = 0.0
        for block in self._chain:
            for tx in block.transactions:
                if tx.to_address == address:
                    balance += tx.amount
                if tx.from_address == address:
                    balance -= (tx.amount + tx.fee)
        return round(balance, 8)

    def replace_chain(self, new_chain: List[Block]) -> bool:
        if len(new_chain) <= len(self._chain):
            return False
        temp = BorderChain()
        temp._chain = new_chain
        valid, reason = temp.validate_chain()
        if not valid:
            return False
        self._chain = new_chain
        self._rebuild_spent_set()
        return True

    def _rebuild_spent_set(self) -> None:
        self._spent_receipts = set()
        self._spent_compute_proofs = set()
        for block in self._chain:
            for proof in block.bandwidth_proofs:
                self._spent_receipts.add(proof.receipt_id)
            for cproof in block.compute_proofs:
                self._spent_compute_proofs.add(cproof.proof_id)

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
    def pending_compute_bc(self) -> float:
        return sum(p.compute_reward_bc for p in self._pending_compute_proofs)

    @property
    def stats(self) -> dict:
        return {
            "height":                     self.height,
            "total_supply":               self.total_supply,
            "pending_proofs":             len(self._pending_proofs),
            "pending_bandwidth_mb":       round(self.pending_bandwidth_mb, 2),
            "pending_compute_proofs":     len(self._pending_compute_proofs),
            "pending_compute_bc":         round(self.pending_compute_bc, 6),
            "mempool_size":               len(self._mempool),
            "spent_receipts":             len(self._spent_receipts),
            "spent_compute_proofs":       len(self._spent_compute_proofs),
            "min_bandwidth_per_block_mb": MIN_BYTES_PER_BLOCK / (1024 * 1024),
            "block_reward_bc":            BLOCK_REWARD,
            "bc_per_gb":                  BC_PER_GB,
            "bc_per_compute_hour":        BC_PER_COMPUTE_HOUR,
        }

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
