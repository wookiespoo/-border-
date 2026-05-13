# Border Protocol: A Censorship-Resistant Mesh Network with Proof-of-Bandwidth Consensus

**Version 1.0 — May 2026**

---

## Abstract

Border is a decentralized, censorship-resistant mesh network protocol that enables private communication, distributed compute, and peer-to-peer storage through a permissionless node network. Border combines a novel **Proof-of-Bandwidth (PoB)** consensus mechanism with off-chain payment channels, a staking/slashing governance system, and LoRa radio last-mile connectivity to operate in environments where conventional internet access is restricted or surveilled. This paper describes the protocol architecture, economic model, cryptographic design, threat model, and roadmap.

---

## 1. Introduction

Censorship of internet communications remains a persistent and growing problem. Governments and network operators increasingly deploy deep packet inspection (DPI), DNS poisoning, BGP hijacking, and application-layer blocking to restrict access to information. Existing circumvention tools—VPNs, Tor, I2P—provide partial solutions but suffer from centralized trust assumptions, poor performance incentives, or limited adoption.

Border addresses these shortcomings through three design principles:

1. **Incentivized relay**: Nodes earn BC tokens for forwarding traffic, removing the free-rider problem that limits volunteer-based networks.
2. **Cryptographic proof of work**: Bandwidth forwarding is verified on-chain via signed proofs, creating an auditable ledger of relay contributions.
3. **Last-mile resilience**: LoRa radio integration enables operation in environments with no internet infrastructure.

---

## 2. System Architecture

### 2.1 Node Types

Border nodes operate in one or more of five roles:

| Role | Function | Stake Requirement |
|------|----------|-------------------|
| **RELAY** | Forwards obfuscated traffic between clients and destinations | 1.0 BC |
| **COMPUTE** | Executes inference and rendering jobs | 5.0 BC |
| **STORAGE** | Stores and serves encrypted file chunks | 2.0 BC |
| **INFER** | Specialized AI inference node | 5.0 BC |
| **FULL** | All roles combined | 10.0 BC |

Nodes stake BC to participate. Stake signals economic commitment and is subject to slashing for provable misbehavior.

### 2.2 Network Layers

```
┌─────────────────────────────────────────────┐
│            Application Layer                │
│  (Compute Market / Storage Market / DNS)    │
├─────────────────────────────────────────────┤
│            Payment Layer                    │
│   (Off-chain payment channels — Lightning)  │
├─────────────────────────────────────────────┤
│            Relay Layer                      │
│  (ChaCha20-Poly1305 obfuscation + HKDF)    │
├─────────────────────────────────────────────┤
│            Blockchain Layer                 │
│  (PoB consensus — Ed25519 — SQLite store)  │
├─────────────────────────────────────────────┤
│            P2P Layer                        │
│  (Gossip protocol — peer discovery)         │
├──────────────────────────┬──────────────────┤
│       TCP/IP             │    LoRa Radio    │
└──────────────────────────┴──────────────────┘
```

### 2.3 LoRa Last-Mile

Border nodes optionally operate a LoRa radio transceiver (868 MHz EU / 915 MHz US) alongside their internet-connected interface. Compressed protocol messages—peer announcements, block headers, payment receipts—are broadcast over LoRa, enabling Border to function as a mesh network in environments with intermittent or no internet access. LoRa messages are limited to 255 bytes and prioritized for control-plane traffic.

---

## 3. Proof-of-Bandwidth Consensus

### 3.1 Overview

Traditional proof-of-work consensus burns energy with no productive output. Border's Proof-of-Bandwidth (PoB) mechanism requires miners to demonstrate *useful work*—forwarding real user traffic—before producing a block.

### 3.2 BandwidthProof

Each relay session generates a `BandwidthProof`:

```
BandwidthProof {
  receipt_id:       UUID (unique per session)
  relay_address:    BC_<sha256(pubkey)[:32]>
  client_id:        anonymized client identifier
  bytes_forwarded:  uint64
  timestamp:        float (Unix)
  session_id:       UUID
  relay_signature:  Ed25519(relay_privkey, hash(relay_address:client_id:bytes:timestamp))
  relay_public_key: base64(Ed25519_pubkey)
}
```

The relay signs the proof with its Ed25519 private key. The public key is included in the proof; the blockchain verifies that the public key derives to the claimed `relay_address` via:

```
derived_address = "BC_" + SHA256(pubkey_bytes)[:32]
assert derived_address == relay_address
```

This binding prevents key substitution attacks: a malicious actor cannot use someone else's relay address with their own key.

### 3.3 Block Validity

A block is valid only if:

1. All `BandwidthProof` signatures verify against their embedded public keys.
2. The total `bytes_forwarded` across proofs meets the network minimum (`MIN_BYTES_PER_BLOCK`, default 100 MB on mainnet, 1 MB on testnet).
3. No `receipt_id` in the block has been spent in a prior block (`_spent_receipts` set).
4. The block hash satisfies the current difficulty target (leading zero bits).
5. `previous_hash` chains correctly to the tip.

### 3.4 Double-Spend Prevention

Each `receipt_id` is a UUID generated per relay session. Once included in a confirmed block, it is added to the chain's `_spent_receipts` set. Attempts to include the same receipt in a subsequent block are rejected at validation time.

### 3.5 Difficulty Adjustment

Difficulty adjusts every 2016 blocks (mirroring Bitcoin) targeting a 10-minute average block time. The adjustment multiplier is clamped to [0.25, 4.0] to prevent oscillation.

---

## 4. Token Economics

### 4.1 Supply Schedule

| Parameter | Value |
|-----------|-------|
| Token symbol | BC |
| Maximum supply | 21,000,000 BC |
| Genesis block reward | 50 BC |
| Halving interval | 210,000 blocks |
| Target block time | 600 seconds |
| Transaction fee floor | 0.0001 BC |

At 10-minute blocks, the full supply is issued over approximately 200 years, following a deflationary schedule identical to Bitcoin's.

### 4.2 Reward Distribution

Block rewards are paid to the miner address in the coinbase transaction. The miner is also credited with bandwidth proof rewards proportional to bytes forwarded:

```
bandwidth_bonus = bytes_forwarded / 1e9 * BC_PER_GB   # BC_PER_GB = 0.1
```

Transaction fees from all mempool transactions included in the block are added to the miner's reward.

### 4.3 Staking

Nodes must stake BC to participate in each role. Staked BC is locked (excluded from spendable balance) but earns participation rights:

- **RELAY** nodes receive bandwidth rewards from their proofs.
- **COMPUTE** nodes receive `bc_per_compute_hour` per confirmed proof.
- **STORAGE** nodes receive `bc_per_gb_per_day` per confirmed storage proof.

Staked BC earns no passive yield — rewards come only from demonstrated work.

### 4.4 Slashing

Nodes caught misbehaving (invalid proofs, double-spend attempts, governance violations) can be slashed via on-chain DAO vote or automatic protocol enforcement:

- Automatic: chain rejects blocks with invalid proofs; stake is not reduced automatically.
- Governance SLASH: a proposal specifying address + slash_amount is passed by DAO majority; upon execution, `chain.slash(address, amount)` reduces the node's stake.

---

## 5. Off-Chain Payment Channels

### 5.1 Motivation

On-chain transactions (BC blockchain) have a 10-minute confirmation time, unsuitable for streaming micro-payments (e.g., per-megabyte bandwidth charges). Border implements Lightning-style unidirectional payment channels.

### 5.2 Channel Lifecycle

```
open_channel(sender, receiver, deposit)
  → escrow TX on-chain (deposit + 0.01 BC anti-spam fee)
  → PaymentChannel created (state: OPEN)

send(channel_id, increment, sender_wallet)
  → PaymentReceipt signed by sender (cumulative amount, nonce++)
  → receipt delivered to receiver off-chain

receive(receipt, receiver_wallet, sender_pubkey)
  → validates sender Ed25519 signature
  → countersigns (ack_signature)
  → nonce tracked to prevent replay

close(channel_id, wallet)
  → state: CLOSING (either party)

settle(channel_id, sender_wallet)
  → TX: escrow → receiver (cumulative_paid)
  → TX: escrow → sender (deposit - cumulative_paid)
  → state: CLOSED
```

### 5.3 Payment Receipt

Each receipt commits to a monotonically increasing nonce and cumulative amount:

```
signing_bytes = f"{channel_id}:{nonce}:{amount:.8f}"
sender_signature = Ed25519(sender_privkey, signing_bytes)
```

Tampering with `amount` or `nonce` invalidates the signature. The receiver's countersignature (`ack_signature`) creates a mutual acknowledgement suitable for dispute resolution.

---

## 6. Traffic Obfuscation

### 6.1 Design Goals

Border traffic must be indistinguishable from random bytes to a passive observer performing deep packet inspection.

### 6.2 Cryptographic Construction

1. **Key derivation**: `HKDF-SHA256(input_key_material, salt=os.urandom(32), info="border-relay-v1")` produces a 32-byte session key.
2. **Encryption**: `ChaCha20-Poly1305` encrypts payload with authenticated additional data (AAD) containing a session token.
3. **Padding**: Payloads are padded to a random multiple of 256 bytes to resist traffic analysis.
4. **Protocol camouflage**: Obfuscated packets mimic TLS 1.3 record structure (length prefix, random-looking payload) to pass basic protocol fingerprinting.

---

## 7. Identity and Governance

### 7.1 Decentralized Identifiers (DIDs)

Each Border node generates a `BorderDID` derived from its Ed25519 public key:

```
did:border:<sha256(pubkey)[:32]>
```

DIDs support verifiable claims (signed attestations about the node's capabilities or reputation) and form the basis of the identity registry.

### 7.2 DAO Governance

Protocol parameters are governed by on-chain DAO proposals. Proposal types:

| Type | Effect |
|------|--------|
| PARAMETER | Update a whitelisted protocol parameter |
| SLASH | Reduce a node's stake |
| UPGRADE | Signal protocol upgrade readiness |
| CUSTOM | Arbitrary off-chain signal |

Proposals require a minimum quorum of staked BC to pass. `PARAMETER` proposals are restricted to a whitelist of safe keys (`tx_fee_pct`, `block_reward`, `min_stake_to_work`, etc.) to prevent governance attacks.

### 7.3 DNS

Border includes a decentralized DNS registry (`BorderDNS`) where nodes can register `.border` domain names backed by Ed25519-signed records. Transfers require the current owner's signature. This enables human-readable addressing without a central registrar.

---

## 8. Threat Model

### 8.1 Assumptions

- **Adversary**: A nation-state or ISP capable of monitoring all internet traffic, blocking IP ranges, and injecting malicious peers.
- **Honest majority**: We assume > 50% of staked BC is controlled by honest nodes.
- **Cryptographic assumptions**: Ed25519 security, ChaCha20-Poly1305 IND-CCA2 security, SHA-256 collision resistance.

### 8.2 Attacks and Mitigations

| Attack | Mitigation |
|--------|------------|
| Sybil attack | Staking requirement raises cost of creating many identities |
| Fake bandwidth proofs | Ed25519 signature + address binding + double-spend check |
| Eclipse attack | Diverse peer discovery; LoRa fallback for peer gossip |
| Traffic analysis | Padding to fixed block sizes; ChaCha20-Poly1305 encryption |
| DPI fingerprinting | TLS-mimicking packet structure; random-looking ciphertext |
| Governance attack | Quorum requirements; parameter whitelist; time-locked execution |
| Double-spend (TX) | Mempool deduplication; `tx_id` in spent set after confirmation |
| Double-spend (proof) | `receipt_id` in `_spent_receipts` across all blocks |
| Mining reward inflation | Strict BW proof validation before block acceptance |
| Key substitution | `relay_public_key → relay_address` derivation checked at block validation |

### 8.3 Limitations

- **Correlation attacks**: A global passive adversary observing ingress and egress traffic can correlate sessions with sufficient data. Onion routing (future work) would mitigate this.
- **Proof of bandwidth quality**: The current protocol proves *quantity* of bytes forwarded, not *quality* (latency, packet loss). A future proof-of-QoS extension is planned.
- **LoRa bandwidth**: LoRa data rates (0.3–50 kbps) limit its use to control-plane messages; user data traffic still requires an internet path.

---

## 9. Implementation

### 9.1 Reference Implementation

The Border reference implementation is written in Python 3.11+ and licensed under MIT. Key modules:

| Module | Description |
|--------|-------------|
| `border.blockchain` | Chain, blocks, transactions, wallet, SQLite store |
| `border.relay` | Obfuscated relay sessions, bandwidth proofs |
| `border.payments` | Off-chain payment channels |
| `border.compute` | Distributed compute market |
| `border.storage` | Encrypted distributed storage |
| `border.identity` | DIDs and verifiable claims |
| `border.dao` | Governance proposals and voting |
| `border.dns` | Decentralized DNS registry |
| `border.lora` | LoRa radio broadcaster |

### 9.2 Performance Characteristics

| Operation | Complexity | Notes |
|-----------|------------|-------|
| Block append (SQLite) | O(1) | Single INSERT, WAL mode |
| Chain load at startup | O(n) | Full table scan once |
| Balance lookup | O(1) | In-memory cache |
| TX validation | O(1) | Mempool + spent set lookup |
| BW proof validation | O(k) | k = proofs per block |
| Payment channel send | O(1) | Off-chain, no disk I/O |

### 9.3 Testnet

A public testnet (`border-testnet-1`, chain ID 1337) runs with relaxed parameters:

- `MIN_BYTES_PER_BLOCK`: 1 MB (vs 100 MB mainnet)
- Block reward: 50 BC (same schedule)
- Seed nodes: `seed1.testnet.border.network:9000`, `seed2.testnet.border.network:9000`
- Faucet: `POST /faucet/drip {"address":"BC_..."}` → 10 BC per request (1h/IP cooldown)

---

## 10. Roadmap

| Milestone | Description | Status |
|-----------|-------------|--------|
| v0.1 | Core relay + PoB blockchain | ✅ Complete |
| v0.2 | Compute + storage markets | ✅ Complete |
| v0.3 | Identity + DAO governance | ✅ Complete |
| v0.4 | Payment channels + LoRa | ✅ Complete |
| v0.5 | Security audit + CI | ✅ Complete |
| v0.6 | Testnet launch + explorer | ✅ Complete |
| v0.7 | Script VM (multi-sig, escrow) | 🔄 In progress |
| v0.8 | Onion routing (3-hop) | Planned |
| v0.9 | Mobile light client | Planned |
| v1.0 | Mainnet genesis | Planned |

---

## 11. Conclusion

Border demonstrates that censorship-resistant networking can be economically self-sustaining. By aligning incentives—relay operators earn BC for forwarding traffic, miners earn BC for producing valid blocks, stakers earn participation rights by locking capital—Border avoids the tragedy of the commons that limits volunteer-based anonymity networks.

The Proof-of-Bandwidth mechanism provides a novel alternative to proof-of-work that burns energy on useful network infrastructure rather than hash computation. Combined with off-chain payment channels for streaming micro-payments, LoRa radio for last-mile connectivity, and on-chain governance for protocol evolution, Border is designed to be a durable foundation for censorship-resistant communication.

---

## References

1. Nakamoto, S. (2008). *Bitcoin: A Peer-to-Peer Electronic Cash System.*
2. Poon, J. & Dryja, T. (2016). *The Bitcoin Lightning Network.*
3. Dingledine, R., Mathewson, N. & Syverson, P. (2004). *Tor: The Second-Generation Onion Router.*
4. Bernstein, D.J. (2008). *ChaCha, a variant of Salsa20.*
5. Josefsson, S. & Liusvaara, I. (2017). *RFC 8032: Edwards-Curve Digital Signature Algorithm (EdDSA).*
6. Krawczyk, H. & Eronen, P. (2010). *RFC 5869: HMAC-based Extract-and-Expand Key Derivation Function (HKDF).*

---

*Border Protocol is open source. Source code, specifications, and contribution guidelines are available at https://github.com/wookiespoo/-border-*
