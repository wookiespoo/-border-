#!/usr/bin/env bash
# Push BorderCoin blockchain update to GitHub
# Run this in Git Bash from the phantom project folder

set -e

echo ""
echo "💎 Pushing BorderCoin blockchain to GitHub..."
echo ""

# Make sure we're in the phantom directory
if [ ! -f "pyproject.toml" ]; then
  echo "ERROR: Run this script from inside the phantom project folder"
  echo "  cd /c/Users/pj382/AppData/Roaming/Claude/local-agent-mode-sessions/6c212c2a-8fee-4f86-bdf2-c3acb4258d44/9a322b45-b4b2-4a37-9e4d-0981967bf6f1/local_997baaed-bebe-40cf-84fb-803a6bfbd6de/outputs/phantom"
  exit 1
fi

# Stage new/changed files
git add phantom/blockchain/__init__.py
git add phantom/blockchain/wallet.py
git add phantom/blockchain/transaction.py
git add phantom/blockchain/block.py
git add phantom/blockchain/chain.py
git add phantom/blockchain/node.py
git add phantom/node.py
git add examples/blockchain_demo.py

echo "Files staged:"
git diff --cached --name-only

echo ""
git commit -m "feat: add BorderCoin Proof of Bandwidth blockchain

- wallet.py     Ed25519 keypair, PC_ addresses derived from pubkey
- transaction.py Signed transfers + coinbase block rewards
- block.py      BandwidthProof dataclass, Block with 100MB minimum
- chain.py      Full blockchain: PoB consensus, balances, longest-chain
- node.py       FastAPI P2P node (mine/broadcast/sync)
- __init__.py   Package exports

Integration:
- phantom/node.py now accepts wallet + chain_endpoint params
- Every proxied byte auto-submits a BandwidthProof to the chain
- Relay operators earn 1 PC per GB forwarded + 1 PC block reward
- Fire-and-forget: relay performance unaffected by chain latency

Demo:
- examples/blockchain_demo.py: full in-process end-to-end test
  relay traffic -> receipts -> proofs -> mine block -> check balance
  All tests pass ✓"

git push origin main

echo ""
echo "✅ BorderCoin blockchain pushed to github.com/wookiespoo/phantom"
echo ""
echo "The blockchain is live:"
echo "  Mining = forwarding real internet traffic to censored users"
echo "  1 PC per GB forwarded  +  1 PC block reward"
echo "  No wasted energy. The work IS the proof."
echo ""
