"""Tests for P2P gossip router and peer dataclass."""
import time
import pytest
from border.p2p.gossip import GossipRouter
from border.p2p.peer import Peer, PeerState


def make_router():
    """GossipRouter(self_host, self_port, get_peers)."""
    return GossipRouter(
        self_host="127.0.0.1",
        self_port=9001,
        get_peers=lambda: [],
    )


def make_envelope(router, msg_type, payload, ttl=3):
    """Build a fresh envelope that hasn't been seen before."""
    import hashlib, json, uuid
    unique_payload = dict(payload, _nonce=uuid.uuid4().hex)
    raw = msg_type.encode() + json.dumps(unique_payload, sort_keys=True).encode()
    msg_id = hashlib.sha256(raw).hexdigest()[:24]
    return {
        "msg_id":   msg_id,
        "msg_type": msg_type,
        "ttl":      ttl,
        "origin":   "127.0.0.1:9002",
        "payload":  unique_payload,
    }


class TestGossipRouter:
    def test_receive_calls_handler(self):
        """receive() dispatches to registered handlers."""
        router = make_router()
        received = []
        router.on("test_msg", lambda p: received.append(p))
        env = make_envelope(router, "test_msg", {"data": "hello"})
        router.receive(env)
        assert len(received) == 1

    def test_dedup_ignores_repeated_message(self):
        """Same msg_id seen twice is only dispatched once."""
        router = make_router()
        received = []
        router.on("ping", lambda p: received.append(p))
        env = make_envelope(router, "ping", {"x": 1})
        router.receive(env)
        router.receive(env)   # exact same envelope again
        assert len(received) == 1

    def test_unknown_message_no_error(self):
        """Unknown message type with no handler must not raise."""
        router = make_router()
        env = make_envelope(router, "no_handler_for_this", {})
        router.receive(env)  # must not raise

    def test_multiple_handlers_same_type(self):
        """Multiple handlers for the same type all fire."""
        router = make_router()
        hits = []
        router.on("event", lambda p: hits.append("h1"))
        router.on("event", lambda p: hits.append("h2"))
        env = make_envelope(router, "event", {})
        router.receive(env)
        assert "h1" in hits and "h2" in hits

    def test_broadcast_does_not_raise(self):
        """broadcast() fans out to peers (none here) without error."""
        router = make_router()
        router.broadcast("ping", {"ts": 1})  # must not raise

    def test_ttl_zero_not_forwarded(self):
        """TTL=0 envelope should still dispatch locally but not forward."""
        router = make_router()
        received = []
        router.on("msg", lambda p: received.append(p))
        env = make_envelope(router, "msg", {}, ttl=0)
        router.receive(env)
        assert len(received) == 1  # dispatched locally


class TestPeer:
    def test_create_peer_defaults(self):
        p = Peer(host="127.0.0.1", port=9001, node_id="abc123")
        assert p.state == PeerState.UNKNOWN
        assert p.fail_count == 0

    def test_touch_updates_last_seen(self):
        p = Peer(host="127.0.0.1", port=9001, node_id="abc123")
        before = p.last_seen
        time.sleep(0.02)
        p.touch()
        assert p.last_seen > before

    def test_mark_failure_increments(self):
        p = Peer(host="127.0.0.1", port=9001, node_id="abc123")
        p.mark_failure(ban_after=5)
        assert p.fail_count == 1
        assert p.state != PeerState.BANNED

    def test_ban_after_threshold(self):
        p = Peer(host="127.0.0.1", port=9001, node_id="abc123")
        for _ in range(3):
            p.mark_failure(ban_after=3)
        assert p.state == PeerState.BANNED

    def test_to_dict_has_required_keys(self):
        p = Peer(host="10.0.0.1", port=8080, node_id="xyz789")
        d = p.to_dict()
        assert d["host"] == "10.0.0.1"
        assert d["port"] == 8080
        assert d["node_id"] == "xyz789"

    def test_from_dict_roundtrip(self):
        p = Peer(host="10.0.0.1", port=8080, node_id="xyz789")
        d = p.to_dict()
        p2 = Peer.from_dict(d)
        assert p2.host == p.host
        assert p2.port == p.port
        assert p2.node_id == p.node_id
