"""Runtime governance plane.

The assessment side of Muraqib answers "is this platform governed?" once, from
a description. This package answers a different question, continuously: "is
*this specific transaction* permitted, right now?"

Six gates run in a fixed order and fail closed. Any BLOCK stops the journey and
nothing downstream executes. Every decision, and every reason for it, is written
to the same hash-chained audit ledger the assessor uses, so a completed
transaction can be reconstructed end to end: principal -> session -> trace ->
agents -> tools -> sources -> model -> policy -> response.
"""

from .engine import GovernanceEngine
from .models import (
    GateName,
    GateResult,
    ModelCall,
    Principal,
    RetrievedItem,
    TransactionContext,
    TransactionDecision,
    Verdict,
)
from .policy import GovernancePolicy, load_policy

__all__ = [
    "GovernanceEngine",
    "GovernancePolicy",
    "load_policy",
    "GateName",
    "GateResult",
    "ModelCall",
    "Principal",
    "RetrievedItem",
    "TransactionContext",
    "TransactionDecision",
    "Verdict",
]
