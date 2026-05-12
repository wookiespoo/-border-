# 🌐 Project Border

**Free internet for people living under censorship.**

Border is a three-layer system that routes around internet censorship, pays relay operators in cryptocurrency for their bandwidth, and delivers connectivity even where there's no internet at all — via long-range radio.

No company controls it. No server can be shut down. The more people run it, the stronger it gets.

---

## The Problem

Over 4 billion people live under some form of internet censorship. Governments block news, social media, messaging apps, and VPNs. Existing circumvention tools depend on centralized infrastructure that can be targeted and taken down.

Border fixes this by making the relay network **economically self-sustaining** and **technically invisible**.

---

## How It Works

```
[Censored User]
      ↓
  LoRa Radio          ← no internet required, 2–15km range
      ↓
[Border Bridge Node]
      ↓
  Obfuscated HTTPS    ← looks like normal web traffic
      ↓
[Border Relay Node]   ← running anywhere with free internet
      ↓
   The Internet
```

### Layer 1 — Obfuscated Relay
Traffic is encrypted with **X25519 + ChaCha20-Poly1305** and disguised as normal HTTPS requests. Every packet looks like a call to a generic API. Cover domains, random User-Agents, random padding — deep packet inspection sees nothing suspicious.

### Layer 2 — LoRa Radio Last-Mile
For users with no internet at all, a cheap **LoRa radio module** ($15–30) can reach a Border bridge node 2–15km away. No WiFi, no mobile data, no infrastructure needed. Just radio waves.

### Layer 3 — BorderCoin (Proof of Bandwidth)
Relay operators earn **BorderCoin (BC)** for every byte they forward. Instead of wasting electricity on hash puzzles like Bitcoin, BorderCoin mining **IS** the act of giving people internet access.

- **1 BC per GB forwarded**
- **1 BC block reward** per block produced
- **100MB minimum** bandwidth required per block
- **Ed25519** signed receipts — unforgeable proof of work done

---

## Quick Start

### Run a relay node (earn BorderCoin)

```bash
pip install -e .

python -c "
from phantom.blockchain import BorderWallet
wallet = BorderWallet.create()
wallet.save('my_wallet.json')
print('Wallet:', wallet.address)
"

python examples/run_relay.py --wallet my_wallet.json --chain http://localhost:7777
```

### Run the blockchain node

```bash
python -c "
from phantom.blockchain import BorderWallet, BorderChainNode
wallet = BorderWallet.load('my_wallet.json')
node = BorderChainNode(wallet=wallet, port=7777)
node.run()
"
```

### See it all working end-to-end

```bash
python examples/blockchain_demo.py
```

This runs a full simulation: censored users get internet → relay earns BorderCoin → block mined → balances updated. No servers needed.

---

## Project Structure

```
phantom/
├── obfuscate.py       X25519 key exchange + ChaCha20 encryption
├── node.py            Relay node (earns BC automatically)
├── client.py          Client that connects through a relay
├── ledger.py          Bandwidth receipt tracking
├── discovery.py       Relay node discovery
├── lora.py            LoRa radio interface (sim + real hardware)
└── blockchain/
    ├── wallet.py      Ed25519 keypair, BC_ addresses
    ├── transaction.py Signed transfers + coinbase rewards
    ├── block.py       BandwidthProof, Block structure
    ├── chain.py       Full blockchain with PoB consensus
    └── node.py        P2P network node (mine/broadcast/sync)

examples/
├── blockchain_demo.py Full end-to-end demo (no servers needed)
├── run_relay.py       Start a relay and earn BorderCoin
└── demo.py            Three-layer system demo
```

---

## BorderCoin Economics

| Event | Reward |
|---|---|
| Forwarding 1 GB of traffic | 1.0 BC |
| Producing a valid block | 1.0 BC |
| Transaction fee | 0.001 BC (goes to miner) |

Blocks require ≥100MB of verified bandwidth receipts. There is no other way to mine. You cannot earn BorderCoin without actually helping someone get internet access.

---

## Hardware (LoRa Last-Mile)

For the radio layer you need:
- **Raspberry Pi** (any model) or similar single-board computer
- **SX1276/SX1278 LoRa HAT** (~$20 on Amazon/AliExpress)
- **868MHz or 915MHz antenna**

The software runs in simulation mode by default so you can develop and test without hardware.

---

## Philosophy

Most censorship circumvention tools are charities — they depend on volunteers and donations that can dry up. Border replaces altruism with economics. If you forward traffic for censored users, you earn money. The harder the censorship, the more valuable the relay network, the more people run nodes.

The "work" in Proof of Bandwidth is real. Someone in Tehran, Beijing, or Havana got to read a news article. That's the mining reward justification — not burning electricity on meaningless math.

---

## Contributing

Border is open source and needs help with:
- **Real-world testing** of the obfuscation layer against actual censorship infrastructure
- **Mobile clients** (Android/iOS) for the censored-user side
- **LoRa hardware testing** across different environments and distances
- **Cryptographic review** of the obfuscation and blockchain code
- **Bootstrap relay nodes** — if you have a server with free internet outside a censored region, run one

---

## License

MIT — do whatever you want with it. Help people.

---

*Built with the belief that access to information is a human right.*
