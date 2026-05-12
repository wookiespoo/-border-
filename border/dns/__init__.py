"""
BorderDNS — Decentralised naming for the Border network.

Register human-readable names like alice.border, gpu-farm-1.border.
Names are anchored to BorderID DIDs and wallet addresses.
No ICANN. No registrar. No renewal fees. You own the name.

Voted into existence by the BorderDAO (feature_border_dns = 1).

Usage:
    from border.dns import DNSRegistry, DNSResolver, DNSRecord, RecordType

    registry = DNSRegistry()
    resolver = DNSResolver(registry)

    # Register a name (costs 1 BC)
    record = DNSRecord.create(
        name="alice.border",
        record_type=RecordType.ADDRESS,
        value=wallet.address,
        owner_address=wallet.address,
    )
    record.sign(wallet)
    registry.register(record, fee_paid=1.0)

    # Resolve
    addr = resolver.resolve_address("alice.border")
    did  = resolver.resolve_did("alice.border")
"""

from .record   import (DNSRecord, RecordType, validate_name,
                        BORDER_TLD, REGISTRATION_FEE_BC, TRANSFER_FEE_BC,
                        MIN_NAME_LENGTH, MAX_NAME_LENGTH, NAME_TTL_DEFAULT)
from .registry import DNSRegistry
from .resolver import DNSResolver
from .node     import BorderDNSNode, serve_dns

__all__ = [
    "DNSRecord", "RecordType", "validate_name",
    "BORDER_TLD", "REGISTRATION_FEE_BC", "TRANSFER_FEE_BC",
    "MIN_NAME_LENGTH", "MAX_NAME_LENGTH", "NAME_TTL_DEFAULT",
    "DNSRegistry", "DNSResolver",
    "BorderDNSNode", "serve_dns",
]
