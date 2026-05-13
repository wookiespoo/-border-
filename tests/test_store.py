"""
Tests for border.blockchain.store — SQLite chain persistence.
"""
import os
import time
import tempfile
import pytest

from border.blockchain.store import ChainStore
from border.blockchain.chain import BorderChain
from border.blockchain.block import Block
from border.blockchain.wallet import BorderWallet


# ─────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────

def _make_block(chain: BorderChain, wallet: BorderWallet) -> Block:
    """Fabricate a valid-looking block without PoW (for persistence tests)."""
    prev = chain._chain[-1]
    blk = Block(
        index          = prev.index + 1,
        previous_hash  = prev.block_hash,
        timestamp      = time.time(),
        transactions   = [],
        bandwidth_proofs=[],
        miner_address  = wallet.address,
        difficulty     = 1,
    )
    blk.nonce = 0
    blk.block_hash = blk.compute_hash()
    return blk


# ─────────────────────────────────────────────────────────
# ChainStore unit tests
# ─────────────────────────────────────────────────────────

class TestChainStore:
    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.db  = os.path.join(self.tmp, "test.db")

    def _store(self) -> ChainStore:
        return ChainStore(self.db)

    def _sample_block(self, idx=0) -> dict:
        return {
            "index":         idx,
            "block_hash":    f"hash_{idx:04d}",
            "previous_hash": f"hash_{idx-1:04d}" if idx > 0 else "0" * 64,
            "miner_address": "BC_" + "a" * 32,
            "timestamp":     1_700_000_000.0 + idx,
            "difficulty":    4,
            "nonce":         12345,
            "transactions":  [],
        }

    def test_empty_store_height_minus_one(self):
        s = self._store()
        assert s.height() == -1

    def test_empty_store_count_zero(self):
        s = self._store()
        assert s.count() == 0

    def test_append_and_count(self):
        s = self._store()
        s.append_block(self._sample_block(0))
        assert s.count() == 1

    def test_append_multiple(self):
        s = self._store()
        for i in range(5):
            s.append_block(self._sample_block(i))
        assert s.count() == 5
        assert s.height() == 4

    def test_append_idempotent(self):
        s = self._store()
        s.append_block(self._sample_block(0))
        s.append_block(self._sample_block(0))  # duplicate
        assert s.count() == 1

    def test_load_all_ordered(self):
        s = self._store()
        for i in range(4):
            s.append_block(self._sample_block(i))
        blocks = s.load_all()
        assert [b["index"] for b in blocks] == [0, 1, 2, 3]

    def test_get_block_known(self):
        s = self._store()
        s.append_block(self._sample_block(0))
        b = s.get_block(0)
        assert b is not None
        assert b["index"] == 0

    def test_get_block_unknown_returns_none(self):
        s = self._store()
        assert s.get_block(999) is None

    def test_get_by_hash(self):
        s = self._store()
        s.append_block(self._sample_block(2))
        b = s.get_by_hash("hash_0002")
        assert b is not None
        assert b["index"] == 2

    def test_get_by_hash_unknown_returns_none(self):
        s = self._store()
        assert s.get_by_hash("not_a_real_hash") is None

    def test_get_range(self):
        s = self._store()
        for i in range(6):
            s.append_block(self._sample_block(i))
        r = s.get_range(2, 4)
        assert len(r) == 3
        assert [b["index"] for b in r] == [2, 3, 4]

    def test_get_range_clamped(self):
        s = self._store()
        for i in range(3):
            s.append_block(self._sample_block(i))
        r = s.get_range(0, 100)
        assert len(r) == 3

    def test_reorg_trims_chain(self):
        s = self._store()
        for i in range(5):
            s.append_block(self._sample_block(i))
        s.reorg_to(2)
        assert s.count() == 3
        assert s.height() == 2

    def test_metadata_set_get(self):
        s = self._store()
        s.set_meta("network", "testnet")
        assert s.get_meta("network") == "testnet"

    def test_metadata_default(self):
        s = self._store()
        assert s.get_meta("missing", "default_val") == "default_val"

    def test_metadata_overwrite(self):
        s = self._store()
        s.set_meta("k", "v1")
        s.set_meta("k", "v2")
        assert s.get_meta("k") == "v2"

    def test_stats_keys(self):
        s = self._store()
        s.append_block(self._sample_block(0))
        st = s.stats()
        assert "block_count" in st
        assert "height"      in st
        assert "db_size_kb"  in st
        assert st["block_count"] == 1


# ─────────────────────────────────────────────────────────
# BorderChain integration with SQLite
# ─────────────────────────────────────────────────────────

class TestChainSQLite:
    def setup_method(self):
        self.tmp    = tempfile.mkdtemp()
        self.wallet = BorderWallet.create()

    def _db(self, name="chain.db"):
        return os.path.join(self.tmp, name)

    def test_sqlite_store_created_for_db_path(self):
        chain = BorderChain(persist_path=self._db())
        assert chain._store is not None

    def test_json_path_uses_legacy(self):
        chain = BorderChain(persist_path=os.path.join(self.tmp, "chain.json"))
        assert chain._store is None

    def test_bare_path_gets_db_suffix(self):
        chain = BorderChain(persist_path=os.path.join(self.tmp, "node"))
        assert chain._store is not None
        assert chain._store.db_path.suffix == ".db"

    def test_genesis_persisted_on_init(self):
        chain = BorderChain(persist_path=self._db())
        assert chain._store.count() == 1
        assert chain._store.get_block(0) is not None

    def test_add_block_appends_to_store(self):
        chain = BorderChain(persist_path=self._db())
        blk = _make_block(chain, self.wallet)
        chain._chain.append(blk)
        chain._save()
        assert chain._store.count() == 2

    def test_reload_recovers_chain(self):
        db = self._db()
        chain = BorderChain(persist_path=db)
        for _ in range(3):
            blk = _make_block(chain, self.wallet)
            chain._chain.append(blk)
            chain._save()
        height = chain.height
        del chain
        chain2 = BorderChain(persist_path=db)
        assert chain2.height == height
        assert len(chain2._chain) == height + 1

    def test_reload_recovers_correct_hashes(self):
        db = self._db()
        chain = BorderChain(persist_path=db)
        blk = _make_block(chain, self.wallet)
        chain._chain.append(blk)
        chain._save()
        tip_hash = chain._chain[-1].block_hash
        del chain
        chain2 = BorderChain(persist_path=db)
        assert chain2._chain[-1].block_hash == tip_hash

    def test_no_persist_path_works_in_memory(self):
        chain = BorderChain()
        assert chain._store is None
        blk = _make_block(chain, self.wallet)
        chain._chain.append(blk)
        chain._save()   # no-op, no error
        assert chain.height == 1
