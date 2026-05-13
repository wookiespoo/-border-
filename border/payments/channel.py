"""
border.payments.channel — off-chain payment channel state machine.

Design
------
A PaymentChannel is a Lightning-style unidirectional value stream:

  1. Sender calls ChannelManager.open_channel() — locks BC on-chain.
  2. For each micro-payment the sender creates a PaymentReceipt
     (cumulative amount, monotone nonce) and signs it.
  3. Receiver verifies and countersigns.  No on-chain tx per payment.
  4. Either party calls close() — the final receipt is settled on-chain
     via a single BorderChain transaction releasing funds to receiver
     and returning the remainder to sender.

Each receipt is signed over: channel_id | nonce | cumulative_amount
so receipts are strictly ordered and cannot be replayed.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ChannelState(str, Enum):
    OPEN      = "open"
    CLOSING   = "closing"   # close requested, awaiting on-chain settlement
    CLOSED    = "closed"
    DISPUTED  = "disputed"  # one party submitted a stale receipt


@dataclass
class PaymentChannel:
    """
    Represents an off-chain payment channel between two Border addresses.

    Attributes
    ----------
    channel_id      : unique identifier (UUID4 hex)
    sender_address  : address that deposited funds
    receiver_address: address that receives payments
    deposited       : total BC locked at open time
    nonce           : last confirmed receipt nonce (0 = no receipts yet)
    cumulative_paid : cumulative BC paid so far (latest receipt amount)
    state           : current state of the channel
    opened_at       : unix timestamp of channel creation
    expires_at      : unix timestamp when channel auto-expires (0 = never)
    closed_at       : unix timestamp when channel was closed (0 = not closed)
    """
    channel_id:       str
    sender_address:   str
    receiver_address: str
    deposited:        float
    nonce:            int   = 0
    cumulative_paid:  float = 0.0
    state:            ChannelState = ChannelState.OPEN
    opened_at:        float = field(default_factory=time.time)
    expires_at:       float = 0.0
    closed_at:        float = 0.0

    # ------------------------------------------------------------------ #
    # Factory
    # ------------------------------------------------------------------ #

    @classmethod
    def create(
        cls,
        sender_address:   str,
        receiver_address: str,
        deposited:        float,
        ttl_seconds:      float = 0.0,
    ) -> "PaymentChannel":
        now = time.time()
        return cls(
            channel_id       = uuid.uuid4().hex,
            sender_address   = sender_address,
            receiver_address = receiver_address,
            deposited        = deposited,
            opened_at        = now,
            expires_at       = (now + ttl_seconds) if ttl_seconds > 0 else 0.0,
        )

    # ------------------------------------------------------------------ #
    # Derived properties
    # ------------------------------------------------------------------ #

    @property
    def balance_receiver(self) -> float:
        """How much the receiver can claim on settlement."""
        return round(min(self.cumulative_paid, self.deposited), 8)

    @property
    def balance_sender(self) -> float:
        """How much the sender gets back on settlement."""
        return round(max(self.deposited - self.cumulative_paid, 0.0), 8)

    @property
    def is_expired(self) -> bool:
        return self.expires_at > 0 and time.time() > self.expires_at

    @property
    def is_active(self) -> bool:
        return self.state == ChannelState.OPEN and not self.is_expired

    # ------------------------------------------------------------------ #
    # Serialization
    # ------------------------------------------------------------------ #

    def to_dict(self) -> dict:
        return {
            "channel_id":       self.channel_id,
            "sender_address":   self.sender_address,
            "receiver_address": self.receiver_address,
            "deposited":        self.deposited,
            "nonce":            self.nonce,
            "cumulative_paid":  self.cumulative_paid,
            "state":            self.state.value,
            "opened_at":        self.opened_at,
            "expires_at":       self.expires_at,
            "closed_at":        self.closed_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PaymentChannel":
        return cls(
            channel_id       = d["channel_id"],
            sender_address   = d["sender_address"],
            receiver_address = d["receiver_address"],
            deposited        = d["deposited"],
            nonce            = d.get("nonce", 0),
            cumulative_paid  = d.get("cumulative_paid", 0.0),
            state            = ChannelState(d.get("state", "open")),
            opened_at        = d.get("opened_at", 0.0),
            expires_at       = d.get("expires_at", 0.0),
            closed_at        = d.get("closed_at", 0.0),
        )
