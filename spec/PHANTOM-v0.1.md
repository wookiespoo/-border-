# Project Phantom — Protocol Specification v0.1

> Free internet for everyone. Invisible, unblockable, unstoppable.

---

## The Problem

4 billion people live under internet censorship. Governments block VPNs, throttle Tor,
and shut down networks entirely. Existing solutions fail because:

1. They're detectable — traffic patterns reveal what they are
2. They rely on volunteers — no economic incentive means too few relay nodes
3. They don't solve the last mile — people with NO internet can't use software solutions

Phantom solves all three.

---

## Architecture: Three Layers

```
[ Free Internet ]
       ↓
[ Layer 1: Relay Network ]     — Paid relay nodes forward traffic
       ↓
[ Layer 2: Obfuscation ]       — Traffic disguised as normal HTTPS
       ↓
[ Layer 3: LoRa Last Mile ]    — Radio broadcast for the truly disconnected
       ↓
[ Person in censored country ]
```

---

## Layer 1: Relay Network

### Node Types

| Type | Role |
|------|------|
| `RELAY` | Has free internet, forwards traffic, earns BorderCoin |
| `CLIENT` | Wants access, routes requests through relay nodes |
| `BRIDGE` | Special relay near censored borders, also broadcasts via LoRa |
| `DIRECTORY` | Maintains list of active relay nodes |

### Node Identity

Every node has:
- A **keypair** (Ed25519) — identity and bandwidth proof signing
- A **node ID** — SHA256 of public key, first 16 bytes, hex encoded
- A **node card** — public manifest (similar to HAP agent card)

```json
{
  "phantom": "0.1",
  "node_id": "a3f8c21d9e4b7f01",
  "type": "RELAY",
  "endpoint": "https://relay.example.com/phantom",
  "public_key": "base64-encoded-ed25519-pubkey",
  "bandwidth_tier": "high",
  "region": "EU",
  "uptime_score": 0.97,
  "registered_at": "2026-03-21T00:00:00Z"
}
```

### Bandwidth Proof

Every relay signs bandwidth receipts that clients can aggregate and submit for payment:

```json
{
  "relay_id": "a3f8c21d9e4b7f01",
  "client_id": "b7e2a41f8d3c9e02",
  "bytes_forwarded": 1048576,
  "timestamp": "2026-03-21T14:32:00Z",
  "session_id": "sess_abc123",
  "signature": "base64-ed25519-signature"
}
```

---

## Layer 2: Obfuscation

### The Problem with Regular VPNs

Censors don't need to decrypt your traffic. They just need to identify its pattern.
OpenVPN has a recognizable handshake. Tor has recognizable cell sizes.
Deep packet inspection catches them all.

### Phantom Obfuscation

Phantom traffic is designed to be indistinguishable from normal HTTPS web traffic.

**Technique: HTTPS Mimicry**

Every Phantom request is wrapped to look like a standard HTTPS POST to a common endpoint.
The actual destination and payload are encrypted and hidden inside what appears to be
normal web form data or API calls.

**Request wrapper:**
```
POST /api/v1/data HTTP/1.1
Host: [cover-domain]
Content-Type: application/json
User-Agent: Mozilla/5.0 (compatible; normal browser string)
X-Request-ID: [random UUID]

{
  "session": "[base64 encoded encrypted phantom payload]",
  "ts": "[timestamp]",
  "v": "1"
}
```

The cover domain can be any legitimate-looking domain. The encrypted payload inside
contains the real destination URL and request data.

**Encryption:**
- Key exchange: X25519 ECDH
- Symmetric encryption: ChaCha20-Poly1305
- Each session uses ephemeral keys — no two sessions look the same

**Padding:**
- Random padding added to every request/response to defeat size-based fingerprinting
- Timing jitter added to defeat timing analysis

### Traffic Flow

```
CLIENT                          RELAY                        DESTINATION
  |                               |                               |
  |-- obfuscated HTTPS POST ----> |                               |
  |   (looks like normal web req) |                               |
  |                               |-- real HTTP/S request ------> |
  |                               | <-- real response ----------- |
  |                               |                               |
  | <-- obfuscated HTTPS resp --- |                               |
  |   (looks like normal web resp)|                               |
```

---

## Layer 3: LoRa Last Mile

For people with NO internet connection at all.

### Hardware

- **LoRa radio module**: SX1276/SX1278 — costs $5-15
- **Microcontroller**: Raspberry Pi ($35) or ESP32 ($5)
- **Total cost per receiver**: ~$10-40
- **Range**: 2-15km in urban areas, up to 50km line-of-sight

### Bridge Nodes

A Bridge node is a RELAY node that also has LoRa hardware attached.
It sits near a censored border with free internet on one side.
It downloads and rebroadcasts content via LoRa radio into the censored area.

```
[ Internet ] --> [ Bridge node ] --> [ LoRa broadcast ] --> [ $10 receiver ]
```

### LoRa Protocol

**Frequency**: 433MHz (Asia), 868MHz (EU), 915MHz (Americas)
(check local regulations — LoRa is license-free in most countries)

**Packet format** (limited to 255 bytes per LoRa packet):

```
| 1 byte  | 2 bytes    | 2 bytes | 1 byte | up to 249 bytes |
| version | session_id | seq_num | flags  | payload chunk   |
```

Larger content is chunked across multiple LoRa packets and reassembled.

**Content types broadcast:**
- News articles (text, compressed)
- Wikipedia pages
- Medical references
- Cached web pages
- Messages (via delay-tolerant store-and-forward)

### Delay-Tolerant Mode

For areas where even LoRa can't reach:
- Content is cached on bridge nodes
- Physical couriers (people crossing borders) sync cached content to USB drives
- Receivers sync from USB when no radio link is available

---

## BorderCoin — Bandwidth Incentive

### Why crypto?

Tor has 7,000 relay nodes because it relies on volunteers.
Phantom will have millions of relay nodes because it pays them.

### Proof of Bandwidth

The consensus mechanism is **Proof of Bandwidth (PoB)**:

1. Relay forwards traffic for a client
2. Relay generates a signed bandwidth receipt
3. Client countersigns the receipt (confirming they received the data)
4. Both signatures submitted to the blockchain
5. Relay earns BorderCoin proportional to verified bytes forwarded

Unlike Proof of Work (burns energy on math) or Proof of Stake (rewards the rich),
PoB rewards nodes for providing real value: internet access.

### Anti-Gaming

To prevent fake traffic generation:
- Clients must stake a small amount of BorderCoin to make requests
- Relays that consistently serve only one client are flagged
- Traffic diversity requirements: serve at least N different clients per period
- Random audits: directory nodes send test requests to verify relay quality

---

## Discovery

### Directory Nodes

Maintained list of active relay nodes, available via:
- HTTPS (for those with partial internet)
- DNS TXT records (harder to block)
- LoRa broadcast (for those with no internet)
- Hardcoded bootstrap list in the client

### Node Registration

```json
{
  "type": "REGISTER",
  "node_card": { ... },
  "timestamp": "ISO8601",
  "signature": "ed25519-sig-of-node-card"
}
```

### Node Discovery Query

```json
{
  "type": "DISCOVER",
  "region_preference": "EU",
  "max_results": 10,
  "min_uptime": 0.9
}
```

---

## Security Model

| Threat | Mitigation |
|--------|-----------|
| Traffic fingerprinting | HTTPS mimicry + padding + timing jitter |
| Relay node compromise | End-to-end encryption, relay never sees plaintext destination |
| Directory blocking | Multiple directory sources, DNS, LoRa fallback |
| Sybil attack on network | Stake requirement + diversity checks |
| Physical seizure of receiver | No stored keys on receiver by default |
| LoRa jamming | Frequency hopping, multiple bridge nodes |

---

## Design Principles

1. **Assume the adversary controls the network** — design for active censorship
2. **Economic sustainability** — incentives must work without goodwill
3. **Graceful degradation** — works with full internet, partial internet, or no internet
4. **Cheap enough for anyone** — $10 hardware, free software
5. **No single point of failure** — every component is distributed
6. **Open everything** — hardware designs, software, protocol — all public
