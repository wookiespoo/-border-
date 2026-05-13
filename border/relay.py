"""
border.relay — Proof-of-Bandwidth relay layer.

Wraps BorderObfuscator with byte accounting and automatic BandwidthProof
generation. When a session closes (or exceeds PROOF_FLUSH_BYTES), a signed
BandwidthProof is pushed onto a queue that the BorderNode mining daemon drains.

Architecture
------------
  BorderRelay          -- per-node singleton; owns the proof queue + mining loop
    .wrap_request()    -- measure bytes, forward to obfuscator
    .unwrap_request()  -- measure bytes, forward to obfuscator
    .close_session()   -- finalise proof for that session
    .start()           -- launch background mining daemon thread
    .stop()

  The BorderNode (node_runner.py) creates one BorderRelay, passes it the chain
  and wallet, and calls relay.start().  The relay's HTTP helpers are exposed
  under /relay/* by the Flask app.

Proof generation
----------------
  A BandwidthProof is emitted when:
    - A session is explicitly closed  (close_session)
    - A session exceeds PROOF_FLUSH_BYTES while still open
    - The flush_interval timer fires (covers long-lived sessions)

Mining daemon
-------------
  A background thread wakes every MINE_INTERVAL seconds, checks whether the
  pending proof pool has crossed MIN_BYTES_PER_BLOCK, and if so calls
  chain.create_block() + chain.add_block().  The mined block is broadcast via
  p2p.broadcast_block() if a P2PNode reference is provided.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Dict, List, Optional, TYPE_CHECKING

from .obfuscate import BorderObfuscator, BorderSession
from .blockchain.block import BandwidthProof

if TYPE_CHECKING:
    from .blockchain.chain import BorderChain
    from .blockchain.wallet import BorderWallet
    from .p2p.node import P2PNode

logger = logging.getLogger("border.relay")

# Emit a proof once a session has forwarded this many bytes
PROOF_FLUSH_BYTES = 10 * 1024 * 1024       # 10 MB

# Flush any open sessions on this interval even if below threshold
FLUSH_INTERVAL_SEC = 120                    # 2 minutes

# Mining daemon wake interval
MINE_INTERVAL_SEC = 30


class _SessionStats:
    """Byte counters and metadata for one relay session."""
    def __init__(self, session: BorderSession, relay_address: str):
        self.session       = session
        self.session_id    = session.session_id
        self.relay_address = relay_address
        self.client_id     = session.session_id
        self.bytes_in      = 0
        self.bytes_out     = 0
        self.started_at    = time.time()
        self.last_flush    = time.time()
        self.proof_count   = 0

    @property
    def total_bytes(self) -> int:
        return self.bytes_in + self.bytes_out

    @property
    def unflushed_bytes(self) -> int:
        return self.total_bytes - self._flushed_bytes

    def mark_flush(self, flushed: int) -> None:
        self._flushed_bytes = flushed
        self.last_flush = time.time()
        self.proof_count += 1

    # Internal tracker
    _flushed_bytes: int = 0


class BorderRelay:
    """
    Proof-of-Bandwidth relay node.

    Parameters
    ----------
    wallet        : Signing wallet for proof generation
    chain         : BorderChain to submit proofs and mine blocks
    p2p           : Optional P2PNode for broadcasting mined blocks
    mine_interval : Seconds between mining attempts (default 30)
    """

    def __init__(
        self,
        wallet: "BorderWallet",
        chain: "BorderChain",
        p2p: Optional["P2PNode"] = None,
        mine_interval: int = MINE_INTERVAL_SEC,
    ):
        self.wallet        = wallet
        self.chain         = chain
        self.p2p           = p2p
        self.mine_interval = mine_interval
        self._obfuscator   = BorderObfuscator()
        self._sessions: Dict[str, _SessionStats] = {}
        self._proof_queue: queue.Queue[BandwidthProof] = queue.Queue()
        self._running      = False
        self._lock         = threading.Lock()

    # -------------------------------------------------------------------
    # Session management
    # -------------------------------------------------------------------

    def open_session(self) -> BorderSession:
        """Create a new relay session and start tracking its bytes."""
        session = BorderSession.create()
        stats   = _SessionStats(session, relay_address=self.wallet.address)
        with self._lock:
            self._sessions[session.session_id] = stats
        logger.debug(f"[Relay] Session opened: {session.session_id}")
        return session

    def close_session(self, session_id: str) -> None:
        """Finalise a session and emit a proof for any un-flushed bytes."""
        with self._lock:
            stats = self._sessions.pop(session_id, None)
        if stats and stats.unflushed_bytes > 0:
            self._emit_proof(stats, stats.unflushed_bytes)
            logger.info(f"[Relay] Session closed: {session_id}  "
                        f"total={stats.total_bytes/1024:.1f}KB")

    # -------------------------------------------------------------------
    # Wrap / unwrap (byte-metered)
    # -------------------------------------------------------------------

    def wrap_request(self, payload: dict, session: BorderSession) -> dict:
        """Obfuscate an outbound request and record bytes sent."""
        envelope = self._obfuscator.wrap_request(payload, session)
        size     = len(str(envelope).encode())
        self._record_bytes(session.session_id, sent=size)
        return envelope

    def unwrap_request(self, envelope: dict, session: BorderSession) -> dict:
        """Unwrap an inbound request and record bytes received."""
        size    = len(str(envelope).encode())
        self._record_bytes(session.session_id, received=size)
        return self._obfuscator.unwrap_request(envelope, session)

    def wrap_response(self, payload: dict, session: BorderSession) -> dict:
        """Obfuscate a response."""
        envelope = self._obfuscator.wrap_response(payload, session)
        size     = len(str(envelope).encode())
        self._record_bytes(session.session_id, sent=size)
        return envelope

    def unwrap_response(self, envelope: dict, session: BorderSession) -> dict:
        """Unwrap a response."""
        size    = len(str(envelope).encode())
        self._record_bytes(session.session_id, received=size)
        return self._obfuscator.unwrap_response(envelope, session)

    def get_cover_headers(self) -> dict:
        return self._obfuscator.get_cover_headers()

    # -------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        threading.Thread(
            target=self._flush_loop, daemon=True, name="relay-flush"
        ).start()
        threading.Thread(
            target=self._mine_loop, daemon=True, name="relay-mine"
        ).start()
        logger.info("[Relay] Started  wallet=%s", self.wallet.address[:20])

    def stop(self) -> None:
        self._running = False

    def stats(self) -> dict:
        with self._lock:
            sessions = len(self._sessions)
            total_bytes = sum(s.total_bytes for s in self._sessions.values())
        return {
            "open_sessions": sessions,
            "pending_bytes": total_bytes,
            "proof_queue":   self._proof_queue.qsize(),
            "chain_height":  self.chain.height,
        }

    # -------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------

    def _record_bytes(self, session_id: str,
                      sent: int = 0, received: int = 0) -> None:
        with self._lock:
            stats = self._sessions.get(session_id)
            if not stats:
                return
            stats.bytes_out += sent
            stats.bytes_in  += received
            unflushed = stats.unflushed_bytes

        if unflushed >= PROOF_FLUSH_BYTES:
            with self._lock:
                stats = self._sessions.get(session_id)
                if stats:
                    self._emit_proof(stats, unflushed)

    def _emit_proof(self, stats: _SessionStats, byte_count: int) -> None:
        """Build, sign, and enqueue a BandwidthProof."""
        import uuid as _uuid
        receipt_id = f"rcpt_{stats.session_id}_{stats.proof_count}_{_uuid.uuid4().hex[:8]}"
        ts = time.time()
        proof = BandwidthProof(
            receipt_id       = receipt_id,
            relay_address    = stats.relay_address,
            client_id        = stats.client_id,
            bytes_forwarded  = byte_count,
            timestamp        = ts,
            session_id       = stats.session_id,
            relay_public_key = self.wallet.public_key_b64,
            relay_signature  = "",   # filled below after hash is stable
        )
        # Sign the canonical hash (relay_address:client_id:bytes:timestamp)
        proof.relay_signature = self.wallet.sign(proof.hash().encode())
        stats.mark_flush(stats.total_bytes)
        self._proof_queue.put(proof)
        # Submit to chain immediately
        self.chain.add_proof(proof)
        logger.info(
            f"[Relay] Proof emitted  bytes={byte_count/1024:.1f}KB  "
            f"receipt={receipt_id[:20]}  queue={self._proof_queue.qsize()}"
        )

    def _flush_loop(self) -> None:
        """Periodically flush long-lived open sessions."""
        while self._running:
            time.sleep(FLUSH_INTERVAL_SEC)
            now = time.time()
            with self._lock:
                to_flush = [
                    s for s in self._sessions.values()
                    if s.unflushed_bytes > 0 and
                       (now - s.last_flush) >= FLUSH_INTERVAL_SEC
                ]
            for stats in to_flush:
                logger.debug(f"[Relay] Interval flush: {stats.session_id}")
                self._emit_proof(stats, stats.unflushed_bytes)

    def _mine_loop(self) -> None:
        """
        Background mining daemon.

        Wakes every mine_interval seconds.  Attempts to mine a block if the
        chain has accumulated enough bandwidth proofs.  Broadcasts the block
        to peers via P2PNode if one is configured.
        """
        logger.info("[Relay] Mining daemon started")
        while self._running:
            time.sleep(self.mine_interval)
            try:
                block = self.chain.create_block(
                    miner_address=self.wallet.address
                )
                if block is None:
                    pending_mb = self.chain.pending_bandwidth_mb
                    import border.blockchain.block as _bm
                    needed_mb  = _bm.MIN_BYTES_PER_BLOCK / (1024 * 1024)
                    logger.debug(
                        f"[Relay] Not enough proofs to mine: "
                        f"{pending_mb:.1f}MB / {needed_mb:.0f}MB needed"
                    )
                    continue

                ok, reason = self.chain.add_block(block)
                if ok:
                    logger.info(
                        f"[Relay] Mined block #{block.index}  "
                        f"reward={block.transactions[0].amount:.4f} BC  "
                        f"hash={block.block_hash[:14]}..."
                    )
                    if self.p2p:
                        self.p2p.broadcast_block(block)
                else:
                    logger.warning(f"[Relay] Block rejected: {reason}")

            except Exception as e:
                logger.warning(f"[Relay] Mine loop error: {e}")
