"""
BorderCoin Blockchain
Proof of Bandwidth + Proof of Compute — the work is real.
"""

from .wallet import BorderWallet
from .transaction import Transaction
from .block import Block, BandwidthProof, ComputeProofRecord, MIN_BYTES_PER_BLOCK, BLOCK_REWARD, BC_PER_GB, BC_PER_COMPUTE_HOUR
from .chain import BorderChain
from .node import BorderChainNode

__all__ = [
    "BorderWallet", "Transaction", "Block", "BandwidthProof", "ComputeProofRecord",
    "BorderChain", "BorderChainNode",
    "MIN_BYTES_PER_BLOCK", "BLOCK_REWARD", "BC_PER_GB", "BC_PER_COMPUTE_HOUR",
]
