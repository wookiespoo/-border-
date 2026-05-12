"""
BorderDNS — Resolver

Recursive resolution with CNAME chaining and TTL-based caching.
"""

from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional

from .record import DNSRecord, RecordType, BORDER_TLD
from .registry import DNSRegistry

logger = logging.getLogger("border.dns.resolver")

CACHE_TTL       = 300   # 5 minute local cache
MAX_CNAME_DEPTH = 8


class CacheEntry:
    def __init__(self, records: List[DNSRecord], cached_at: float):
        self.records   = records
        self.cached_at = cached_at

    def is_fresh(self) -> bool:
        return time.time() - self.cached_at < CACHE_TTL


class DNSResolver:
    def __init__(self, registry: DNSRegistry):
        self.registry  = registry
        self._cache:   Dict[str, CacheEntry] = {}
        self._hits:    int = 0
        self._misses:  int = 0

    # ── Core resolution ───────────────────────────────────
    def resolve(self, name: str,
                record_type: Optional[RecordType] = None) -> List[DNSRecord]:
        name      = self._normalise(name)
        cache_key = f"{name}:{record_type}"

        cached = self._cache.get(cache_key)
        if cached and cached.is_fresh():
            self._hits += 1
            return cached.records

        self._misses += 1
        records = self._resolve_recursive(name, record_type, depth=0)
        self._cache[cache_key] = CacheEntry(records=records, cached_at=time.time())
        return records

    def _resolve_recursive(self, name: str,
                            record_type: Optional[RecordType],
                            depth: int) -> List[DNSRecord]:
        if depth > MAX_CNAME_DEPTH:
            logger.warning(f"[DNS] CNAME chain too deep at {name}")
            return []

        records = self.registry.resolve(name, record_type)

        # Follow CNAME if no direct records found
        if not records and record_type != RecordType.CNAME:
            cname_records = self.registry.resolve(name, RecordType.CNAME)
            if cname_records:
                target = cname_records[0].value
                logger.debug(f"[DNS] CNAME: {name} → {target}")
                return self._resolve_recursive(target, record_type, depth + 1)

        return records

    # ── Convenience resolvers ─────────────────────────────
    def resolve_address(self, name: str) -> Optional[str]:
        addr = self.registry.resolve_address(name)
        if addr:
            return addr
        # Also try via CNAME chain
        records = self.resolve(name, RecordType.ADDRESS)
        return records[0].value if records else None

    def resolve_did(self, name: str) -> Optional[str]:
        records = self.resolve(name, RecordType.DID)
        return records[0].value if records else None

    def resolve_services(self, name: str,
                          service_type: Optional[str] = None) -> List[DNSRecord]:
        srvs = self.resolve(name, RecordType.SRV)
        if service_type:
            srvs = [s for s in srvs
                    if s.metadata.get("service_type") == service_type]
        return srvs

    def resolve_txt(self, name: str) -> Dict[str, str]:
        records = self.resolve(name, RecordType.TXT)
        result: Dict[str, str] = {}
        for r in records:
            result.update(r.metadata)
        return result

    def reverse_lookup(self, address: str) -> List[str]:
        return self.registry.names_for(address)

    def resolve_many(self, names: List[str]) -> Dict[str, Optional[str]]:
        return {name: self.resolve_address(name) for name in names}

    # ── Cache ─────────────────────────────────────────────
    def flush_cache(self, name: Optional[str] = None) -> None:
        if name:
            norm = self._normalise(name)
            for k in [k for k in self._cache if k.startswith(norm)]:
                del self._cache[k]
        else:
            self._cache.clear()

    @property
    def cache_stats(self) -> dict:
        total = self._hits + self._misses
        return {
            "cached_entries": len(self._cache),
            "hits":           self._hits,
            "misses":         self._misses,
            "hit_rate":       round(self._hits / total, 3) if total else 0.0,
        }

    def _normalise(self, name: str) -> str:
        name = name.lower().strip()
        if not name.endswith(f".{BORDER_TLD}"):
            name = f"{name}.{BORDER_TLD}"
        return name
