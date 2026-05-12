"""
BorderID — Decentralised Identity for the Border ecosystem.

Every node (relay, compute, storage) gets a DID tied to its wallet.
Claims and reputation scores flow through the same BorderChain.

Usage:
    from phantom.identity import BorderDID, IdentityRegistry, ReputationEngine

    wallet = BorderWallet.create()
    did    = BorderDID.from_wallet(wallet, handle="alice.border")
    registry = IdentityRegistry()
    registry.register(did)

    claim = VerifiableClaim.node_type(did.did, "COMPUTE")
    claim.sign(wallet)
    registry.add_claim(claim)

    engine = ReputationEngine(registry)
    score  = engine.score(did.did)
"""

from .did        import BorderDID, ServiceType, ServiceEndpoint, DID_METHOD
from .claim      import VerifiableClaim, ClaimType
from .registry   import IdentityRegistry
from .reputation import ReputationEngine, ReputationScore, MIN_SCORE_FOR_PRIORITY
from .node       import BorderIDNode, serve_identity

__all__ = [
    "BorderDID", "ServiceType", "ServiceEndpoint", "DID_METHOD",
    "VerifiableClaim", "ClaimType",
    "IdentityRegistry",
    "ReputationEngine", "ReputationScore", "MIN_SCORE_FOR_PRIORITY",
    "BorderIDNode", "serve_identity",
]
