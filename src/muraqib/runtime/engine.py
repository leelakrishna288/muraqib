"""The governance engine: run the gates, fail closed, record everything."""

from __future__ import annotations

import logging

from ..observability.audit import AuditLedger
from ..observability.tracing import Tracer
from .gates import GATES
from .models import (
    SEVERITY,
    GateName,
    GateResult,
    TransactionContext,
    TransactionDecision,
    Verdict,
)
from .policy import GovernancePolicy

log = logging.getLogger("muraqib.runtime")


class GovernanceEngine:
    """Evaluates one transaction against a policy.

    Fail-closed is the default and it is enforced here rather than inside each
    gate, so the stopping rule is in one readable place:

    * BLOCK always stops the journey. Gates after it never run, and are recorded
      as NOT_RUN so the trace shows what was and was not evaluated.
    * NOT_EVIDENCED stops the journey when ``policy.fail_closed`` is true. That
      is the honest default: a gate that could not establish the fact it needed
      has not approved anything.
    * MASK and WARN continue, but the final verdict carries the highest severity
      any gate reached, so a masked response is never reported as a clean ALLOW.
    """

    def __init__(
        self,
        policy: GovernancePolicy | None = None,
        ledger: AuditLedger | None = None,
        tracer: Tracer | None = None,
    ):
        self.policy = policy or GovernancePolicy.default()
        self.ledger = ledger or AuditLedger(run_id="runtime")
        self.tracer = tracer or Tracer()

    def evaluate(self, ctx: TransactionContext) -> TransactionDecision:
        results: list[GateResult] = []
        stopped = False

        self.ledger.record(
            "transaction_started",
            actor=ctx.principal.audit_identity(),
            transaction_id=ctx.transaction_id,
            trace_id=ctx.trace_id,
            channel=ctx.principal.channel.value,
            agents=ctx.requested_agents,
            tools=ctx.requested_tools,
            sources=ctx.data_sources,
            model=(ctx.model.name if ctx.model else None),
            region=(ctx.model.region if ctx.model else None),
            policy=self.policy.version,
        )

        with self.tracer.span("runtime.transaction", transaction_id=ctx.transaction_id):
            for gate_cls in GATES:
                gate = gate_cls(self.policy)
                if stopped:
                    results.append(
                        GateResult(
                            gate=gate.name,
                            verdict=Verdict.NOT_RUN,
                            reasons=["Journey stopped at an earlier gate."],
                        )
                    )
                    continue

                with self.tracer.span(f"gate.{gate.name.value}"):
                    result = gate.run(ctx)
                results.append(result)

                self.ledger.record(
                    "gate_evaluated",
                    actor=f"gate:{gate.name.value}",
                    transaction_id=ctx.transaction_id,
                    verdict=result.verdict.value,
                    reasons=result.reasons,
                    duration_ms=result.duration_ms,
                )

                # BLOCK always stops. NOT_EVIDENCED stops only under fail-closed,
                # which is the difference between "this failed" and "we could not
                # tell" - the same distinction the assessment side draws.
                stop_here = result.verdict is Verdict.BLOCK or (
                    result.verdict is Verdict.NOT_EVIDENCED and self.policy.fail_closed
                )
                if stop_here:
                    stopped = True
                elif result.verdict is Verdict.MASK:
                    masked = result.evidence.get("masked_text")
                    if isinstance(masked, str):
                        ctx = ctx.model_copy(update={"response": masked})

        verdict = max(
            (r.verdict for r in results if r.verdict is not Verdict.NOT_RUN),
            key=lambda v: SEVERITY[v],
            default=Verdict.NOT_EVIDENCED,
        )
        release = next((r for r in results if r.gate is GateName.RELEASE), None)
        released = release is not None and release.verdict is Verdict.ALLOW

        masked_anywhere = any(r.verdict is Verdict.MASK for r in results)
        decision = TransactionDecision(
            transaction_id=ctx.transaction_id,
            trace_id=ctx.trace_id,
            principal=ctx.principal.audit_identity(),
            verdict=verdict,
            gates=results,
            released_response=ctx.response if released else None,
            masked=masked_anywhere,
            response_id=(release.evidence.get("response_id", "") if release else ""),
            provenance=(release.evidence.get("provenance", []) if release else []),
            policy_version=self.policy.version,
        )

        ok, _ = self.ledger.verify()
        decision = decision.model_copy(update={"ledger_verified": ok})
        self.ledger.record(
            "transaction_decided",
            actor="runtime",
            transaction_id=ctx.transaction_id,
            verdict=verdict.value,
            blocked_at=(decision.blocked_at.value if decision.blocked_at else None),
            released=released,
            masked=masked_anywhere,
            response_id=decision.response_id,
            ledger_verified=ok,
        )
        log.info(
            "transaction decided",
            extra={
                "transaction_id": ctx.transaction_id,
                "verdict": verdict.value,
                "blocked_at": decision.blocked_at.value if decision.blocked_at else None,
            },
        )
        return decision
