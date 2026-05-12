"""
BorderDNS — Registry

The authoritative source of truth for .border names.
Persists to JSON. Anchors registrations to BorderChain.

Name ownership = wallet address. Transfer = sign + pay fee.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .record import (DNSRecord, RecordType, validate_name,
                     REGISTRATION_FEE_BC, TRANSFER_FEE_BC, BORDER_TLD)

logger = logging.getLogger("border.dns.registry")


class DNSRegistry:
    def __init__(self, persist_path: Optional[str] = None):
        # name → list of records (a name can have multiple record types)
        self._records:  Dict[str, List[DNSRecord]] = {}
        # name → owner address
        self._owners:   Dict[str, str]             = {}
        self._persist_path = Path(persist_path) if persist_path else None
        self._total_fees_collected: float = 0.0

        if self._persist_path and self._persist_path.exists():
            self._load()

    # ── Registration ──────────────────────────────────────
    def register(self, record: DNSRecord,
                 fee_paid: float = 0.0) -> Tuple[bool, str]:
        name = record.name.lower()

        valid, reason = validate_name(name)
        if not valid:
            return False, reason

        if name in self._owners and self._owners[name] != record.owner_address:
            return False, f"Name already registered: {name}"

        if fee_paid < REGISTRATION_FEE_BC and name not in self._owners:
            return False, f"Registration fee required: {REGISTRATION_FEE_BC} BC"

        if name not in self._records:
            self._records[name]  = []
            self._owners[name]   = record.owner_address
            self._total_fees_collected += fee_paid
            logger.info(f"[DNS] Registered: {name} → {record.owner_address[:20]}...")
        else:
            # Update existing record of same type
            self._records[name] = [
                r for r in self._records[name]
                if r.record_type != record.record_type
            ]
            logger.info(f"[DNS] Updated: {name} ({record.record_type})")

        self._records[name].append(record)
        self._save()
        return True, "registered"

    def add_record(self, name: str, record: DNSRecord,
                   caller_address: str) -> Tuple[bool, str]:
        """Add an additional record to an already-registered name."""
        name = name.lower()
        if name not in self._owners:
            return False, "Name not registered"
        if self._owners[name] != caller_address:
            return False, "Not the owner"
        self._records[name].append(record)
        self._save()
        return True, "added"

    # ── Transfer ──────────────────────────────────────────
    def transfer(self, name: str, from_address: str,
                 to_address: str, fee_paid: float) -> Tuple[bool, str]:
        name = name.lower()
        if name not in self._owners:
            return False, "Name not registered"
        if self._owners[name] != from_address:
            return False, "Not the owner"
        if fee_paid < TRANSFER_FEE_BC:
            return False, f"Transfer fee required: {TRANSFER_FEE_BC} BC"

        self._owners[name] = to_address
        # Update owner_address on all records
        for r in self._records.get(name, []):
            r.owner_address = to_address
            r.updated_at    = time.time()

        self._total_fees_collected += fee_paid
        logger.info(f"[DNS] Transferred: {name} → {to_address[:20]}...")
        self._save()
        return True, "transferred"

    # ── Resolution ────────────────────────────────────────
    def resolve(self, name: str,
                record_type: Optional[RecordType] = None) -> List[DNSRecord]:
        name = name.lower()
        if not name.endswith(f".{BORDER_TLD}"):
            name = f"{name}.{BORDER_TLD}"

        records = self._records.get(name, [])
        records = [r for r in records if not r.is_expired]

        if record_type:
            records = [r for r in records if r.record_type == record_type]
        return records

    def resolve_address(self, name: str) -> Optional[str]:
        """Resolve name → BC wallet address."""
        records = self.resolve(name, RecordType.ADDRESS)
        if records:
            return records[0].value
        # Fall back: try DID record and extract address
        did_records = self.resolve(name, RecordType.DID)
        if did_records:
            did = did_records[0].value
            if did.startswith("did:border:"):
                return did.replace("did:border:", "")
        return None

    def resolve_did(self, name: str) -> Optional[str]:
        records = self.resolve(name, RecordType.DID)
        return records[0].value if records else None

    def resolve_services(self, name: str) -> List[DNSRecord]:
        return self.resolve(name, RecordType.SRV)

    def owner_of(self, name: str) -> Optional[str]:
        return self._owners.get(name.lower())

    def names_for(self, address: str) -> List[str]:
        return [name for name, owner in self._owners.items()
                if owner == address]

    # ── Search ────────────────────────────────────────────
    def search(self, query: str) -> List[str]:
        """Find names containing the query string."""
        q = query.lower()
        return [name for name in self._owners if q in name]

    def all_names(self) -> List[str]:
        return list(self._owners.keys())

    # ── Stats ─────────────────────────────────────────────
    @property
    def stats(self) -> dict:
        total_records = sum(len(v) for v in self._records.values())
        return {
            "registered_names":     len(self._owners),
            "total_records":        total_records,
            "total_fees_collected": round(self._total_fees_collected, 8),
            "tld":                  BORDER_TLD,
            "registration_fee_bc":  REGISTRATION_FEE_BC,
            "transfer_fee_bc":      TRANSFER_FEE_BC,
        }

    # ── Persistence ───────────────────────────────────────
    def _save(self) -> None:
        if not self._persist_path:
            return
        data = {
            "owners":  self._owners,
            "records": {k: [r.to_dict() for r in v]
                        for k, v in self._records.items()},
            "fees":    self._total_fees_collected,
        }
        self._persist_path.write_text(json.dumps(data, indent=2))

    def _load(self) -> None:
        if not self._persist_path or not self._persist_path.exists():
            return
        data = json.loads(self._persist_path.read_text())
        self._owners = data.get("owners", {})
        self._total_fees_collected = data.get("fees", 0.0)
        for name, records in data.get("records", {}).items():
            self._records[name] = [DNSRecord.from_dict(r) for r in records]
        logger.info(f"[DNS] Loaded: {len(self._owners)} names")
