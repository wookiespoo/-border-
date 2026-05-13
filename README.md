# Border Protocol

**A decentralized internet infrastructure network.**

Border is a peer-to-peer protocol that rewards nodes for providing real compute, storage, bandwidth, DNS, and identity services. Every service unit earns BorderCoin (BC) — a cryptographic token with a fixed supply schedule identical to Bitcoin's.

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Modules](#modules)
- [Getting Started](#getting-started)
- [Running a Node](#running-a-node)
- [Light Client / CLI](#light-client--cli)
- [Testnet](#testnet)
- [Token Economics](#token-economics)
- [Security Model](#security-model)
- [Development](#development)

---

## Overview

| Layer | What it does |
|---|---|
| **Blockchain** | Proof-of-Bandwidth + Proof-of-Compute + Proof-of-Storage consensus |
| **P2P** | Gossip protocol, peer discovery, binary-search chain sync |
| **Storage** | ChaCha20-Poly1305 encrypted file chunking, challenge/proof system |
| **Compute** | GPU job market, worker daemons, signed proof-of-work |
| **DNS** | On-chain `.border` name registry, signed ownership transfers |
| **Identity** | Ed25519 DIDs, verifiable claims, reputation engine |
| **DAO** | On-chain governance, signed vote submission |
| **Obfuscation** | HKDF-derived session keys, traffic shaping relay |

---

## Architecture

```
                        ┌──────────────────────────────┐
                        │         border-node           │
                        │  (unified process runner)     │
                        └────────────┬─────────────────┘
                                     │
          ┌──────────────────────────┼──────────────────────────┐
          │                          │                          │
   ┌──────▼──────┐           ┌───────▼──────┐          ┌───────▼──────┐
   │  Blockchain  │           │     P2P      │          │  Subsystems  │
   │  chain.py    │◄──────────│  discovery   │          │  storage     │
   │  block.py    │           │  gossip      │          │  compute     │
   │  wallet.py   │           │  sync        │          │  dns         │
   │  transaction │           │  server      │          │  identity    │
   └─────────────┘           └─────────────┘          └──────────────┘
```

---

## Modules

### `border/blockchain/`
- **`chain.py`** — `BorderChain`: add/validate blocks, mempool, balance lookup, persistence
- **`block.py`** — `Block`, `BandwidthProof`, `ComputeProofRecord`, `StorageProofRecord`
- **`transaction.py`** — `Transaction`: canonical JSON signing, Ed25519 verification, public-key→address binding
- **`wallet.py`** — `BorderWallet`: Ed25519 key pair, scrypt+ChaCha20-Poly1305 encrypted save/load
- **`economics.py`** — halving schedule, supply cap, fee market floor, `block_reward(height)`

### `border/p2p/`
- **`node.py`** — `P2PNode`: high-level object bundling discovery + gossip + sync
- **`discovery.py`** — `PeerDiscovery`: bootstrap seeds, ping, peer-exchange, disk persistence
- **`gossip.py`** — `GossipRouter`: push-fanout gossip with TTL and dedup cache
- **`sync.py`** — `ChainSync`: binary-search fork finding + batch block download
- **`server.py`** — Flask Blueprint: `/p2p/ping`, `/p2p/peers`, `/p2p/gossip`, `/p2p/blocks`

### `border/storage/`
- **`chunk.py`** — `FileChunker`: ChaCha20-Poly1305 encryption (AAD = chunk_id), manifest
- **`proof.py`** — `StorageProof`: canonical JSON hash, Ed25519 node signature
- **`node.py`** — `BorderStorageNode`: upload/download server, challenge-response proofs
- **`client.py`** — `BorderStorageClient`: upload + ChaCha20-Poly1305 download

### `border/compute/`
- **`job.py`** — `ComputeJob`, `ComputeProof`: worker_public_key + Ed25519 signature
- **`market.py`** — `ComputeMarket`: job matching, signature verification on proof submission
- **`daemon.py`** — `WorkerDaemon`: GPU job runner (OS-level sandboxing required in production)
- **`worker.py`** — `Worker`: registration, GPU spec advertisement

### `border/dns/`
- **`registry.py`** — `DNSRegistry`: signed register/transfer/add-record (forgery rejected)
- **`node.py`** — `BorderDNSNode`: HTTP API passing owner_public_key + owner_signature
- **`resolver.py`** — `DNSResolver`: local + remote resolution

### `border/identity/`
- **`did.py`** — `BorderDID`: Ed25519 decentralized identifier
- **`claim.py`** — `VerifiableClaim`: Ed25519 verification (replaces SECP256k1)
- **`registry.py`** — `IdentityRegistry`: DID + claim storage
- **`reputation.py`** — `ReputationEngine`: stake-weighted scoring

### `border/dao/`
- **`governance.py`** — `Governance`: proposal lifecycle, signature-verified vote casting
- **`vote.py`** — `Vote`: `verify_signature(public_key_b64)` enforced before counting
- **`proposal.py`** — `Proposal`: quorum + threshold enforcement
- **`treasury.py`** — `Treasury`: multi-sig fund release

### `border/node_runner.py`
Unified process that boots all subsystems. Exposes:
- `GET  /status`
- `GET  /chain/height`, `/chain/block/<n>`, `/chain/balance/<addr>`
- `POST /chain/tx`, `POST /chain/mine`
- All `/p2p/*` routes

### `border/cli.py`
Light-client CLI — no local chain required.

### `border/obfuscate.py`
Traffic relay with HKDF (salt=session_id), X25519 ECDH, ChaCha20-Poly1305.

---

## Getting Started

```bash
git clone https://github.com/<you>/phantom
cd phantom
pip install flask requests cryptography
```

Run all demos end-to-end:

```bash
python examples/p2p_demo.py
python examples/node_runner_demo.py
python examples/cli_demo.py
python examples/economics_demo.py
BORDER_NETWORK=testnet python examples/testnet_demo.py
python examples/storage_demo.py
python examples/dns_demo.py
python examples/dao_demo.py
```

---

## Running a Node

```bash
# Start a full node on port 9000, seeding from a known peer
python -m border.node_runner \
  --host 0.0.0.0 \
  --port 9000 \
  --data-dir ~/.border \
  --peers seed.border.network:9000 \
  --storage --compute --dns

# Check your node
curl http://localhost:9000/status
curl http://localhost:9000/wallet
curl http://localhost:9000/chain/height
```

**Environment variables** (override CLI flags):

| Variable | Default |
|---|---|
| `BORDER_HOST` | `0.0.0.0` |
| `BORDER_PORT` | `9000` |
| `BORDER_DATA_DIR` | `~/.border` |
| `BORDER_PEERS` | _(empty)_ |
| `BORDER_NETWORK` | `mainnet` |

---

## Light Client / CLI

```bash
# Create a wallet
python -m border.cli wallet new

# Check balance
python -m border.cli wallet info

# Send BC
python -m border.cli wallet send BC_<recipient_address> 1.5

# Register a .border domain
python -m border.cli dns register myname

# Check chain
python -m border.cli chain status
python -m border.cli chain balance BC_<address>
python -m border.cli chain block 42

# Point at a different node
python -m border.cli --node-url http://node.border.network:9000 chain status
```

---

## Testnet

A 3-node local testnet can be started with Docker Compose:

```bash
cd testnet/
docker-compose up --build
```

Or without Docker:

```bash
BORDER_NETWORK=testnet bash testnet/run_local_testnet.sh start
# Check:
curl http://127.0.0.1:9001/status
# Stop:
bash testnet/run_local_testnet.sh stop
```

**Testnet vs mainnet differences:**

| Parameter | Mainnet | Testnet |
|---|---|---|
| Min bytes/block | 100 MB | 1 MB |
| Chain ID | 1 | 1337 |
| Network ID | `border-mainnet` | `border-testnet-1` |
| Bootstrap peers | TBD | `seed1.testnet.border.network:9000` |

---

## Token Economics

| Parameter | Value |
|---|---|
| Maximum supply | 21,000,000 BC |
| Initial block reward | 50 BC |
| Halving interval | 210,000 blocks (~4 years) |
| Minimum tx fee | 0.0001 BC |
| Storage reward | 0.01 BC / GB / day |
| Compute reward | 2.0 BC / GPU-hour |
| Bandwidth reward | 1.0 BC / GB forwarded |

Block rewards follow a Bitcoin-style halving schedule. Service rewards (storage, compute, bandwidth) are added on top of the base block reward and are also capped by the remaining supply headroom.

---

## Security Model

All cryptographic operations use modern primitives:

| Operation | Primitive |
|---|---|
| Signing / verification | Ed25519 |
| Symmetric encryption | ChaCha20-Poly1305 (AEAD) |
| Key derivation (wallet) | scrypt (N=32768, r=8, p=1) |
| Key derivation (relay) | HKDF-SHA256 (salt=session_id) |
| Key exchange | X25519 ECDH |

**Key enforcements:**
- Every transaction binds `public_key` to `from_address` at verification time
- DNS register/transfer requires a signed owner intent verified against the stored owner address
- Storage proofs carry `node_public_key` and are re-verified when blocks arrive from peers
- DAO votes must be signed; `cast_vote()` rejects unsigned votes when a public key is provided
- Compute proofs carry `worker_public_key`; `submit_proof()` verifies the signature
- `compute/daemon.py` `_run_custom` documents the requirement for OS-level sandboxing (seccomp/nsjail/gVisor) in production

---

## Development

```
border/
  blockchain/     chain, block, transaction, wallet, economics
  p2p/            discovery, gossip, sync, server, node
  storage/        chunk, proof, node, client
  compute/        job, market, daemon, worker
  dns/            registry, node, resolver, record
  identity/       did, claim, registry, reputation
  dao/            governance, vote, proposal, treasury
  infer/          inference job market
  render/         render job market
  testnet/        config (testnet parameter overrides)
  cli.py          light client CLI
  node_runner.py  unified node process
  obfuscate.py    traffic relay + obfuscation layer

examples/
  p2p_demo.py
  node_runner_demo.py
  cli_demo.py
  economics_demo.py
  testnet_demo.py
  storage_demo.py
  dns_demo.py
  dao_demo.py
  compute_demo.py
  identity_demo.py

testnet/
  genesis.json
  docker-compose.yml
  Dockerfile
  run_local_testnet.sh
```

---

*Border is an open protocol. Run a node. Earn BC.*
