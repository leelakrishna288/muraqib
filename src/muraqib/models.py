"""Core domain models.

Every structure an LLM is allowed to produce is defined here as a Pydantic
model. Nothing free-form from a model reaches the report: it is parsed,
validated and citation-checked first (see ``muraqib.guardrails``).
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _stable_id(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------
# Compliance corpus
# --------------------------------------------------------------------------


class Framework(str, Enum):
    """Governance instruments Muraqib can assess against.

    Naming is deliberate and matches the official title of each instrument.
    See docs/COMPLIANCE_SOURCES.md for citations and the legal status of each
    (several are non-binding guidance, not law).
    """

    NDMO = "NDMO"  # National Data Management Office standards (KSA)
    SDAIA_AI_ETHICS = "SDAIA_AI_ETHICS"  # SDAIA AI Ethics Principles v2.0 (KSA)
    KSA_PDPL = "KSA_PDPL"  # Personal Data Protection Law (KSA)
    UAE_PDPL = "UAE_PDPL"  # Federal Decree-Law No. 45 of 2021 (UAE)
    EU_AI_ACT = "EU_AI_ACT"  # Regulation (EU) 2024/1689 as amended by (EU) 2026/1744
    GDPR = "GDPR"  # Regulation (EU) 2016/679
    NIST_AI_RMF = "NIST_AI_RMF"  # NIST AI 100-1
    ISO_IEC_42001 = "ISO_IEC_42001"  # ISO/IEC 42001:2023 (clause references only)


class Obligation(str, Enum):
    """Whether the instrument is legally binding. Used by the risk engine."""

    BINDING_LAW = "binding_law"
    NON_BINDING_GUIDANCE = "non_binding_guidance"
    CERTIFIABLE_STANDARD = "certifiable_standard"


class Control(BaseModel):
    """A single assessable control.

    ``question`` is Muraqib's own paraphrased assessment question. We do not
    redistribute verbatim control text from instruments that carry no
    redistribution grant (NDMO, SDAIA, ISO/IEC 42001) - we cite the control ID
    and link to the official source instead. See docs/COMPLIANCE_SOURCES.md.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(description="Official control identifier, e.g. 'NDMO.DG.1'")
    framework: Framework
    domain: str
    title: str
    question: str = Field(description="Muraqib's paraphrased assessment question")
    intent: str = Field(default="", description="Why the control exists, in our words")
    evidence_hints: list[str] = Field(default_factory=list)
    weight: int = Field(default=1, ge=1, le=5)
    source_url: str = ""
    verbatim_text_included: bool = Field(
        default=False,
        description="True only where the source licence permits redistribution.",
    )

    def as_document(self) -> str:
        """Flatten to a retrievable document."""
        hints = "; ".join(self.evidence_hints)
        return (
            f"[{self.id}] {self.framework.value} / {self.domain} - {self.title}\n"
            f"Assessment question: {self.question}\n"
            f"Intent: {self.intent}\n"
            f"Evidence hints: {hints}"
        )


class FrameworkPack(BaseModel):
    """A loaded framework: metadata plus its controls."""

    framework: Framework
    official_name: str
    issuing_body: str
    jurisdiction: str
    obligation: Obligation
    status_note: str = ""
    source_url: str = ""
    licence_note: str = ""
    controls: list[Control] = Field(default_factory=list)

    @property
    def control_count(self) -> int:
        return len(self.controls)


# --------------------------------------------------------------------------
# The system under assessment
# --------------------------------------------------------------------------


class DataCategory(str, Enum):
    PERSONAL = "personal"
    SENSITIVE_PERSONAL = "sensitive_personal"
    HEALTH = "health"
    FINANCIAL = "financial"
    BIOMETRIC = "biometric"
    GOVERNMENT_ID = "government_id"
    CHILDREN = "children"
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"


class Endpoint(BaseModel):
    name: str
    url: str = ""
    method: str = "POST"
    authenticated: bool = False
    auth_scheme: str = ""
    rate_limited: bool = False
    logs_requests: bool = False
    pii_redacted: bool = False


class ModelSpec(BaseModel):
    name: str
    provider: str = ""
    hosting: Literal["saas", "private_cloud", "on_premise", "sovereign_cloud", "unknown"] = (
        "unknown"
    )
    region: str = ""
    fine_tuned: bool = False
    training_data_documented: bool = False
    human_oversight: bool = False
    explainability_method: str = ""


class PlatformConfig(BaseModel):
    """Untrusted input describing a client AI platform.

    This is DATA, never instructions. It is scanned for prompt injection
    (``guardrails.injection``) and redacted for PII (``guardrails.pii``)
    before any of it reaches a model.
    """

    platform_name: str
    owner_org: str = ""
    jurisdiction: list[str] = Field(default_factory=list)
    purpose: str = ""
    deployment: Literal[
        "saas", "private_cloud", "on_premise", "sovereign_cloud", "hybrid", "unknown"
    ] = "unknown"
    data_categories: list[DataCategory] = Field(default_factory=list)
    data_residency: str = ""
    cross_border_transfer: bool = False
    affects_individuals: bool = False
    automated_decision_making: bool = False
    human_in_the_loop: bool = False
    sources: list[str] = Field(default_factory=list)
    endpoints: list[Endpoint] = Field(default_factory=list)
    models: list[ModelSpec] = Field(default_factory=list)
    controls_documented: dict[str, str] = Field(
        default_factory=dict,
        description="Optional client-declared evidence, keyed by control id.",
    )
    notes: str = ""

    @field_validator("platform_name")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("platform_name must not be empty")
        return v.strip()

    @property
    def fingerprint(self) -> str:
        return _stable_id(self.platform_name, self.owner_org, self.deployment)


# --------------------------------------------------------------------------
# Assessment output
# --------------------------------------------------------------------------


class Status(str, Enum):
    """Deliberately includes NOT_ASSESSABLE.

    A governance tool that never says "I could not tell" is lying. Any finding
    without a citation is downgraded to NOT_ASSESSABLE by the citation gate.
    """

    COMPLIANT = "compliant"
    PARTIAL = "partial"
    NON_COMPLIANT = "non_compliant"
    NOT_APPLICABLE = "not_applicable"
    NOT_ASSESSABLE = "not_assessable"


STATUS_SCORE: dict[Status, float] = {
    Status.COMPLIANT: 1.0,
    Status.PARTIAL: 0.5,
    Status.NON_COMPLIANT: 0.0,
    Status.NOT_APPLICABLE: 0.0,
    Status.NOT_ASSESSABLE: 0.0,
}


class Citation(BaseModel):
    """Provenance for one finding. No citation, no finding."""

    control_id: str
    source: Literal["corpus", "platform_config", "client_evidence"]
    quote: str = ""
    locator: str = ""


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Finding(BaseModel):
    control_id: str
    framework: Framework
    domain: str = ""
    status: Status
    confidence: Confidence = Confidence.MEDIUM
    rationale: str = ""
    evidence: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    recommendation: str = ""
    citations: list[Citation] = Field(default_factory=list)
    critic_verdict: Literal["upheld", "downgraded", "not_reviewed"] = "not_reviewed"
    critic_note: str = ""

    @property
    def score(self) -> float:
        return STATUS_SCORE[self.status]

    @property
    def counts_toward_coverage(self) -> bool:
        return self.status is not Status.NOT_APPLICABLE


class RiskTier(str, Enum):
    """Four tiers, mirroring the tiering used by SDAIA's AI Ethics Principles
    and (separately) the EU AI Act's risk pyramid. Muraqib computes its own
    tier deterministically - it does not ask a model to guess it."""

    UNACCEPTABLE = "unacceptable"
    HIGH = "high"
    LIMITED = "limited"
    MINIMAL = "minimal"


class RiskAssessment(BaseModel):
    tier: RiskTier
    drivers: list[str] = Field(default_factory=list)
    rationale: str = ""
    deterministic: bool = True


class FrameworkCoverage(BaseModel):
    framework: Framework
    official_name: str = ""
    obligation: Obligation = Obligation.NON_BINDING_GUIDANCE
    total_controls: int = 0
    assessed: int = 0
    compliant: int = 0
    partial: int = 0
    non_compliant: int = 0
    not_applicable: int = 0
    not_assessable: int = 0
    coverage_pct: float = 0.0
    weighted_score_pct: float = 0.0


class TokenUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    calls: int = 0
    estimated_cost_usd: float = 0.0

    def add(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            calls=self.calls + other.calls,
            estimated_cost_usd=round(self.estimated_cost_usd + other.estimated_cost_usd, 6),
        )


class AssessmentReport(BaseModel):
    run_id: str
    created_at: datetime = Field(default_factory=utcnow)
    platform_name: str
    owner_org: str = ""
    frameworks: list[Framework] = Field(default_factory=list)
    risk: RiskAssessment
    findings: list[Finding] = Field(default_factory=list)
    coverage: list[FrameworkCoverage] = Field(default_factory=list)
    overall_coverage_pct: float = 0.0
    overall_weighted_pct: float = 0.0
    blocking_gaps: list[str] = Field(default_factory=list)
    usage: TokenUsage = Field(default_factory=TokenUsage)
    model_used: str = ""
    guardrail_events: list[dict[str, Any]] = Field(default_factory=list)
    disclaimer: str = (
        "This is a readiness and gap-analysis output produced by an automated "
        "system. It is not a certification, legal advice, or an attestation of "
        "compliance. Control text is paraphrased; consult the official source "
        "for authoritative wording."
    )

    def to_json(self) -> str:
        return self.model_dump_json(indent=2)
