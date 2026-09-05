"""The six governance gates.

Order is fixed and meaningful: identity before entitlement (you cannot
authorise an unknown principal), entitlement before source authorisation (an
unpermitted tool never gets to ask for data), source authorisation before
inference (the model must not see data the user could not open), inference
before output protection (you cannot DLP-scan a response you have not
generated), and release last.
"""

from __future__ import annotations

import abc
import hashlib
import re
import time

from ..guardrails.injection import InjectionScanner
from ..guardrails.pii import PIIRedactor
from .models import (
    CLASSIFICATION_RANK,
    Classification,
    GateName,
    GateResult,
    TransactionContext,
    Verdict,
)
from .policy import GovernancePolicy

_SECRET = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|"
    r"xox[baprs]-[A-Za-z0-9-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)"
)


class Gate(abc.ABC):
    name: GateName

    def __init__(self, policy: GovernancePolicy):
        self.policy = policy

    def run(self, ctx: TransactionContext) -> GateResult:
        started = time.perf_counter()
        result = self.evaluate(ctx)
        return result.model_copy(
            update={"duration_ms": round((time.perf_counter() - started) * 1000, 3)}
        )

    @abc.abstractmethod
    def evaluate(self, ctx: TransactionContext) -> GateResult: ...

    def _result(self, verdict: Verdict, reasons: list[str], **evidence) -> GateResult:  # noqa: ANN003
        return GateResult(gate=self.name, verdict=verdict, reasons=reasons, evidence=evidence)


class IdentityGate(Gate):
    """Who is asking, and is the claim strong enough to act on."""

    name = GateName.IDENTITY

    def evaluate(self, ctx: TransactionContext) -> GateResult:
        p, ip = ctx.principal, self.policy.identity
        blocks: list[str] = []
        unevidenced: list[str] = []

        if ip.require_authentication and not p.authenticated:
            blocks.append("Principal is not authenticated.")
        if ip.require_mfa and p.authenticated and not p.mfa:
            blocks.append("Multi-factor authentication was not satisfied.")
        if ip.require_delegated_identity and not p.delegated_identity:
            blocks.append(
                "No delegated (on-behalf-of) identity: downstream source-level "
                "authorisation cannot be enforced for this user."
            )
        if ip.allowed_channels and p.channel.value not in ip.allowed_channels:
            blocks.append(f"Channel '{p.channel.value}' is not an approved channel.")
        if not p.entitlement_keys:
            unevidenced.append("Principal carries no roles or groups; entitlement is undetermined.")

        if blocks:
            return self._result(Verdict.BLOCK, blocks, subject=p.audit_identity())
        if unevidenced:
            return self._result(Verdict.NOT_EVIDENCED, unevidenced, subject=p.audit_identity())
        return self._result(
            Verdict.ALLOW,
            ["Authenticated, MFA satisfied, delegated identity present, channel approved."],
            subject=p.audit_identity(),
            roles=list(p.entitlement_keys),
        )


class EntitlementGate(Gate):
    """Which agents and tools this principal may invoke. Deny by default."""

    name = GateName.ENTITLEMENT

    def evaluate(self, ctx: TransactionContext) -> GateResult:
        ent = self.policy.effective_entitlement(ctx.principal.entitlement_keys)
        denied_agents = [a for a in ctx.requested_agents if not ent.permits_agent(a)]
        denied_tools = [t for t in ctx.requested_tools if not ent.permits_tool(t)]

        if denied_agents or denied_tools:
            reasons = []
            if denied_agents:
                reasons.append(
                    f"Agents not entitled for this principal: {', '.join(denied_agents)}"
                )
            if denied_tools:
                reasons.append(f"Tools not entitled for this principal: {', '.join(denied_tools)}")
            return self._result(
                Verdict.BLOCK, reasons, denied_agents=denied_agents, denied_tools=denied_tools
            )

        if not ctx.requested_agents and not ctx.requested_tools:
            return self._result(Verdict.ALLOW, ["No agent or tool invocation requested."])

        return self._result(
            Verdict.ALLOW,
            [
                f"All requested components are within entitlement "
                f"({len(ctx.requested_agents)} agent(s), {len(ctx.requested_tools)} tool(s))."
            ],
            permitted_agents=ctx.requested_agents,
            permitted_tools=ctx.requested_tools,
        )


class SourceAuthorizationGate(Gate):
    """Whether the data actually retrieved was the principal's to see.

    Two separate checks, and the second is the one that catches real incidents:
    the source may be permitted while an individual retrieved document belongs
    to somebody else. Entitlement at the source level does not imply entitlement
    at the object level.
    """

    name = GateName.SOURCE_AUTHORIZATION

    def evaluate(self, ctx: TransactionContext) -> GateResult:
        ent = self.policy.effective_entitlement(ctx.principal.entitlement_keys)
        denied = [s for s in ctx.data_sources if not ent.permits_source(s)]
        cross_user = [
            f"{item.source}:{item.excerpt[:40] or 'object'}"
            for item in ctx.retrieved
            if item.owner and item.owner != ctx.principal.subject
        ]
        unclassified = [
            item.source for item in ctx.retrieved if item.classification is Classification.UNKNOWN
        ]

        if denied:
            return self._result(
                Verdict.BLOCK,
                [f"Data sources not permitted for this principal: {', '.join(denied)}"],
                denied_sources=denied,
            )
        if cross_user:
            return self._result(
                Verdict.BLOCK,
                [
                    "Retrieved content belongs to another user; source-level permission "
                    "does not grant object-level access: " + ", ".join(cross_user)
                ],
                cross_user_objects=cross_user,
            )
        if unclassified:
            return self._result(
                Verdict.NOT_EVIDENCED,
                [
                    "Retrieved content carries no classification label, so handling rules "
                    "cannot be applied: " + ", ".join(sorted(set(unclassified)))
                ],
                unclassified_sources=sorted(set(unclassified)),
            )
        return self._result(
            Verdict.ALLOW,
            [f"All {len(ctx.data_sources)} source(s) permitted and all objects user-owned."],
            sources=ctx.data_sources,
        )


class PreInferenceGate(Gate):
    """Everything that must be true before a prompt reaches a model."""

    name = GateName.PRE_INFERENCE

    def __init__(self, policy: GovernancePolicy):
        super().__init__(policy)
        self._redactor = PIIRedactor(enabled=True)
        self._scanner = InjectionScanner()

    def evaluate(self, ctx: TransactionContext) -> GateResult:
        r = self.policy.residency
        blocks: list[str] = []
        unevidenced: list[str] = []
        warns: list[str] = []

        if ctx.model is None:
            return self._result(
                Verdict.NOT_EVIDENCED, ["No model call declared for this transaction."]
            )

        if r.approved_models and ctx.model.name not in r.approved_models:
            blocks.append(f"Model '{ctx.model.name}' is not on the approved model list.")
        if (
            r.approved_providers
            and ctx.model.provider
            and ctx.model.provider not in r.approved_providers
        ):
            blocks.append(f"Provider '{ctx.model.provider}' is not approved.")

        if not ctx.model.region:
            unevidenced.append(
                "Model processing region is not declared; residency cannot be checked."
            )
        elif not r.region_allowed(ctx.model.region):
            if r.allow_cross_border:
                warns.append(
                    f"Processing region '{ctx.model.region}' is outside the approved set; "
                    "permitted only because cross-border processing is enabled."
                )
            else:
                blocks.append(
                    f"Processing region '{ctx.model.region}' is outside the approved set "
                    f"({', '.join(r.allowed_regions)}) and cross-border processing is disabled."
                )

        # Sensitive content must not cross a border even when the region is allowed.
        highest = max(
            (item.classification for item in ctx.retrieved),
            key=lambda c: CLASSIFICATION_RANK[c],
            default=Classification.PUBLIC,
        )
        if (
            ctx.model.region
            and not r.region_allowed(ctx.model.region)
            and highest in r.block_classifications_cross_border
        ):
            blocks.append(
                f"Context classified '{highest.value}' may not be processed outside the "
                "approved region regardless of the cross-border setting."
            )

        injection = self._scanner.scan(ctx.prompt)
        if injection.should_block:
            blocks.append(
                "Prompt contains high-severity injection patterns: "
                + ", ".join(m["rule"] for m in injection.matches)
            )

        if r.require_prompt_redaction and not ctx.prompt_redacted:
            found = self._redactor.redact(ctx.prompt)
            if found.redacted_any:
                blocks.append(
                    "Prompt carries unredacted personal data "
                    f"({', '.join(f'{k}x{v}' for k, v in found.counts.items())}) and redaction "
                    "before model invocation is required."
                )
            else:
                unevidenced.append(
                    "Prompt is not marked as redacted; no personal data was detected, but "
                    "the minimisation step is unevidenced."
                )

        if blocks:
            return self._result(
                Verdict.BLOCK, blocks, model=ctx.model.name, region=ctx.model.region
            )
        if unevidenced:
            return self._result(
                Verdict.NOT_EVIDENCED, unevidenced, model=ctx.model.name, region=ctx.model.region
            )
        if warns:
            return self._result(Verdict.WARN, warns, model=ctx.model.name, region=ctx.model.region)
        return self._result(
            Verdict.ALLOW,
            ["Approved model and region, prompt minimised, no injection detected."],
            model=ctx.model.name,
            region=ctx.model.region,
            highest_classification=highest.value,
        )


class OutputProtectionGate(Gate):
    """What may leave. ALLOW / WARN / MASK / BLOCK."""

    name = GateName.OUTPUT_PROTECTION

    def __init__(self, policy: GovernancePolicy):
        super().__init__(policy)
        self._redactor = PIIRedactor(enabled=True)

    def evaluate(self, ctx: TransactionContext) -> GateResult:
        d = self.policy.dlp
        if ctx.response is None:
            return self._result(Verdict.NOT_EVIDENCED, ["No response supplied to inspect."])

        text = ctx.response
        blocks: list[str] = []

        if d.block_on_secrets and _SECRET.search(text):
            blocks.append("Response contains credential-shaped material.")

        for phrase in d.blocked_phrases:
            if phrase.lower() in text.lower():
                blocks.append(f"Response contains a blocked phrase: {phrase!r}")

        leaked = [
            item.source
            for item in ctx.retrieved
            if item.classification in d.block_classifications
            and item.excerpt
            and item.excerpt[:40] in text
        ]
        if leaked:
            blocks.append(
                "Response reproduces content classified as "
                f"{', '.join(c.value for c in d.block_classifications)} from: "
                + ", ".join(sorted(set(leaked)))
            )

        if d.max_records and ctx.record_count > d.max_records:
            blocks.append(
                f"Response would return {ctx.record_count} records, above the "
                f"bulk-extraction threshold of {d.max_records}."
            )

        if blocks:
            return self._result(Verdict.BLOCK, blocks, record_count=ctx.record_count)

        if d.mask_pii:
            redaction = self._redactor.redact(text)
            if redaction.redacted_any:
                return self._result(
                    Verdict.MASK,
                    [
                        "Personal data masked before release: "
                        + ", ".join(f"{k}x{v}" for k, v in redaction.counts.items())
                    ],
                    masked_text=redaction.text,
                    counts=dict(redaction.counts),
                )

        return self._result(Verdict.ALLOW, ["No sensitive content detected in the response."])


class ReleaseGate(Gate):
    """The final decision, and the record of it."""

    name = GateName.RELEASE

    def evaluate(self, ctx: TransactionContext) -> GateResult:
        if ctx.response is None:
            return self._result(Verdict.NOT_EVIDENCED, ["Nothing to release."])
        response_id = (
            "RSP-"
            + hashlib.sha256(f"{ctx.transaction_id}|{ctx.response}".encode()).hexdigest()[:12]
        )
        provenance = sorted({item.source for item in ctx.retrieved})
        return self._result(
            Verdict.ALLOW,
            ["Released to the authenticated principal with provenance recorded."],
            response_id=response_id,
            provenance=provenance,
        )


GATES: tuple[type[Gate], ...] = (
    IdentityGate,
    EntitlementGate,
    SourceAuthorizationGate,
    PreInferenceGate,
    OutputProtectionGate,
    ReleaseGate,
)
