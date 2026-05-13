# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| `main`  | ✅ Yes    |
| Older branches | ❌ No |

Only the `main` branch receives security patches. Pin to a specific commit SHA for production deployments and monitor this file for advisories.

---

## Reporting a Vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Send a report to **security@border-protocol.dev** (PGP key available on request) with:

- A clear description of the vulnerability
- Steps to reproduce or a minimal proof-of-concept
- Affected component(s) and versions
- Your assessment of severity and exploitability

You will receive an acknowledgement within **48 hours** and a patch timeline within **7 days**. We will credit you in the advisory unless you request anonymity.

---

## Security Audit — May 2026

An internal security review of the Border codebase was completed on 2026-05-13. The following findings were identified and resolved before public release.

### Critical

| ID | Component | Finding | Status |
|----|-----------|---------|--------|
| C1 | `blockchain/block.py` | `BandwidthProof` accepted any `relay_signature` string without cryptographic verification. A malicious node could forge proofs and claim unearned block rewards. | **Fixed** — `verify_signature()` added; enforces Ed25519 signature over canonical content hash and binds public key to relay address via SHA-256 derivation. |
| C2 | `blockchain/chain.py` | `_validate_block()` did not verify bandwidth proof signatures or check for double-spent proof receipts. Both omissions could be exploited to inflate block rewards. | **Fixed** — block validation now calls `proof.verify_signature()` on every bandwidth proof and rejects any `receipt_id` already recorded in `_spent_receipts`. |
| C3 | `dao/governance.py` | `PARAMETER` governance proposals accepted arbitrary key names, allowing an attacker with a governance majority to inject unknown parameters into chain state. | **Fixed** — `ALLOWED_PARAMETER_KEYS` whitelist introduced; proposals targeting any unlisted key are rejected at submission time. |

### High

| ID | Component | Finding | Status |
|----|-----------|---------|--------|
| H1 | `dao/governance.py` | `SLASH` governance proposals executed without invoking `chain.slash()`, making the slashing mechanism effectively a no-op. | **Fixed** — `GovernanceEngine` now accepts an optional `chain` reference; approved SLASH proposals call `chain.slash()`. |
| H2 | `blockchain/chain.py` | `BorderChain` had no thread-safety on shared mutable state (`_chain`, `_mempool`, `_balance_cache`). Concurrent Flask requests could cause races leading to balance corruption. | **Fixed** — `threading.RLock` wraps all public mutating methods (`add_transaction`, `add_block`, `stake`, `unstake`, `slash`). |
| H3 | `node_runner.py` | The `/chain/mine` endpoint was publicly accessible, allowing any peer to trigger mining on a remote node (CPU exhaustion / block spam). | **Fixed** — endpoint returns HTTP 403 for any caller whose `remote_addr` is not `127.0.0.1`, `::1`, or `localhost`. |
| H4 | `ledger.py` | `BandwidthLedger._sign()` produced SHA-256 hashes rather than Ed25519 signatures, making ledger entries unforgeable only by obscurity. | **Fixed** — `BandwidthLedger` now accepts an optional `wallet` parameter; if provided, `_sign()` produces a real Ed25519 signature. A deprecation warning is emitted when falling back to the hash-only path. |

### Medium

| ID | Component | Finding | Status |
|----|-----------|---------|--------|
| M1 | `payments/manager.py` | `settle()` created settlement transactions from the sender's wallet address rather than the channel escrow address. This double-charged the sender's on-chain balance. | **Fixed** — settlement transactions now originate from the symbolic escrow address (`channel_<receiver[:8]>`), consistent with the lock transaction created at channel open. |
| M2 | `payments/manager.py` | The receiver-side stale nonce check compared `receipt.nonce` against the channel's *sent* nonce rather than an independently tracked *received* nonce, causing all valid receipts to be rejected after the first send. | **Fixed** — `_received_nonces` dict tracks the last ACK'd nonce separately from the channel's sent nonce. |
| M3 | `p2p/server.py` | Query parameters (`from_port`, `index`, `start`, `end`) were cast to `int` without exception handling, allowing a malformed request to crash the P2P server process. | **Fixed** — all integer query parameters are wrapped in `try/except`; malformed values return HTTP 400. |

### Low

| ID | Component | Finding | Status |
|----|-----------|---------|--------|
| L1 | `blockchain/wallet.py` | `Wallet.save()` writes the private key unencrypted when no password is supplied. | **Mitigated** — password-based encryption (PBKDF2-HMAC-SHA256 + AES-GCM) was added in a prior audit round. The no-password path now emits a deprecation warning. A future release will make the password mandatory. |
| L2 | `relay.py` | Relay log messages included full client IP addresses, which could leak user metadata if logs were captured. | **Advisory** — log messages now truncate peer identifiers to the first 12 characters. Full identifiers remain in debug-level logs only. |

---

## Cryptographic Primitives

Border uses the following cryptographic primitives:

| Purpose | Algorithm |
|---------|-----------|
| Node / wallet keys | Ed25519 (via `cryptography` library) |
| Transaction signing | Ed25519 |
| Bandwidth proof signing | Ed25519 |
| Storage proof signing | Ed25519 |
| Traffic obfuscation key derivation | HKDF-SHA256 |
| Traffic encryption | ChaCha20-Poly1305 |
| Wallet file encryption | PBKDF2-HMAC-SHA256 + AES-256-GCM |
| Block hashing / PoW | SHA-256 |

---

## Scope

The following components are in-scope for security reports:

- `border/blockchain/` — chain, wallet, transactions, proofs
- `border/payments/` — payment channels and receipts
- `border/relay.py`, `border/obfuscate.py` — traffic obfuscation
- `border/node_runner.py`, `border/p2p/` — node API and P2P layer
- `border/dao/` — governance and parameter control
- `border/staking.py` — stake and slash logic

Out of scope: example scripts, documentation, test fixtures.
