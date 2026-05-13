"""
border.blockchain.script — Minimal stack-based script VM.

Opcodes
-------
OP_TRUE / OP_FALSE          push 1 / 0
OP_PUSH <data>              push data onto stack
OP_DUP                      duplicate top
OP_DROP                     discard top
OP_EQUAL / OP_EQUALVERIFY   compare top two; EQUALVERIFY fails if not equal
OP_NOT                      boolean NOT
OP_ADD / OP_SUB             integer arithmetic
OP_CHECKSIG                 pop pubkey + sig, verify Ed25519(sig, msg, pubkey)
OP_CHECKMULTISIG            M-of-N: pop N pubkeys, M sigs, verify M sigs valid
OP_CHECKLOCKTIMEVERIFY      fail if current block height < stack top
OP_IF / OP_ELSE / OP_ENDIF  conditional execution
OP_RETURN                   mark output unspendable (data carrier)
OP_HASH256                  SHA256(SHA256(top))

Script types (standard templates)
----------------------------------
P2PK  — Pay-to-Public-Key
P2PKH — Pay-to-Public-Key-Hash  (most common)
MULTISIG — M-of-N
TIMELOCK — height-locked P2PKH
DATA — OP_RETURN carrier (zero value)

Usage
-----
>>> from border.blockchain.script import Script, ScriptEngine, ScriptBuilder
>>> # P2PKH locking script
>>> lock = ScriptBuilder.p2pkh(wallet.address)
>>> unlock = ScriptBuilder.p2pkh_unlock(sig, wallet.public_key_b64)
>>> ok, err = ScriptEngine(block_height=10).run(unlock + lock, signing_msg=b"tx_data")
"""

from __future__ import annotations

import base64
import hashlib
import logging
from enum import IntEnum
from typing import List, Optional, Tuple

from .wallet import BorderWallet

logger = logging.getLogger("border.blockchain.script")


# ── Opcodes ───────────────────────────────────────────────────────────────────

class OP(IntEnum):
    OP_0            = 0x00
    OP_FALSE        = 0x00
    OP_PUSH         = 0x4c   # next byte = length, then data
    OP_TRUE         = 0x51
    OP_1            = 0x51
    OP_DUP          = 0x76
    OP_DROP         = 0x75
    OP_EQUAL        = 0x87
    OP_EQUALVERIFY  = 0x88
    OP_NOT          = 0x91
    OP_ADD          = 0x93
    OP_SUB          = 0x94
    OP_HASH256      = 0xAA
    OP_CHECKSIG     = 0xAC
    OP_CHECKMULTISIG= 0xAE
    OP_CLAIMTIMEVERIFY = 0xB1   # CLTV — check lock time verify
    OP_IF           = 0x63
    OP_ELSE         = 0x67
    OP_ENDIF        = 0x68
    OP_RETURN       = 0x6A


# ── Script ─────────────────────────────────────────────────────────────────

class Script:
    """Raw byte sequence representing a locking or unlocking script."""

    def __init__(self, bytecode: bytes = b"") -> None:
        self.bytecode = bytecode

    def __add__(self, other: "Script") -> "Script":
        return Script(self.bytecode + other.bytecode)

    def __repr__(self) -> str:
        return f"Script({self.bytecode.hex()!r})"

    def to_hex(self) -> str:
        return self.bytecode.hex()

    @classmethod
    def from_hex(cls, hex_str: str) -> "Script":
        return cls(bytes.fromhex(hex_str))

    @classmethod
    def from_bytes(cls, data: bytes) -> "Script":
        return cls(data)


# ── Script builder ────────────────────────────────────────────────────────────

class ScriptBuilder:
    """Helpers for creating standard script templates."""

    @staticmethod
    def _push(data: bytes) -> bytes:
        length = len(data)
        if length > 255:
            raise ValueError("Push data too large (max 255 bytes)")
        return bytes([OP.OP_PUSH, length]) + data

    @classmethod
    def p2pkh_lock(cls, public_key_b64: str) -> Script:
        """
        Pay-to-Public-Key-Hash locking script.
        OP_DUP OP_HASH256 <pubkey_hash> OP_EQUALVERIFY OP_CHECKSIG
        The hash stored is SHA256(SHA256(pubkey_b64.encode())).
        """
        pub_hash = hashlib.sha256(
            hashlib.sha256(public_key_b64.encode()).digest()
        ).digest()
        code = (bytes([OP.OP_DUP, OP.OP_HASH256]) +
                cls._push(pub_hash) +
                bytes([OP.OP_EQUALVERIFY, OP.OP_CHECKSIG]))
        return Script(code)

    @classmethod
    def p2pkh_unlock(cls, signature: str, public_key_b64: str) -> Script:
        """
        Unlocking script for P2PKH.
        <sig> <pubkey>
        """
        sig_bytes = signature.encode()
        pub_bytes = public_key_b64.encode()
        code = cls._push(sig_bytes) + cls._push(pub_bytes)
        return Script(code)

    @classmethod
    def multisig_lock(cls, m: int, public_keys: List[str]) -> Script:
        """
        M-of-N multisig locking script.
        OP_m <pubkey1> ... <pubkeyN> OP_N OP_CHECKMULTISIG
        """
        n = len(public_keys)
        if not (1 <= m <= n <= 15):
            raise ValueError("M-of-N must have 1≤M≤N≤15")
        code = bytes([0x50 + m])   # OP_m (OP_1 = 0x51, OP_2 = 0x52, ...)
        for pk in public_keys:
            code += cls._push(pk.encode())
        code += bytes([0x50 + n, OP.OP_CHECKMULTISIG])
        return Script(code)

    @classmethod
    def multisig_unlock(cls, signatures: List[str]) -> Script:
        """Unlocking script: OP_FALSE <sig1> ... <sigM>"""
        code = bytes([OP.OP_FALSE])
        for sig in signatures:
            code += cls._push(sig.encode())
        return Script(code)

    @classmethod
    def timelock_lock(cls, block_height: int, public_key_b64: str) -> Script:
        """
        Height-locked P2PKH: unspendable until block_height.
        <height> OP_CLAIMTIMEVERIFY OP_DROP OP_DUP OP_HASH256 <pub_hash> OP_EQUALVERIFY OP_CHECKSIG
        """
        h_bytes  = block_height.to_bytes(4, "little")
        pub_hash = hashlib.sha256(
            hashlib.sha256(public_key_b64.encode()).digest()
        ).digest()
        code = (cls._push(h_bytes) +
                bytes([OP.OP_CLAIMTIMEVERIFY, OP.OP_DROP,
                       OP.OP_DUP, OP.OP_HASH256]) +
                cls._push(pub_hash) +
                bytes([OP.OP_EQUALVERIFY, OP.OP_CHECKSIG]))
        return Script(code)

    @classmethod
    def data_carrier(cls, data: bytes) -> Script:
        """OP_RETURN <data> — unspendable data-carrier output."""
        if len(data) > 80:
            raise ValueError("Data carrier max 80 bytes")
        return Script(bytes([OP.OP_RETURN]) + cls._push(data))


# ── VM ────────────────────────────────────────────────────────────────────────

class ScriptEngine:
    """
    Stack-based script interpreter.

    Parameters
    ----------
    block_height : current chain height (needed for CLTV)
    signing_msg  : bytes that CHECKSIG verifies against (typically tx hash)
    """

    MAX_STACK_DEPTH = 64
    MAX_SCRIPT_OPS  = 201

    def __init__(
        self,
        block_height: int = 0,
        signing_msg: bytes = b"",
    ) -> None:
        self.block_height = block_height
        self.signing_msg  = signing_msg
        self._stack:  List[bytes] = []
        self._altstack: List[bytes] = []

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def run(
        self,
        script: Script,
        signing_msg: Optional[bytes] = None,
    ) -> Tuple[bool, str]:
        """
        Execute `script`.  Returns (success, error_message).
        success=True iff stack top is truthy after execution.
        """
        if signing_msg is not None:
            self.signing_msg = signing_msg

        self._stack = []
        data = script.bytecode
        idx  = 0
        ops  = 0

        try:
            while idx < len(data):
                if ops > self.MAX_SCRIPT_OPS:
                    return False, "Script exceeded max op count"
                ops += 1

                op = data[idx]
                idx += 1

                if op == OP.OP_PUSH:
                    length = data[idx]; idx += 1
                    self._push(data[idx:idx+length]); idx += length

                elif op in (OP.OP_TRUE,):    # OP_1 / OP_TRUE
                    self._push(b"\x01")

                elif op == OP.OP_FALSE:
                    self._push(b"")

                elif op == OP.OP_DUP:
                    top = self._top()
                    self._push(top)

                elif op == OP.OP_DROP:
                    self._pop()

                elif op == OP.OP_HASH256:
                    d = self._pop()
                    self._push(hashlib.sha256(hashlib.sha256(d).digest()).digest())

                elif op == OP.OP_EQUAL:
                    a, b = self._pop(), self._pop()
                    self._push(b"\x01" if a == b else b"")

                elif op == OP.OP_EQUALVERIFY:
                    a, b = self._pop(), self._pop()
                    if a != b:
                        return False, "OP_EQUALVERIFY failed"

                elif op == OP.OP_NOT:
                    v = self._pop()
                    self._push(b"" if v and v != b"\x00" else b"\x01")

                elif op == OP.OP_ADD:
                    a = int.from_bytes(self._pop(), "little", signed=True)
                    b = int.from_bytes(self._pop(), "little", signed=True)
                    self._push((a + b).to_bytes(4, "little", signed=True))

                elif op == OP.OP_SUB:
                    b = int.from_bytes(self._pop(), "little", signed=True)
                    a = int.from_bytes(self._pop(), "little", signed=True)
                    self._push((a - b).to_bytes(4, "little", signed=True))

                elif op == OP.OP_CHECKSIG:
                    ok, err = self._checksig()
                    if not ok:
                        return False, err
                    self._push(b"\x01")

                elif op == OP.OP_CHECKMULTISIG:
                    ok, err = self._checkmultisig()
                    if not ok:
                        return False, err
                    self._push(b"\x01")

                elif op == OP.OP_CLAIMTIMEVERIFY:
                    lock_height = int.from_bytes(self._top(), "little")
                    if self.block_height < lock_height:
                        return False, (f"CLTV: locked until block {lock_height}, "
                                       f"current={self.block_height}")

                elif op == OP.OP_RETURN:
                    return False, "OP_RETURN: unspendable output"

                elif op == OP.OP_IF:
                    # Minimal IF: skip to OP_ELSE/OP_ENDIF if top is falsy
                    cond = self._pop()
                    if not (cond and cond != b"\x00"):
                        depth = 1
                        while idx < len(data) and depth > 0:
                            op2 = data[idx]; idx += 1
                            if op2 == OP.OP_PUSH:
                                l = data[idx]; idx += 1 + l
                            elif op2 == OP.OP_IF:
                                depth += 1
                            elif op2 in (OP.OP_ELSE, OP.OP_ENDIF):
                                depth -= 1

                elif op == OP.OP_ELSE:
                    # Skip to OP_ENDIF
                    depth = 1
                    while idx < len(data) and depth > 0:
                        op2 = data[idx]; idx += 1
                        if op2 == OP.OP_PUSH:
                            l = data[idx]; idx += 1 + l
                        elif op2 == OP.OP_IF:
                            depth += 1
                        elif op2 == OP.OP_ENDIF:
                            depth -= 1

                elif op == OP.OP_ENDIF:
                    pass  # no-op, marks end of conditional

                elif 0x52 <= op <= 0x60:
                    # OP_2 through OP_16
                    self._push(bytes([op - 0x50]))

                else:
                    return False, f"Unknown opcode: 0x{op:02x}"

        except ScriptError as e:
            return False, str(e)
        except Exception as e:
            return False, f"Script exception: {e}"

        if not self._stack:
            return False, "Empty stack after execution"
        top = self._stack[-1]
        return bool(top and top != b"\x00"), ""

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _push(self, data: bytes) -> None:
        if len(self._stack) >= self.MAX_STACK_DEPTH:
            raise ScriptError("Stack overflow")
        self._stack.append(data)

    def _pop(self) -> bytes:
        if not self._stack:
            raise ScriptError("Stack underflow")
        return self._stack.pop()

    def _top(self) -> bytes:
        if not self._stack:
            raise ScriptError("Stack underflow (peek)")
        return self._stack[-1]

    def _checksig(self) -> Tuple[bool, str]:
        """OP_CHECKSIG: pop pubkey_b64, pop sig; verify."""
        pub_bytes = self._pop()
        sig_bytes = self._pop()
        pub_b64   = pub_bytes.decode()
        sig_str   = sig_bytes.decode()
        try:
            ok = BorderWallet.verify(pub_b64, self.signing_msg, sig_str)
            return ok, ("" if ok else "CHECKSIG: invalid signature")
        except Exception as e:
            return False, f"CHECKSIG error: {e}"

    def _checkmultisig(self) -> Tuple[bool, str]:
        """
        OP_CHECKMULTISIG: pop N pubkeys, pop M, pop M sigs, pop dummy.
        Verify that at least M sigs are valid.
        """
        n_byte = self._pop()
        n = n_byte[0] if n_byte else 0
        pubkeys = [self._pop().decode() for _ in range(n)]

        m_byte = self._pop()
        m = m_byte[0] if m_byte else 0
        sigs = [self._pop().decode() for _ in range(m)]

        # Pop dummy (Bitcoin bug compatibility — one extra OP_FALSE)
        if self._stack:
            self._pop()

        valid = 0
        pk_idx = 0
        for sig in sigs:
            while pk_idx < len(pubkeys):
                try:
                    if BorderWallet.verify(pubkeys[pk_idx], self.signing_msg, sig):
                        valid += 1
                        pk_idx += 1
                        break
                except Exception:
                    pass
                pk_idx += 1

        if valid < m:
            return False, f"CHECKMULTISIG: {valid}/{m} valid signatures"
        return True, ""


class ScriptError(Exception):
    pass
