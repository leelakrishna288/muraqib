"""Runtime governance domain model."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Verdict(str, Enum):
    """A gate's outcome.

    NOT_EVIDENCED is not a synonym for BLOCK. It means the gate could not
    establish the fact it needed - the policy did not say, or the transaction
    did not carry the attribute. Whether that stops the journey is a policy
    decision (`fail_closed`), not the gate's to make, and the distinction is
    exactly the one the assessment side draws between "fails" and "unevidenced".
    """

    ALLOW = "allow"
    WARN = "warn"
    MASK = "mask"
    BLOCK = "block"
    NOT_EVIDENCED = "not_evidenced"
    NOT_RUN = "not_run"


TERMINAL = {Verdict.BLOCK}
SEVERITY = {
    Verdict.ALLOW: 0,
    Verdict.NOT_RUN: 0,
    Verdict.WARN: 1,
    Verdict.MASK: 2,
    Verdict.NOT_EVIDENCED: 3,
    Verdict.BLOCK: 4,
}


class GateName(str, Enum):
    IDENTITY = "identity"
    ENTITLEMENT = "entitlement"
    SOURCE_AUTHORIZATION = "source_authorization"
    PRE_INFERENCE = "pre_inference"
    OUTPUT_PROTECTION = "output_protection"
    RELEASE = "release"


class Channel(str, Enum):
    WEB = "web"
    CHAT = "chat"
    API = "api"
    EMAIL = "email"
    UNKNOWN = "unknown"


class Classification(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"
    UNKNOWN = "unknown"


CLASSIFICATION_RANK = {
    Classification.PUBLIC: 0,
    Classification.INTERNAL: 1,
    Classification.CONFIDENTIAL: 2,
    Classification.RESTRICTED: 3,
    Classification.UNKNOWN: 99,
}


class Principal(BaseModel):
    """Who is asking, and on whose behalf."""

    model_config = ConfigDict(frozen=True)

    subject: str
    authenticated: bool = False
    mfa: bool = False
    roles: tuple[str, ...] = ()
    groups: tuple[str, ...] = ()
    channel: Channel = Channel.UNKNOWN
    delegated_identity: bool = Field(
        default=False,
        description=(
            "True when downstream calls carry the user's own identity (on-behalf-of) "
            "rather than a shared service principal. Without it, source-level "
            "authorisation downstream is unenforceable."
        ),
    )
    issuer: str = ""

    @property
    def entitlement_keys(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((*self.roles, *self.groups)))

    def audit_identity(self) -> str:
        """Stable, non-PII identity for the ledger."""
        digest = hashlib.sha256(f"{self.issuer}|{self.subject}".encode()).hexdigest()[:16]
        return f"principal:{digest}"


class ModelCall(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    provider: str = ""
    deployment: str = ""
    region: str = ""
    endpoint: str = ""


class RetrievedItem(BaseModel):
    """One piece of context the agent pulled in before the model saw it."""

    model_config = ConfigDict(frozen=True)

    source: str
    owner: str = ""
    classification: Classification = Classification.UNKNOWN
    excerpt: str = ""


class TransactionContext(BaseModel):
    """Everything a governance decision needs about one request."""

    transaction_id: str = Field(default_factory=lambda: f"TXN-{uuid.uuid4().hex[:12]}")
    trace_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    session_id: str = ""
    created_at: datetime = Field(default_factory=_now)

    principal: Principal
    prompt: str = ""
    requested_agents: list[str] = Field(default_factory=list)
    requested_tools: list[str] = Field(default_factory=list)
    data_sources: list[str] = Field(default_factory=list)
    retrieved: list[RetrievedItem] = Field(default_factory=list)
    model: ModelCall | None = None
    response: str | None = None
    prompt_redacted: bool = Field(
        default=False,
        description="True when PII redaction has already been applied to the prompt.",
    )
    record_count: int = Field(
        default=0,
        description="Rows or documents the response would return. Bulk-extraction signal.",
    )


class GateResult(BaseModel):
    gate: GateName
    verdict: Verdict
    reasons: list[str] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)
    duration_ms: float = 0.0

    @property
    def blocking(self) -> bool:
        return self.verdict in TERMINAL


class TransactionDecision(BaseModel):
    transaction_id: str
    trace_id: str
    decided_at: datetime = Field(default_factory=_now)
    principal: str
    verdict: Verdict
    gates: list[GateResult] = Field(default_factory=list)
    released_response: str | None = None
    masked: bool = False
    response_id: str = ""
    provenance: list[str] = Field(default_factory=list)
    ledger_verified: bool = True
    policy_version: str = ""
    disclaimer: str = (
        "Runtime governance decision produced by an automated policy engine. It "
        "enforces the configured policy; it is not a legal determination and does "
        "not certify compliance."
    )

    @property
    def allowed(self) -> bool:
        return self.verdict in (Verdict.ALLOW, Verdict.WARN, Verdict.MASK)

    @property
    def blocked_at(self) -> GateName | None:
        for g in self.gates:
            if g.blocking:
                return g.gate
        return None

    def trace(self) -> list[str]:
        """The reconstruction path an auditor asks for."""
        return [
            f"principal={self.principal}",
            f"session={self.transaction_id}",
            f"trace={self.trace_id}",
            *[f"{g.gate.value}={g.verdict.value}" for g in self.gates],
            f"response={self.response_id or 'not_released'}",
        ]
