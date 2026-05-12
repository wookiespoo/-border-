# 🌐 Border

**Decentralised internet infrastructure. Owned by no one. Powered by everyone.**

Border is a full-stack decentralised protocol — censorship-resistant relay, GPU compute marketplace, encrypted storage, private AI inference, on-demand rendering, self-sovereign identity, and community governance. All connected by one currency: **BorderCoin (BC)**.

No company controls it. No server can be shut down. Every node that joins makes it stronger.

---

## The Ecosystem

```
┌─────────────────────────────────────────────────────────┐
│                    BorderCoin (BC)                       │
│        Universal currency · Powers every layer           │
└──────────────────┬──────────────────────────────────────┘
                   │
     ┌─────────────┼─────────────┐
     ▼             ▼             ▼
┌─────────┐  ┌───────────┐  ┌──────────────┐
│  Border │  │BorderChain│  │ BorderWallet │
│  Relay  │  │ P2P node  │  │ Ed25519 keys │
│BC/GB fwd│  │PoB mining │  │send·recv·sign│
└─────────┘  └───────────┘  └──────────────┘
     │             │
     ▼             ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│BorderCompute │  │ BorderInfer  │  │ BorderRender │
│GPU job market│  │Private AI    │  │Image·Video·3D│
│BC/GPU-hour   │  │BC/1K tokens  │  │BC/frame      │
└──────────────┘  └──────────────┘  └──────────────┘
     │
     ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│ BorderStore  │  │  BorderID    │  │  BorderDAO   │
│Encrypted     │  │Self-sovereign│  │BC holders    │
│BC/GB/day     │  │identity+rep  │  │vote on proto │
└──────────────┘  └──────────────┘  └──────────────┘
```

---

## Modules

| Module | What it does | How you earn |
|---|---|---|
| **Border Relay** | Routes censored traffic through obfuscated HTTPS | 1 BC per GB forwarded |
| **BorderChain** | Proof-of-Bandwidth blockchain — mining IS relaying | 1 BC block reward |
| **BorderWallet** | Ed25519 keypair, BC addresses, sign/verify | — |
| **BorderCompute** | GPU job marketplace — inference, render, train, custom | 2 BC per GPU-hour |
| **BorderInfer** | Private AI inference — llama3, mistral, phi3, embeddings | 0.0008 BC / 1K tokens |
| **BorderRender** | Image, video, 3D rendering — SDXL, AnimateDiff, FLUX | 0.002 BC per image |
| **BorderStore** | Encrypted decentralised storage with challenge proofs | 0.01 BC / GB / day |
| **BorderID** | Self-sovereign identity, verifiable claims, reputation | — |
| **BorderDAO** | On-chain governance — BC holders vote on protocol changes | — |

---

## Quick Start

### Create a wallet

```python
from border.blockchain import BorderWallet

wallet = BorderWallet.create()
wallet.save("my_wallet.json")
print(f"Address: {wallet.address}")
print(f"Balance: {chain.get_balance(wallet.address)} BC")
```

### Run a relay node (earn BC for forwarding traffic)

```bash
pip install -e .
python examples/run_relay.py --wallet my_wallet.json
```

### Run a GPU compute worker (earn BC per job)

```python
from border.compute import WorkerDaemon
from border.blockchain import BorderWallet

wallet = BorderWallet.load("my_wallet.json")
daemon = WorkerDaemon(wallet=wallet, market_endpoint="http://localhost:8888")
daemon.run()   # auto-detects your GPUs, polls for jobs, earns BC
```

### Run a private AI inference worker (earn BC per token)

```python
from border.infer import InferDaemon, ModelBackend
from border.blockchain import BorderWallet

wallet = BorderWallet.load("my_wallet.json")
# Runs ollama locally, polls market, earns BC for every token generated
daemon = InferDaemon(
    wallet=wallet, market=market,
    model_ids=["llama3:8b", "mistral:7b"],
    total_vram_gb=12.0,
    backend=ModelBackend.OLLAMA,
)
daemon.run()
```

### Run a render worker (earn BC per frame)

```python
from border.render import RenderDaemon, RenderBackend
from border.blockchain import BorderWallet

wallet = BorderWallet.load("my_wallet.json")
daemon = RenderDaemon(
    wallet=wallet, market=market,
    model_ids=["sdxl", "flux-schnell", "animatediff"],
    total_vram_gb=12.0,
    backend=RenderBackend.COMFYUI,
)
daemon.run()
```

### Run a storage node (earn BC per GB stored)

```python
from border.storage import BorderStorageNode

node = BorderStorageNode(
    node_address=wallet.address,
    storage_path="./data",
    capacity_bytes=4 * 1024**3,   # 4 TB
    stake_bc=10.0,
)
node.run(port=6666)
```

---

## Run the demos (no servers needed)

Every module has a self-contained in-process demo:

```bash
# Blockchain + relay
python examples/blockchain_demo.py

# GPU compute marketplace
python examples/compute_demo.py

# Encrypted decentralised storage
python examples/storage_demo.py

# Self-sovereign identity + reputation
python examples/identity_demo.py

# Private AI inference + GPU rendering
python examples/infer_render_demo.py

# Community governance (DAO)
python examples/dao_demo.py
```

---

## Project Structure

```
border/
├── blockchain/        BorderCoin chain, wallet, transactions, P2P node
├── compute/           GPU job marketplace + worker daemon
├── infer/             Private AI inference (ollama / llama.cpp)
├── render/            Image, video, 3D rendering (ComfyUI / A1111)
├── storage/           Encrypted chunked storage + challenge proofs
├── identity/          BorderID — DIDs, claims, reputation engine
├── dao/               Governance — proposals, voting, treasury
├── node.py            Border relay node
├── client.py          Border relay client
├── ledger.py          Bandwidth receipt tracking
├── discovery.py       Node discovery
├── obfuscate.py       X25519 + ChaCha20-Poly1305 obfuscation
└── lora.py            LoRa radio interface (sim + hardware)

examples/
├── blockchain_demo.py     Relay → proofs → block mined
├── compute_demo.py        GPU jobs → BC earned
├── storage_demo.py        Upload → challenge → download
├── identity_demo.py       DIDs → claims → reputation leaderboard
├── infer_render_demo.py   AI tokens + render frames → BC
├── dao_demo.py            Proposals → votes → protocol updated
└── run_relay.py           Production relay node starter
```

---

## BorderCoin Economics

| Action | Reward |
|---|---|
| Forward 1 GB of censored traffic | 1.0 BC |
| Produce a valid block | 1.0 BC |
| Run a GPU job for 1 hour | 2.0 BC |
| Generate 1M AI tokens | ~0.8 BC |
| Render 500 images (SDXL) | ~1.0 BC |
| Store 1 TB for 1 day | ~10.24 BC |
| Answer a storage challenge | 0.0001 BC |

Blocks require ≥100MB of verified bandwidth receipts. You cannot mine BorderCoin without actually helping someone get internet access or providing real compute/storage to the network.

---

## BorderID — Self-Sovereign Identity

Every node gets a DID tied to its wallet:

```
did:border:BC_a1b2c3...
```

Nodes self-attest capabilities (GPU count, region, stake), earn reputation from chain proofs, and can be vouched for by other nodes. High-reputation nodes get priority job routing. No registrar. No central authority.

---

## BorderDAO — Community Governance

BC holders govern the protocol:

- **PARAMETER** proposals — change fees, rewards, thresholds
- **TREASURY** proposals — spend protocol fees on development grants
- **PROTOCOL** proposals — enable new features (e.g. BorderDNS)
- **SLASH** proposals — penalise misbehaving nodes

Voting is stake-weighted (1 BC = 1 vote), 7-day window, 10% quorum required.

---

## Relay Architecture

```
[Censored User]
      ↓
  LoRa Radio          ← no internet required, 2–15km range
      ↓
[Border Bridge Node]
      ↓
  Obfuscated HTTPS    ← looks like normal web traffic to DPI
      ↓
[Border Relay Node]   ← running anywhere with free internet
      ↓
   The Internet
```

Traffic is encrypted with **X25519 + ChaCha20-Poly1305** and disguised as normal HTTPS. Cover domains, random User-Agents, random padding. Deep packet inspection sees nothing suspicious.

For users with no internet at all, a cheap **LoRa radio module** ($15–30) reaches a Border bridge node 2–15km away. No WiFi. No mobile data. Just radio.

---

## Hardware for GPU Workers

Border works with any CUDA or ROCm GPU:

| GPU | VRAM | Best for |
|---|---|---|
| RX 580 8GB | 8 GB | llama3:8b inference, SDXL rendering |
| RTX 3060 12GB | 12 GB | mistral:7b, SDXL, AnimateDiff |
| RTX 3080 12GB | 12 GB | mixtral:8x7b, FLUX, video |
| RTX 4090 24GB | 24 GB | llama3:70b, LoRA training |

The more GPUs, the more BC earned per hour.

---

## Philosophy

Most censorship circumvention tools are charities — they depend on donations that dry up. Border replaces altruism with economics. Forward traffic for censored users and earn money. Run GPU compute and earn money. Store files and earn money. The harder the censorship, the more valuable the network, the more people run nodes.

The "work" in Proof of Bandwidth is real. Someone in Tehran, Beijing, or Havana got to read a news article. That's the block reward justification — not burning electricity on meaningless math.

---

## Contributing

Border needs help with:
- **Real-world relay testing** against live censorship infrastructure
- **Mobile clients** (Android/iOS) for censored users
- **LoRa hardware testing** across different environments
- **Cryptographic audit** of the obfuscation and blockchain
- **Bootstrap nodes** — if you have a server outside a censored region, run one
- **BorderDNS** — on-chain human-readable names (`alice.border`) — coming next

---

## License

MIT — do whatever you want with it. Help people.

---

*Built with the belief that access to information is a human right.*
