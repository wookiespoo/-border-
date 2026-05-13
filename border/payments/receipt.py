"""
border.payments.receipt — signed off-chain micro-payment receipt.

Wire format (signing input)
---------------------------
SHA-256( channel_id | ":" | nonce_decimal | ":" | amount_8dp )

Both sender and receiver sign this.  Receiver's countersignature
(ack_signature) is optional but lets the sender prove the payment
was acknowledged if a dispute arises.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from ..blockchain.wallet import BorderWallet


def _receipt_signing_bytes(channel_id: str, nonce: int, amount: float) -> bytes:
    """Canonical bytes both parties sign.  Deterministic — no timestamps."""
    payload = f"{channel_id}:{nonce}:{amount:.8f}"
    return payload.encode()


@dataclass
class PaymentReceipt:
    """
    A single micro-payment receipt for an off-chain channel.

    The sender creates it; the receiver countersigns as an ACK.
    A receipt with a higher nonce supersedes all previous receipts
    for the same channel — only the final one needs to go on-chain.

    Attributes
    ----------
    receipt_id        : unique ID for this receipt
    channel_id        : the channel this payment belongs to
    nonce             : strictly increasing; must be > channel.nonce
    amount            : CUMULATIVE total paid (not just this increment)
    sender_address    : payer's Border address
    receiver_address  : payee's Border address
    sender_signature  : Ed25519 sig over signing bytes
    ack_signature     : optional receiver countersignature
    created_at        : unix timestamp
    memo              : optional human-readable note (e.g. "1 GB relayed")
    """
    receipt_id:       str
    channel_id:       str
    nonce:            int
    amount:           float           # cumulative
    sender_address:   str
    receiver_address: str
    sender_signature: str = ""
    ack_signature:    str = ""
    created_at:       float = field(default_factory=time.time)
    memo:             str = ""

    # ------------------------------------------------------------------ #
    # Factory
    # ------------------------------------------------------------------ #

    @classmethod
    def create(
        cls,
        channel_id:       str,
        nonce:            int,
        amount:           float,
        sender_address:   str,
        receiver_address: str,
        memo:             str = "",
    ) -> "PaymentReceipt":
        return cls(
            receipt_id       = uuid.uuid4().hex,
            channel_id       = channel_id,
            nonce            = nonce,
            amount           = round(amount, 8),
            sender_address   = sender_address,
            receiver_address = receiver_address,
            memo             = memo,
        )

    # ------------------------------------------------------------------ #
    # Signing helpers
    # ------------------------------------------------------------------ #

    def signing_bytes(self) -> bytes:
        return _receipt_signing_bytes(self.channel_id, self.nonce, self.amount)

    def sign_as_sender(self, wallet: BorderWallet) -> None:
        """Sign the receipt as the sender (payer)."""
        self.sender_signature = wallet.sign(self.signing_bytes())

    def ack_as_receiver(self, wallet: BorderWallet) -> None:
        """Countersign the receipt as the receiver (payee)."""
        self.ack_signature = wallet.sign(self.signing_bytes())

    def verify_sender(self, sender_public_key_b64: str) -> bool:
        """Verify the sender's signature."""
        if not self.sender_signature:
            return False
        return BorderWallet.verify(
            sender_public_key_b64, self.signing_bytes(), self.sender_signature
        )

    def verify_ack(self, receiver_public_key_b64: str) -> bool:
        """Verify the receiver's countersignature."""
        if not self.ack_signature:
            return False
        return BorderWallet.verify(
            receiver_public_key_b64, self.signing_bytes(), self.ack_signature
        )

    def is_valid_sender_sig(self, sender_public_key_b64: str) -> bool:
        return self.verify_sender(sender_public_key_b64)

    # ------------------------------------------------------------------ #
    # Serialization
    # ------------------------------------------------------------------ #

    def to_dict(self) -> dict:
        return {
            "receipt_id":       self.receipt_id,
            "channel_id":       self.channel_id,
            "nonce":            self.nonce,
            "amount":           self.amount,
            "sender_address":   self.sender_address,
            "receiver_address": self.receiver_address,
            "sender_signature": self.sender_signature,
            "ack_signature":    self.ack_signature,
            "created_at":       self.created_at,
            "memo":             self.memo,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PaymentReceipt":
        return cls(
            receipt_id       = d["receipt_id"],
            channel_id       = d["channel_id"],
            nonce            = d["nonce"],
            amount           = d["amount"],
            sender_address   = d["sender_address"],
            receiver_address = d["receiver_address"],
            sender_signature = d.get("sender_signature", ""),
            ack_signature    = d.get("ack_signature", ""),
            created_at       = d.get("created_at", 0.0),
            memo             = d.get("memo", ""),
        )
