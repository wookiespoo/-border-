"""
BorderCoin Blockchain
Proof of Bandwidth — the work is real internet access, not wasted hashes.

Usage:
    from phantom.blockchain import BorderWallet, BorderChain, BorderChainNode
    from phantom.blockchain import Transaction, Block, BandwidthProof

    wallet = BorderWallet.create()
    chain  = BorderChain()
    node   = BorderChainNode(wallet=wallet, port=7777)
    node.run()
"""

from .wallet import BorderWallet
from .transaction import Transaction
from .block import Block, BandwidthProof, MIN_BYTES_PER_BLOCK, BLOCK_REWARD, BC_PER_GB
from .chain import BorderChain
from .node import BorderChainNode

__all__ = [
    "BorderWallet",
    "Transaction",
    "Block",
    "BandwidthProof",
    "BorderChain",
    "BorderChainNode",
    "MIN_BYTES_PER_BLOCK",
    "BLOCK_REWARD",
    "BC_PER_GB",
]
