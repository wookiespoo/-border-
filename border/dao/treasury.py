"""
BorderDAO — Treasury

Protocol fees flow into the treasury.
Spending requires a PASSED treasury proposal.

Fee sources:
  - 1% of all BC transactions (set by governance)
  - 0.5% of compute job payments
  - 0.5% of storage payments
  - 0.5% of inference payments
  - 0.5% of render payments
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("border.dao.treasury")

# Default fee rates (can be changed by governance)
TX_FEE_PCT       = 0.01    # 1% of tx value
COMPUTE_FEE_PCT  = 0.005   # 0.5% of compute revenue
STORAGE_FEE_PCT  = 0.005
INFER_FEE_PCT    = 0.005
RENDER_FEE_PCT   = 0.005


@dataclass
class TreasuryEntry:
    entry_id:    str
    source:      str      # "tx_fee" / "compute" / "storage" / "infer" / "render"
    amount_bc:   float
    from_address: str
    timestamp:   float    = field(default_factory=time.time)
    note:        str      = ""


@dataclass
class TreasurySpend:
    spend_id:     str
    proposal_id:  str
    recipient:    str
    amount_bc:    float
    executed_at:  float   = field(default_factory=time.time)
    note:         str     = ""


class BorderTreasury:
    def __init__(self):
        self._balance:  float               = 0.0
        self._income:   List[TreasuryEntry] = []
        self._spending: List[TreasurySpend] = []

        # Current fee rates (adjustable by governance)
        self.tx_fee_pct      = TX_FEE_PCT
        self.compute_fee_pct = COMPUTE_FEE_PCT
        self.storage_fee_pct = STORAGE_FEE_PCT
        self.infer_fee_pct   = INFER_FEE_PCT
        self.render_fee_pct  = RENDER_FEE_PCT

    # ── Income ────────────────────────────────────────────
    def collect(self, source: str, amount_bc: float,
                from_address: str = "", note: str = "") -> float:
        import uuid
        fee = round(amount_bc, 8)
        if fee <= 0:
            return 0.0
        entry = TreasuryEntry(
            entry_id     = uuid.uuid4().hex[:12],
            source       = source,
            amount_bc    = fee,
            from_address = from_address,
            note         = note,
        )
        self._income.append(entry)
        self._balance += fee
        logger.info(f"[Treasury] +{fee:.8f} BC from {source}")
        return fee

    def collect_tx_fee(self, tx_amount: float, from_address: str) -> float:
        return self.collect("tx_fee", tx_amount * self.tx_fee_pct, from_address)

    def collect_compute_fee(self, bc_earned: float, worker_address: str) -> float:
        return self.collect("compute", bc_earned * self.compute_fee_pct, worker_address)

    def collect_storage_fee(self, bc_earned: float, node_address: str) -> float:
        return self.collect("storage", bc_earned * self.storage_fee_pct, node_address)

    def collect_infer_fee(self, bc_earned: float, worker_address: str) -> float:
        return self.collect("infer", bc_earned * self.infer_fee_pct, worker_address)

    def collect_render_fee(self, bc_earned: float, worker_address: str) -> float:
        return self.collect("render", bc_earned * self.render_fee_pct, worker_address)

    # ── Spending ──────────────────────────────────────────
    def spend(self, proposal_id: str, recipient: str,
              amount_bc: float, note: str = "") -> Tuple[bool, str]:
        import uuid
        if amount_bc > self._balance:
            return False, f"Insufficient treasury balance: {self._balance:.8f} < {amount_bc:.8f}"
        spend = TreasurySpend(
            spend_id    = uuid.uuid4().hex[:12],
            proposal_id = proposal_id,
            recipient   = recipient,
            amount_bc   = amount_bc,
            note        = note,
        )
        self._spending.append(spend)
        self._balance -= amount_bc
        logger.info(f"[Treasury] -{amount_bc:.8f} BC → {recipient[:20]}... (proposal {proposal_id[:8]})")
        return True, spend.spend_id

    # ── Governance parameter updates ──────────────────────
    def apply_parameter(self, key: str, value: float) -> bool:
        mapping = {
            "tx_fee_pct":      "tx_fee_pct",
            "compute_fee_pct": "compute_fee_pct",
            "storage_fee_pct": "storage_fee_pct",
            "infer_fee_pct":   "infer_fee_pct",
            "render_fee_pct":  "render_fee_pct",
        }
        attr = mapping.get(key)
        if attr:
            setattr(self, attr, value)
            logger.info(f"[Treasury] Parameter updated: {key} = {value}")
            return True
        return False

    # ── Stats ─────────────────────────────────────────────
    @property
    def balance(self) -> float:
        return round(self._balance, 8)

    @property
    def total_collected(self) -> float:
        return round(sum(e.amount_bc for e in self._income), 8)

    @property
    def total_spent(self) -> float:
        return round(sum(s.amount_bc for s in self._spending), 8)

    @property
    def stats(self) -> dict:
        by_source: Dict[str, float] = {}
        for e in self._income:
            by_source[e.source] = by_source.get(e.source, 0.0) + e.amount_bc
        return {
            "balance":          self.balance,
            "total_collected":  self.total_collected,
            "total_spent":      self.total_spent,
            "income_count":     len(self._income),
            "spend_count":      len(self._spending),
            "by_source":        {k: round(v, 8) for k, v in by_source.items()},
            "fee_rates": {
                "tx":      self.tx_fee_pct,
                "compute": self.compute_fee_pct,
                "storage": self.storage_fee_pct,
                "infer":   self.infer_fee_pct,
                "render":  self.render_fee_pct,
            },
        }
