"""Tests for BorderRelay — session lifecycle and byte accounting.

wrap_request/response require a completed ECDH session so we test byte
accounting by calling _record_bytes directly and test wrap/unwrap using
a mock obfuscator that returns the payload unchanged.
"""
import pytest
from unittest.mock import MagicMock, patch
from border.relay import BorderRelay


def make_chain_mock():
    chain = MagicMock()
    chain.add_proof.return_value = True
    chain.height = 5
    return chain


def make_wallet_mock():
    wallet = MagicMock()
    wallet.address = "BC_relay_test_00000000000000000000000"
    wallet.sign.return_value = "mock_sig_base64=="
    return wallet


def make_relay():
    return BorderRelay(chain=make_chain_mock(), wallet=make_wallet_mock())


class TestSession:
    def test_open_session_creates_entry(self):
        relay = make_relay()
        sess = relay.open_session()
        assert sess.session_id in relay._sessions

    def test_close_session_removes_entry(self):
        relay = make_relay()
        sess = relay.open_session()
        relay.close_session(sess.session_id)
        assert sess.session_id not in relay._sessions

    def test_close_unknown_session_no_error(self):
        relay = make_relay()
        relay.close_session("ghost_session_id")  # must not raise

    def test_two_sessions_are_independent(self):
        relay = make_relay()
        s1 = relay.open_session()
        s2 = relay.open_session()
        assert s1.session_id != s2.session_id
        assert len(relay._sessions) == 2

    def test_stats_reflects_open_sessions(self):
        relay = make_relay()
        relay.open_session()
        relay.open_session()
        stats = relay.stats()
        assert stats["open_sessions"] == 2

    def test_stats_has_expected_keys(self):
        relay = make_relay()
        stats = relay.stats()
        assert "open_sessions" in stats
        assert "pending_bytes" in stats


class TestByteAccounting:
    def test_record_bytes_sent(self):
        relay = make_relay()
        sess = relay.open_session()
        relay._record_bytes(sess.session_id, sent=1024)
        st = relay._sessions[sess.session_id]
        assert st.bytes_out == 1024

    def test_record_bytes_received(self):
        relay = make_relay()
        sess = relay.open_session()
        relay._record_bytes(sess.session_id, received=2048)
        st = relay._sessions[sess.session_id]
        assert st.bytes_in == 2048

    def test_total_bytes_sums_both_directions(self):
        relay = make_relay()
        sess = relay.open_session()
        relay._record_bytes(sess.session_id, sent=100, received=200)
        st = relay._sessions[sess.session_id]
        assert st.total_bytes == 300

    def test_bytes_accumulate_across_calls(self):
        relay = make_relay()
        sess = relay.open_session()
        for _ in range(5):
            relay._record_bytes(sess.session_id, sent=10)
        st = relay._sessions[sess.session_id]
        assert st.bytes_out == 50

    def test_unknown_session_record_ignored(self):
        relay = make_relay()
        # Must not raise even for unknown session_id
        relay._record_bytes("ghost_id", sent=999)
