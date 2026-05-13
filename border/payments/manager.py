"""
border.payments.manager — ChannelManager.

Lifecycle
---------
1. open_channel(sender_wallet, receiver_address, deposit_amount)
   → checks sender has enough balance, deducts from chain, returns channel
2. send(channel_id, increment, sender_wallet, memo="")
   → creates and signs a new PaymentReceipt; channel nonce++
3. receive(receipt, receiver_wallet, sender_public_key)
   → validates sender sig, countersigns, records highest-nonce receipt
4. close(channel_id, wallet, final_receipt=None)
   → marks channel CLOSING; settle(channel_id) pushes a real chain TX
5. settle(channel_id)
   → creates BorderChain transactions: deposit → receiver (paid),
     deposit - paid → sender (change)
"""

from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional, Tuple

from ..blockchain.chain import BorderChain
from ..blockchain.transaction import Transaction
from ..blockchain.wallet import BorderWallet
from .channel import PaymentChannel, ChannelState
from .receipt import PaymentReceipt

logger = logging.getLogger("border.payments")


class ChannelError(Exception):
    pass


class ChannelManager:
    """
    Manages the full lifecycle of off-chain payment channels.

    Parameters
    ----------
    chain   : BorderChain instance for on-chain open/settle operations
    """

    OPEN_FEE = 0.01  # BC burned on channel open (anti-spam)

    def __init__(self, chain: Optional[BorderChain] = None):
        self.chain: Optional[BorderChain] = chain
        self._channels:       Dict[str, PaymentChannel] = {}
        self._latest_receipt: Dict[str, PaymentReceipt] = {}  # channel_id → best receipt
        self._receipt_log:    Dict[str, List[PaymentReceipt]] = {}  # full history
        self._received_nonces: Dict[str, int] = {}  # channel_id → last ACK'd nonce

    # ------------------------------------------------------------------ #
    # Open
    # ------------------------------------------------------------------ #

    def open_channel(
        self,
        sender_wallet:    BorderWallet,
        receiver_address: str,
        deposit_amount:   float,
        ttl_seconds:      float = 0.0,
    ) -> Tuple[bool, str, Optional[PaymentChannel]]:
        """
        Open a new payment channel.

        Requires chain to have sufficient balance for the sender.
        Returns (ok, reason, channel).
        """
        if deposit_amount <= 0:
            return False, "deposit_amount must be positive", None

        sender_address = sender_wallet.address

        # Check on-chain balance if chain is wired
        if self.chain is not None:
            available = self.chain.get_balance(sender_address) - self.chain.get_staked(sender_address)
            total_cost = deposit_amount + self.OPEN_FEE
            if available < total_cost:
                return (
                    False,
                    f"Insufficient balance: have {available:.8f} BC, need {total_cost:.8f} BC",
                    None,
                )
            # Deduct deposit + fee via a lock transaction (to the channel "escrow" address)
            escrow_tx = Transaction.create(
                from_address = sender_address,
                to_address   = f"channel_{receiver_address[:8]}",  # symbolic escrow
                amount       = deposit_amount + self.OPEN_FEE,
                private_key  = sender_wallet._private_key,
            )
            if not self.chain.add_transaction(escrow_tx):
                return False, "Chain rejected escrow transaction", None

        channel = PaymentChannel.create(
            sender_address   = sender_address,
            receiver_address = receiver_address,
            deposited        = deposit_amount,
            ttl_seconds      = ttl_seconds,
        )
        self._channels[channel.channel_id] = channel
        self._receipt_log[channel.channel_id] = []
        self._received_nonces[channel.channel_id] = 0
        logger.info(
            f"[Payments] Channel opened  id={channel.channel_id[:8]}  "
            f"sender={sender_address[:12]}  recv={receiver_address[:12]}  "
            f"deposit={deposit_amount:.8f} BC"
        )
        return True, "ok", channel

    # ------------------------------------------------------------------ #
    # Send (off-chain)
    # ------------------------------------------------------------------ #

    def send(
        self,
        channel_id:    str,
        increment:     float,
        sender_wallet: BorderWallet,
        memo:          str = "",
    ) -> Tuple[bool, str, Optional[PaymentReceipt]]:
        """
        Create and sign a micro-payment receipt for `increment` BC.

        The receipt carries the NEW cumulative total, not just the delta.
        Returns (ok, reason, receipt).
        """
        ch = self._channels.get(channel_id)
        if ch is None:
            return False, f"Unknown channel: {channel_id}", None
        if not ch.is_active:
            return False, f"Channel is {ch.state.value}", None
        if ch.sender_address != sender_wallet.address:
            return False, "Wallet is not the channel sender", None
        if increment <= 0:
            return False, "increment must be positive", None

        new_cumulative = round(ch.cumulative_paid + increment, 8)
        if new_cumulative > ch.deposited:
            return (
                False,
                f"Payment would exceed deposit: cumulative {new_cumulative:.8f} > deposit {ch.deposited:.8f}",
                None,
            )

        new_nonce = ch.nonce + 1
        receipt = PaymentReceipt.create(
            channel_id       = channel_id,
            nonce            = new_nonce,
            amount           = new_cumulative,
            sender_address   = ch.sender_address,
            receiver_address = ch.receiver_address,
            memo             = memo,
        )
        receipt.sign_as_sender(sender_wallet)

        # Update channel state
        ch.nonce = new_nonce
        ch.cumulative_paid = new_cumulative
        self._latest_receipt[channel_id] = receipt
        self._receipt_log[channel_id].append(receipt)

        logger.debug(
            f"[Payments] Receipt #{new_nonce}  channel={channel_id[:8]}  "
            f"cumulative={new_cumulative:.8f} BC  memo='{memo}'"
        )
        return True, "ok", receipt

    # ------------------------------------------------------------------ #
    # Receive / ACK (off-chain)
    # ------------------------------------------------------------------ #

    def receive(
        self,
        receipt:             PaymentReceipt,
        receiver_wallet:     BorderWallet,
        sender_public_key:   str,
    ) -> Tuple[bool, str]:
        """
        Validate a receipt as the receiver and countersign it.

        Returns (ok, reason).  On success, `receipt.ack_signature` is set.
        """
        ch = self._channels.get(receipt.channel_id)
        if ch is None:
            # Receiver may not have the channel locally — do a stateless verify
            if not receipt.verify_sender(sender_public_key):
                return False, "Invalid sender signature"
            receipt.ack_as_receiver(receiver_wallet)
            return True, "ok (stateless)"

        last_recv = self._received_nonces.get(receipt.channel_id, 0)
        if receipt.nonce <= last_recv:
            return False, f"Stale receipt: nonce {receipt.nonce} ≤ already ACK'd {last_recv}"
        if not receipt.verify_sender(sender_public_key):
            return False, "Invalid sender signature"
        if receipt.amount > ch.deposited:
            return False, f"Receipt amount {receipt.amount} exceeds deposit {ch.deposited}"
        if receipt.sender_address != ch.sender_address:
            return False, "Receipt sender address mismatch"
        if receiver_wallet.address != ch.receiver_address:
            return False, "Wallet is not the channel receiver"

        receipt.ack_as_receiver(receiver_wallet)

        # Update local channel state
        self._received_nonces[receipt.channel_id] = receipt.nonce
        ch.nonce = max(ch.nonce, receipt.nonce)
        ch.cumulative_paid = receipt.amount
        self._latest_receipt[receipt.channel_id] = receipt
        self._receipt_log[receipt.channel_id].append(receipt)

        logger.debug(
            f"[Payments] ACK #{receipt.nonce}  channel={receipt.channel_id[:8]}  "
            f"cumulative={receipt.amount:.8f} BC"
        )
        return True, "ok"

    # ------------------------------------------------------------------ #
    # Close
    # ------------------------------------------------------------------ #

    def close(
        self,
        channel_id: str,
        wallet:     BorderWallet,
    ) -> Tuple[bool, str]:
        """
        Initiate cooperative channel closure.  Either party can close.
        Call settle() afterwards to push the on-chain settlement TX.
        """
        ch = self._channels.get(channel_id)
        if ch is None:
            return False, f"Unknown channel: {channel_id}"
        if ch.state not in (ChannelState.OPEN,):
            return False, f"Channel already {ch.state.value}"
        if wallet.address not in (ch.sender_address, ch.receiver_address):
            return False, "Not a party to this channel"

        ch.state = ChannelState.CLOSING
        ch.closed_at = time.time()
        logger.info(f"[Payments] Channel closing  id={channel_id[:8]}")
        return True, "ok"

    # ------------------------------------------------------------------ #
    # Settle (on-chain)
    # ------------------------------------------------------------------ #

    def settle(
        self,
        channel_id:    str,
        sender_wallet: BorderWallet,
    ) -> Tuple[bool, str]:
        """
        Push final balances on-chain.

        Creates two transactions from the channel escrow:
          • receiver gets cumulative_paid
          • sender  gets deposited - cumulative_paid  (change)

        In this simplified model the sender's wallet signs both TXs
        (in production, a multi-sig contract would hold escrow).
        """
        ch = self._channels.get(channel_id)
        if ch is None:
            return False, f"Unknown channel: {channel_id}"
        if ch.state not in (ChannelState.CLOSING, ChannelState.OPEN):
            return False, f"Channel is {ch.state.value}"
        if sender_wallet.address != ch.sender_address:
            return False, "Only sender can settle"

        settled = False
        if self.chain is not None and ch.balance_receiver > 0:
            tx_recv = Transaction.create(
                from_address = sender_wallet.address,
                to_address   = ch.receiver_address,
                amount       = ch.balance_receiver,
                private_key  = sender_wallet._private_key,
            )
            self.chain.add_transaction(tx_recv)
            settled = True
            logger.info(
                f"[Payments] Settled → receiver  {ch.balance_receiver:.8f} BC  "
                f"channel={channel_id[:8]}"
            )

        ch.state = ChannelState.CLOSED
        ch.closed_at = time.time()
        logger.info(
            f"[Payments] Channel closed  id={channel_id[:8]}  "
            f"paid={ch.balance_receiver:.8f}  returned={ch.balance_sender:.8f}"
        )
        return True, "settled" if settled else "closed (zero payment)"

    # ------------------------------------------------------------------ #
    # Queries
    # ------------------------------------------------------------------ #

    def get_channel(self, channel_id: str) -> Optional[PaymentChannel]:
        return self._channels.get(channel_id)

    def latest_receipt(self, channel_id: str) -> Optional[PaymentReceipt]:
        return self._latest_receipt.get(channel_id)

    def receipt_history(self, channel_id: str) -> List[PaymentReceipt]:
        return list(self._receipt_log.get(channel_id, []))

    def channels_for(self, address: str) -> List[PaymentChannel]:
        """Return all channels where address is sender or receiver."""
        return [
            ch for ch in self._channels.values()
            if address in (ch.sender_address, ch.receiver_address)
        ]

    @property
    def stats(self) -> dict:
        channels = list(self._channels.values())
        open_ch = [c for c in channels if c.state == ChannelState.OPEN]
        return {
            "total_channels":  len(channels),
            "open_channels":   len(open_ch),
            "closed_channels": len([c for c in channels if c.state == ChannelState.CLOSED]),
            "total_deposited": round(sum(c.deposited for c in open_ch), 8),
            "total_paid":      round(sum(c.cumulative_paid for c in channels), 8),
        }
