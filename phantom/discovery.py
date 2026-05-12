"""
Border Node Discovery
Find relay nodes via multiple channels — HTTPS, DNS, and LoRa broadcast.
No single point of failure. If one channel is blocked, use another.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import httpx

logger = logging.getLogger("phantom.discovery")


@dataclass
class RelayNode:
    node_id: str
    endpoint: str
    region: str = "UNKNOWN"
    uptime_score: float = 1.0
    last_seen: float = field(default_factory=time.time)
    bytes_forwarded: int = 0
    phantom_version: str = "0.1"

    def is_fresh(self, max_age_seconds: int = 300) -> bool:
        return (time.time() - self.last_seen) < max_age_seconds

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "endpoint": self.endpoint,
            "region": self.region,
            "uptime_score": self.uptime_score,
            "last_seen": self.last_seen,
            "bytes_forwarded": self.bytes_forwarded,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RelayNode":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# Hardcoded bootstrap nodes — always available as a fallback
# In production these are maintained by the Phantom Foundation
BOOTSTRAP_NODES = [
    RelayNode(
        node_id="bootstrap-eu-01",
        endpoint="https://eu1.phantom-relay.net",
        region="EU",
        uptime_score=0.99,
    ),
    RelayNode(
        node_id="bootstrap-us-01",
        endpoint="https://us1.phantom-relay.net",
        region="US",
        uptime_score=0.99,
    ),
    RelayNode(
        node_id="bootstrap-ap-01",
        endpoint="https://ap1.phantom-relay.net",
        region="APAC",
        uptime_score=0.99,
    ),
]


class BorderDiscovery:
    """
    Discovers relay nodes via multiple redundant channels.
    Falls through to next channel if one is blocked.
    """

    DIRECTORY_URLS = [
        "https://directory.phantom-relay.net/nodes",
        "https://phantom-nodes.github.io/registry/nodes.json",
    ]

    def __init__(self, cache_path: Optional[str] = None):
        self._nodes: dict[str, RelayNode] = {}
        self._cache_path = Path(cache_path) if cache_path else None

        # Seed with bootstrap nodes
        for node in BOOTSTRAP_NODES:
            self._nodes[node.node_id] = node

        if self._cache_path and self._cache_path.exists():
            self._load_cache()

    async def discover(
        self,
        region_preference: Optional[str] = None,
        min_uptime: float = 0.8,
        max_results: int = 10,
    ) -> List[RelayNode]:
        """
        Discover available relay nodes.
        Tries multiple channels in order of reliability.
        """
        # Try to refresh from directory first
        await self._refresh_from_directory()

        # Filter and rank nodes
        candidates = [
            n for n in self._nodes.values()
            if n.uptime_score >= min_uptime and n.is_fresh()
        ]

        if region_preference:
            preferred = [n for n in candidates if n.region == region_preference]
            others = [n for n in candidates if n.region != region_preference]
            candidates = preferred + others

        candidates.sort(key=lambda n: n.uptime_score, reverse=True)
        return candidates[:max_results]

    async def probe_node(self, node: RelayNode) -> bool:
        """Check if a relay node is alive and responding."""
        try:
            async with httpx.AsyncClient(timeout=5) as http:
                resp = await http.get(f"{node.endpoint}/api/v1/health")
                if resp.status_code == 200:
                    node.last_seen = time.time()
                    return True
        except Exception:
            pass
        return False

    def register_node(self, node: RelayNode) -> None:
        """Add a node to the local registry."""
        self._nodes[node.node_id] = node
        if self._cache_path:
            self._save_cache()

    async def _refresh_from_directory(self) -> None:
        """Try to fetch fresh node list from directory servers."""
        for url in self.DIRECTORY_URLS:
            try:
                async with httpx.AsyncClient(timeout=5) as http:
                    resp = await http.get(url)
                    if resp.status_code == 200:
                        nodes_data = resp.json()
                        for node_data in nodes_data.get("nodes", []):
                            node = RelayNode.from_dict(node_data)
                            self._nodes[node.node_id] = node
                        logger.info(f"[Discovery] Refreshed {len(nodes_data.get('nodes', []))} nodes from {url}")
                        if self._cache_path:
                            self._save_cache()
                        return
            except Exception as e:
                logger.debug(f"[Discovery] Directory {url} unreachable: {e}")

        logger.info("[Discovery] Using cached/bootstrap nodes")

    def _save_cache(self) -> None:
        if not self._cache_path:
            return
        data = {
            "cached_at": time.time(),
            "nodes": [n.to_dict() for n in self._nodes.values()],
        }
        self._cache_path.write_text(json.dumps(data, indent=2))

    def _load_cache(self) -> None:
        if not self._cache_path or not self._cache_path.exists():
            return
        try:
            data = json.loads(self._cache_path.read_text())
            for node_data in data.get("nodes", []):
                node = RelayNode.from_dict(node_data)
                self._nodes[node.node_id] = node
            logger.info(f"[Discovery] Loaded {len(self._nodes)} nodes from cache")
        except Exception as e:
            logger.warning(f"[Discovery] Could not load cache: {e}")

    @property
    def known_nodes(self) -> List[RelayNode]:
        return list(self._nodes.values())
