"""
border.blockchain.store — SQLite persistence backend for BorderChain.

Replaces the full-chain JSON dump with an append-mostly SQLite database.

Schema
------
blocks
  idx          INTEGER PRIMARY KEY   — block index
  block_hash   TEXT NOT NULL
  prev_hash    TEXT
  miner_address TEXT
  timestamp    REAL
  difficulty   INTEGER
  nonce        INTEGER
  block_json   TEXT NOT NULL         — full Block.to_dict() serialization

metadata
  key   TEXT PRIMARY KEY
  value TEXT

Performance
-----------
• add_block    → single INSERT  (O(1))
• load_chain   → full table scan once at startup
• reorg_to     → DELETE WHERE idx > N  (trim stale fork)
• blocks_range → SELECT with LIMIT/OFFSET or idx BETWEEN
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("border.blockchain.store")


class ChainStore:
    """
    SQLite-backed block store.

    Parameters
    ----------
    db_path : path to the .db file.  Created automatically if absent.
    """

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS blocks (
        idx           INTEGER PRIMARY KEY,
        block_hash    TEXT    NOT NULL,
        prev_hash     TEXT,
        miner_address TEXT,
        timestamp     REAL,
        difficulty    INTEGER,
        nonce         INTEGER,
        block_json    TEXT    NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_block_hash ON blocks(block_hash);
    CREATE INDEX IF NOT EXISTS idx_miner      ON blocks(miner_address);

    CREATE TABLE IF NOT EXISTS metadata (
        key   TEXT PRIMARY KEY,
        value TEXT
    );
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
        )
        self._conn.execute("PRAGMA journal_mode=WAL;")   # concurrent reads
        self._conn.execute("PRAGMA synchronous=NORMAL;") # fast, safe enough
        self._conn.executescript(self._SCHEMA)
        self._conn.commit()
        logger.debug(f"[Store] SQLite open  path={self.db_path}")

    # ------------------------------------------------------------------ #
    # Block writes
    # ------------------------------------------------------------------ #

    def append_block(self, block_dict: dict) -> None:
        """Insert one block.  Ignores duplicates (idempotent)."""
        idx    = block_dict["index"]
        bjson  = json.dumps(block_dict)
        self._conn.execute(
            """INSERT OR REPLACE INTO blocks
               (idx, block_hash, prev_hash, miner_address, timestamp,
                difficulty, nonce, block_json)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                idx,
                block_dict.get("block_hash", ""),
                block_dict.get("previous_hash", ""),
                block_dict.get("miner_address", ""),
                block_dict.get("timestamp", 0.0),
                block_dict.get("difficulty", 0),
                block_dict.get("nonce", 0),
                bjson,
            ),
        )
        self._conn.commit()

    def reorg_to(self, height: int) -> None:
        """Delete all blocks with idx > height (fork rollback)."""
        self._conn.execute("DELETE FROM blocks WHERE idx > ?", (height,))
        self._conn.commit()
        logger.info(f"[Store] Reorg: trimmed chain to height {height}")

    # ------------------------------------------------------------------ #
    # Block reads
    # ------------------------------------------------------------------ #

    def load_all(self) -> List[dict]:
        """Return all blocks ordered by index."""
        cur = self._conn.execute(
            "SELECT block_json FROM blocks ORDER BY idx ASC"
        )
        return [json.loads(row[0]) for row in cur.fetchall()]

    def get_block(self, idx: int) -> Optional[dict]:
        cur = self._conn.execute(
            "SELECT block_json FROM blocks WHERE idx = ?", (idx,)
        )
        row = cur.fetchone()
        return json.loads(row[0]) if row else None

    def get_by_hash(self, block_hash: str) -> Optional[dict]:
        cur = self._conn.execute(
            "SELECT block_json FROM blocks WHERE block_hash = ?", (block_hash,)
        )
        row = cur.fetchone()
        return json.loads(row[0]) if row else None

    def get_range(self, start: int, end: int) -> List[dict]:
        """Return blocks[start:end+1]."""
        cur = self._conn.execute(
            "SELECT block_json FROM blocks WHERE idx BETWEEN ? AND ? ORDER BY idx ASC",
            (start, end),
        )
        return [json.loads(row[0]) for row in cur.fetchall()]

    def height(self) -> int:
        cur = self._conn.execute("SELECT MAX(idx) FROM blocks")
        row = cur.fetchone()
        return row[0] if row and row[0] is not None else -1

    def count(self) -> int:
        cur = self._conn.execute("SELECT COUNT(*) FROM blocks")
        return cur.fetchone()[0]

    # ------------------------------------------------------------------ #
    # Metadata key/value store
    # ------------------------------------------------------------------ #

    def set_meta(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO metadata(key,value) VALUES (?,?)",
            (key, value),
        )
        self._conn.commit()

    def get_meta(self, key: str, default: str = "") -> str:
        cur = self._conn.execute(
            "SELECT value FROM metadata WHERE key = ?", (key,)
        )
        row = cur.fetchone()
        return row[0] if row else default

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def close(self) -> None:
        self._conn.close()

    def stats(self) -> dict:
        return {
            "db_path":     str(self.db_path),
            "block_count": self.count(),
            "height":      self.height(),
            "db_size_kb":  round(self.db_path.stat().st_size / 1024, 1)
                           if self.db_path.exists() else 0,
        }
