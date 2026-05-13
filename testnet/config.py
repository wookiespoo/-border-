"""
border.testnet_config — Testnet constants and genesis override.

Import this before starting a testnet node to switch the chain to
testnet parameters (lower MIN_BYTES_PER_BLOCK for easy mining,
different network label, bootstrap peers).

Usage:
    # In your node startup script:
    import border.testnet.testnet_config as _tc  # noqa: F401 (side-effects)
    from border.node_runner import BorderNode
    node = BorderNode(peers=_tc.BOOTSTRAP_PEERS, ...)

Or just set BORDER_NETWORK=testnet before launching border-node.
"""

import os

NETWORK_ID      = "border-testnet-1"
CHAIN_ID        = 1337
BOOTSTRAP_PEERS = [
    "seed1.testnet.border.network:9000",
    "seed2.testnet.border.network:9000",
]

# Testnet uses a much lower bandwidth floor so test miners can produce blocks
# without forwarding 100 MB of real traffic.
TESTNET_MIN_BYTES_PER_BLOCK = 1 * 1024 * 1024   # 1 MB (vs mainnet 100 MB)

# Apply testnet overrides when this module is imported
if os.environ.get("BORDER_NETWORK", "mainnet") == "testnet":
    import border.blockchain.block as _block_mod
    _block_mod.MIN_BYTES_PER_BLOCK = TESTNET_MIN_BYTES_PER_BLOCK
    print(f"[Testnet] Network={NETWORK_ID}  "
          f"min_bytes={TESTNET_MIN_BYTES_PER_BLOCK // (1024*1024)}MB")
