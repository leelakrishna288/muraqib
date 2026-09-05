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


class EvidenceMaturity(str, Enum):
    """How strong the evidence behind a finding actually is.

    A control backed by a policy PDF and a control backed by production
    telemetry are not the same control, and scoring them identically is how
    compliance programmes convince themselves they are further along than they
    are. The ladder runs strongest to weakest.
    """

    RUNTIME_VERIFIED = "runtime_verified"  # observed in production logs / telemetry
    CONFIG_EXPORT = "config_export"  # verified configuration export from the live system
    DOCUMENT = "document"  # policy, procedure or signed record
    DESIGN = "design"  # design intent, not yet built
    SIMULATED = "simulated"  # POC or synthetic demonstration
    NONE = "none"  # asserted, unevidenced


# Multiplier applied to a finding's score. Design and simulated evidence can
# still describe a real control, but they cannot support a production assurance
# claim, so they are discounted rather than discarded.
MATURITY_WEIGHT: dict[EvidenceMaturity, float] = {
    EvidenceMaturity.RUNTIME_VERIFIED: 1.00,
    EvidenceMaturity.CONFIG_EXPORT: 0.85,
    EvidenceMaturity.DOCUMENT: 0.60,
    EvidenceMaturity.DESIGN: 0.35,
    EvidenceMaturity.SIMULATED: 0.25,
    EvidenceMaturity.NONE: 0.00,
}

# Evidence at or above this rung can support a production assurance claim.
PRODUCTION_GRADE = {EvidenceMaturity.RUNTIME_VERIFIED, EvidenceMaturity.CONFIG_EXPORT}


class AssuranceDomain(str, Enum):
    """Cross-framework rollup.

    Clients do not want eight separate framework reports; they want to know
    which part of their estate is weak. Every control maps to exactly one
    domain, so a single view spans NDMO, GDPR, the EU AI Act and the rest.
    """

    IDENTITY = "identity"  # who is asking, and what may they reach
    DATA = "data"  # what data exists, how it is classified and handled
    AI_PLATFORM = "ai_platform"  # models, agents, tools, retrieval, output
    CROSS_CUTTING = "cross_cutting"  # governance, accountability, assurance, incident handling


class EvidenceItem(BaseModel):
    """One piece of declared evidence, with its provenance and maturity."""

    model_config = ConfigDict(frozen=True)

    text: str
    maturity: EvidenceMaturity = EvidenceMaturity.NONE
    source: str = ""

    @classmethod
    def parse(cls, raw: Any) -> EvidenceItem:
        """Accept either a bare string (legacy, treated as DOCUMENT) or a dict.

        A bare string is deliberately DOCUMENT, not RUNTIME_VERIFIED: an
        unlabelled assertion is a claim on paper until someone says otherwise.
        """
        if isinstance(raw, str):
            return cls(text=raw, maturity=EvidenceMaturity.DOCUMENT)
        if isinstance(raw, dict):
            maturity = raw.get("maturity", "document")
            try:
                m = EvidenceMaturity(str(maturity).lower())
            except ValueError:
                m = EvidenceMaturity.DOCUMENT
            return cls(text=str(raw.get("text", "")), maturity=m, source=str(raw.get("source", "")))
        return cls(text=str(raw), maturity=EvidenceMaturity.DOCUMENT)


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
    assurance_domain: AssuranceDomain = AssuranceDomain.CROSS_CUTTING
    critical: bool = Field(
        default=False,
        description=(
            "A critical control cannot be left unevidenced without blocking a "
            "production assurance claim, and cannot fail without blocking go-live."
        ),
    )
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
    controls_documented: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Client-declared evidence keyed by control id. Each value is either a "
            "bare string (treated as DOCUMENT maturity) or an object with "
            "{text, maturity, source} - see EvidenceItem."
        ),
    )

    def evidence_for(self, control_id: str) -> EvidenceItem | None:
        raw = self.controls_documented.get(control_id)
        return None if raw is None else EvidenceItem.parse(raw)

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
    evidence_maturity: EvidenceMaturity = EvidenceMaturity.NONE
    evidence_source: str = ""

    @property
    def score(self) -> float:
        return STATUS_SCORE[self.status]

    @property
    def assurance_score(self) -> float:
        """Score discounted by how strong the evidence actually is.

        This is the number that separates "we have a policy about it" from
        "we can show it running".
        """
        return STATUS_SCORE[self.status] * MATURITY_WEIGHT[self.evidence_maturity]

    @property
    def production_grade(self) -> bool:
        return self.evidence_maturity in PRODUCTION_GRADE

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
    assurance_score_pct: float = 0.0


class DomainCoverage(BaseModel):
    """One assurance domain, rolled up across every framework in scope."""

    domain: AssuranceDomain
    total_controls: int = 0
    assessed: int = 0
    not_assessable: int = 0
    coverage_pct: float = 0.0
    weighted_score_pct: float = 0.0
    assurance_score_pct: float = 0.0
    critical_failures: list[str] = Field(default_factory=list)
    critical_unevidenced: list[str] = Field(default_factory=list)


class AssuranceClaim(BaseModel):
    """Whether the evidence supports a production assurance claim.

    Deliberately separate from the compliance score. Missing evidence is not a
    control failure - but a critical control with no production-grade evidence
    does prevent you from claiming the platform is assured, which is a
    different and more honest statement than "we scored 74%".
    """

    permitted: bool
    blockers: list[str] = Field(default_factory=list)
    rationale: str = ""
    production_grade_controls: int = 0
    total_assessed: int = 0

    @property
    def verdict(self) -> str:
        return "PERMITTED" if self.permitted else "BLOCKED"


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
    domain_coverage: list[DomainCoverage] = Field(default_factory=list)
    overall_coverage_pct: float = 0.0
    overall_weighted_pct: float = 0.0
    overall_assurance_pct: float = 0.0
    assurance_claim: AssuranceClaim | None = None
    evidence_profile: dict[str, int] = Field(default_factory=dict)
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
