"""
Tests for border.blockchain.script — stack VM.
"""
import pytest
import hashlib

from border.blockchain.wallet import BorderWallet
from border.blockchain.script import (
    Script, ScriptEngine, ScriptBuilder, OP, ScriptError
)

def _run(bytecode: bytes, msg=b"test") -> tuple:
    return ScriptEngine(signing_msg=msg).run(Script(bytecode))

def _push_bytes(data: bytes) -> bytes:
    return bytes([OP.OP_PUSH, len(data)]) + data


# ── Basic stack ops ────────────────────────────────────────────────────────────

class TestBasicOps:
    def test_op_true(self):
        ok, err = _run(bytes([OP.OP_TRUE]))
        assert ok, err

    def test_op_false_fails(self):
        ok, _ = _run(bytes([OP.OP_FALSE]))
        assert not ok

    def test_op_dup(self):
        # push "hello", dup → two "hello"s, drop → one "hello" (truthy)
        code = _push_bytes(b"\x01") + bytes([OP.OP_DUP, OP.OP_EQUAL])
        ok, err = _run(code)
        assert ok, err

    def test_op_drop_leaves_lower(self):
        # push 0, push 1, drop → 0 (falsy) → run fails
        code = bytes([OP.OP_FALSE, OP.OP_TRUE, OP.OP_DROP])
        ok, _ = _run(code)
        assert not ok

    def test_op_equal_true(self):
        code = _push_bytes(b"x") + _push_bytes(b"x") + bytes([OP.OP_EQUAL])
        ok, err = _run(code)
        assert ok, err

    def test_op_equal_false(self):
        code = _push_bytes(b"x") + _push_bytes(b"y") + bytes([OP.OP_EQUAL])
        ok, _ = _run(code)
        assert not ok

    def test_op_not_of_true_is_false(self):
        code = bytes([OP.OP_TRUE, OP.OP_NOT])
        ok, _ = _run(code)
        assert not ok

    def test_op_not_of_false_is_true(self):
        code = bytes([OP.OP_FALSE, OP.OP_NOT])
        ok, err = _run(code)
        assert ok, err

    def test_op_hash256(self):
        data = b"hello"
        expected = hashlib.sha256(hashlib.sha256(data).digest()).digest()
        code = _push_bytes(data) + bytes([OP.OP_HASH256]) + _push_bytes(expected) + bytes([OP.OP_EQUAL])
        ok, err = _run(code)
        assert ok, err

    def test_op_return_fails(self):
        ok, err = _run(bytes([OP.OP_RETURN]))
        assert not ok
        assert "OP_RETURN" in err

    def test_empty_stack_fails(self):
        ok, _ = _run(b"")
        assert not ok

    def test_unknown_opcode_fails(self):
        ok, err = _run(bytes([0xFF]))
        assert not ok
        assert "Unknown opcode" in err


# ── P2PKH ─────────────────────────────────────────────────────────────────────

class TestP2PKH:
    def test_valid_p2pkh(self):
        wallet = BorderWallet.create()
        msg    = b"tx_abc123"
        sig    = wallet.sign(msg)
        unlock = ScriptBuilder.p2pkh_unlock(sig, wallet.public_key_b64)
        lock   = ScriptBuilder.p2pkh_lock(wallet.public_key_b64)
        ok, err = ScriptEngine(signing_msg=msg).run(unlock + lock)
        assert ok, err

    def test_wrong_sig_fails(self):
        wallet = BorderWallet.create()
        other  = BorderWallet.create()
        msg    = b"tx_abc123"
        sig    = other.sign(msg)     # signed by wrong key
        unlock = ScriptBuilder.p2pkh_unlock(sig, wallet.public_key_b64)
        lock   = ScriptBuilder.p2pkh_lock(wallet.public_key_b64)
        ok, _  = ScriptEngine(signing_msg=msg).run(unlock + lock)
        assert not ok

    def test_wrong_pubkey_fails(self):
        wallet = BorderWallet.create()
        other  = BorderWallet.create()
        msg    = b"tx_abc123"
        sig    = wallet.sign(msg)
        unlock = ScriptBuilder.p2pkh_unlock(sig, wallet.public_key_b64)
        lock   = ScriptBuilder.p2pkh_lock(other.public_key_b64)  # locked to other
        ok, _  = ScriptEngine(signing_msg=msg).run(unlock + lock)
        assert not ok


# ── Multisig ──────────────────────────────────────────────────────────────────

class TestMultisig:
    def test_2of3_valid(self):
        msg  = b"multisig_tx"
        w1, w2, w3 = [BorderWallet.create() for _ in range(3)]
        s1, s2 = w1.sign(msg), w2.sign(msg)
        unlock = ScriptBuilder.multisig_unlock([s1, s2])
        lock   = ScriptBuilder.multisig_lock(2, [w1.public_key_b64,
                                                  w2.public_key_b64,
                                                  w3.public_key_b64])
        ok, err = ScriptEngine(signing_msg=msg).run(unlock + lock)
        assert ok, err

    def test_1of3_with_one_sig(self):
        msg  = b"multisig_tx"
        w1, w2, w3 = [BorderWallet.create() for _ in range(3)]
        unlock = ScriptBuilder.multisig_unlock([w1.sign(msg)])
        lock   = ScriptBuilder.multisig_lock(1, [w1.public_key_b64,
                                                  w2.public_key_b64,
                                                  w3.public_key_b64])
        ok, err = ScriptEngine(signing_msg=msg).run(unlock + lock)
        assert ok, err

    def test_2of3_with_one_sig_fails(self):
        msg  = b"multisig_tx"
        w1, w2, w3 = [BorderWallet.create() for _ in range(3)]
        unlock = ScriptBuilder.multisig_unlock([w1.sign(msg)])  # only 1 for 2-of-3
        lock   = ScriptBuilder.multisig_lock(2, [w1.public_key_b64,
                                                  w2.public_key_b64,
                                                  w3.public_key_b64])
        ok, _ = ScriptEngine(signing_msg=msg).run(unlock + lock)
        assert not ok


# ── Timelock ──────────────────────────────────────────────────────────────────

class TestTimelock:
    def test_timelock_passes_at_height(self):
        wallet = BorderWallet.create()
        msg    = b"timelock_tx"
        sig    = wallet.sign(msg)
        lock   = ScriptBuilder.timelock_lock(100, wallet.public_key_b64)
        unlock = ScriptBuilder.p2pkh_unlock(sig, wallet.public_key_b64)
        ok, err = ScriptEngine(block_height=100, signing_msg=msg).run(unlock + lock)
        assert ok, err

    def test_timelock_fails_before_height(self):
        wallet = BorderWallet.create()
        msg    = b"timelock_tx"
        sig    = wallet.sign(msg)
        lock   = ScriptBuilder.timelock_lock(200, wallet.public_key_b64)
        unlock = ScriptBuilder.p2pkh_unlock(sig, wallet.public_key_b64)
        ok, err = ScriptEngine(block_height=50, signing_msg=msg).run(unlock + lock)
        assert not ok
        assert "locked until block 200" in err


# ── Data carrier ──────────────────────────────────────────────────────────────

class TestDataCarrier:
    def test_op_return_unspendable(self):
        s = ScriptBuilder.data_carrier(b"border network v1")
        ok, err = ScriptEngine().run(s)
        assert not ok
        assert "OP_RETURN" in err

    def test_data_too_large_rejected(self):
        with pytest.raises(ValueError, match="80"):
            ScriptBuilder.data_carrier(b"x" * 81)
