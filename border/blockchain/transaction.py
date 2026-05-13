"""
BorderCoin Transactions
Send BC from one wallet to another.
Every transaction is signed by the sender -- unforgeable.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Transaction:
    """
    A BorderCoin transfer between two addresses.

    Economics:
        - Fee: 0.001 BC per transaction (goes to the block miner)
        - Min amount: 0.000001 BC (1 satoshi equivalent)
    """
    tx_id:        str
    from_address: str
    to_address:   str
    amount:       float
    fee:          float
    timestamp:    float
    public_key:   str          # sender's public key (for verification)
    signature:    Optional[str] = None

    TX_FEE           = 0.001
    COINBASE_ADDRESS = "BC_COINBASE_00000000000000000000000000000000"

    @classmethod
    def create(
        cls,
        from_address: str,
        to_address:   str,
        amount:       float,
        public_key:   str,
        fee:          Optional[float] = None,
    ) -> "Transaction":
        return cls(
            tx_id        = f"tx_{uuid.uuid4().hex[:16]}",
            from_address = from_address,
            to_address   = to_address,
            amount       = round(amount, 8),
            fee          = fee if fee is not None else cls.TX_FEE,
            timestamp    = time.time(),
            public_key   = public_key,
        )

    @classmethod
    def coinbase(cls, to_address: str, reward: float) -> "Transaction":
        """Block reward transaction -- no sender, no fee."""
        return cls(
            tx_id        = f"cb_{uuid.uuid4().hex[:16]}",
            from_address = cls.COINBASE_ADDRESS,
            to_address   = to_address,
            amount       = reward,
            fee          = 0.0,
            timestamp    = time.time(),
            public_key   = "",
            signature    = "COINBASE",
        )

    def signing_data(self) -> bytes:
        """
        Canonical bytes to sign -- JSON-serialised for unambiguous encoding.
        Includes tx_id so replay of the same fields with a different ID is rejected.
        """
        content = json.dumps({
            "tx_id":        self.tx_id,
            "from_address": self.from_address,
            "to_address":   self.to_address,
            "amount":       self.amount,
            "fee":          self.fee,
            "timestamp":    self.timestamp,
        }, sort_keys=True)
        return content.encode()

    def sign(self, wallet) -> None:
        """Sign this transaction with a wallet."""
        self.signature = wallet.sign(self.signing_data())

    def _public_key_matches_address(self) -> bool:
        """
        Ensure the embedded public_key actually derives to from_address.
        Prevents substitution attacks where a forged public_key is injected.
        """
        try:
            pub_bytes = base64.b64decode(self.public_key)
            derived   = "BC_" + hashlib.sha256(pub_bytes).hexdigest()[:32]
            return derived == self.from_address
        except Exception:
            return False

    def verify(self) -> bool:
        """Verify the transaction signature and public-key/address binding."""
        if self.from_address == self.COINBASE_ADDRESS:
            return self.signature == "COINBASE"
        if not self.signature or not self.public_key:
            return False
        if not self._public_key_matches_address():
            return False
        from .wallet import BorderWallet
        return BorderWallet.verify(self.public_key, self.signing_data(), self.signature)

    def hash(self) -> str:
        content = json.dumps({
            "tx_id":        self.tx_id,
            "from_address": self.from_address,
            "to_address":   self.to_address,
            "amount":       self.amount,
            "fee":          self.fee,
            "timestamp":    self.timestamp,
        }, sort_keys=True)
        return hashlib.sha256(content.encode()).hexdigest()

    def to_dict(self) -> dict:
        return {
            "tx_id":        self.tx_id,
            "from_address": self.from_address,
            "to_address":   self.to_address,
            "amount":       self.amount,
            "fee":          self.fee,
            "timestamp":    self.timestamp,
            "public_key":   self.public_key,
            "signature":    self.signature,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Transaction":
        return cls(
            tx_id        = d["tx_id"],
            from_address = d["from_address"],
            to_address   = d["to_address"],
            amount       = d["amount"],
            fee          = d["fee"],
            timestamp    = d["timestamp"],
            public_key   = d.get("public_key", ""),
            signature    = d.get("signature"),
        )
