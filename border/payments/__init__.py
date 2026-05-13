"""
border.payments — off-chain micropayment channels.

A Lightning-style unidirectional payment channel for streaming
micro-payments (bandwidth, inference, storage) between Border nodes
without a blockchain transaction per payment.

Quick start
-----------
>>> from border.payments import ChannelManager, PaymentReceipt
>>> from border.blockchain.wallet import BorderWallet
>>>
>>> mgr = ChannelManager(chain=my_chain)
>>> ok, reason, ch = mgr.open_channel(sender_wallet, receiver_address, deposit=10.0)
>>>
>>> # Each relay packet / compute second:
>>> ok, reason, receipt = mgr.send(ch.channel_id, increment=0.001, sender_wallet=sender_wallet)
>>>
>>> # Receiver side:
>>> mgr.receive(receipt, receiver_wallet, sender_public_key=sender_wallet.public_key_b64)
>>>
>>> # When done:
>>> mgr.close(ch.channel_id, sender_wallet)
>>> mgr.settle(ch.channel_id, sender_wallet)
"""

from .channel import PaymentChannel, ChannelState
from .receipt import PaymentReceipt
from .manager import ChannelManager, ChannelError

__all__ = [
    "PaymentChannel",
    "ChannelState",
    "PaymentReceipt",
    "ChannelManager",
    "ChannelError",
]
