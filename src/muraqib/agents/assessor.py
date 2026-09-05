"""Assessor: the only agent that forms a compliance judgement.

Flow per control: build a prompt from retrieved evidence -> call the model ->
parse against a strict schema -> enforce the citation gate. A schema violation
or a missing citation produces NOT_ASSESSABLE, never a silent pass.
"""

from __future__ import annotations

import logging

from ..graph.state import RunState
from ..guardrails.schema import SchemaGate, SchemaViolation
from ..llm.base import ChatMessage, ProviderError
from ..llm.router import BudgetExceeded
from ..models import Citation, Confidence, Control, Finding, Status
from .base import Agent
from .intake import IntakeAgent
from .prompts import ASSESSOR_SYSTEM, assessor_user_prompt

log = logging.getLogger("muraqib.assessor")


class AssessorAgent(Agent):
    name = "assessor"

    def run(self, state: RunState) -> RunState:
        ctx = self.ctx
        controls = ctx.corpus.controls(state.frameworks)
        facts = IntakeAgent.facts_of(state)
        risk_tier = state.risk.tier.value if state.risk else "unknown"

        with ctx.tracer.span("agent.assessor", run_id=state.run_id, controls=len(controls)):
            for control in controls:
                if control.id in state.findings:  # resumed run
                    continue
                try:
                    finding = self._assess_one(state, control, facts, risk_tier)
                except BudgetExceeded as exc:
                    state.note_error("assessor", str(exc))
                    self._audit("budget_exceeded", control_id=control.id, detail=str(exc))
                    break
                except ProviderError as exc:
                    state.note_error(f"assessor:{control.id}", str(exc))
                    self._audit("assessment_failed", control_id=control.id, error=str(exc)[:200])
                    finding = self._unassessable(
                        control, f"The model provider could not be reached for this control: {exc}"
                    )
                state.findings[control.id] = finding
                state.usage = ctx.router.usage
                self._audit(
                    "control_assessed",
                    control_id=control.id,
                    framework=control.framework.value,
                    status=finding.status.value,
                    confidence=finding.confidence.value,
                    citations=len(finding.citations),
                )
            state.model_used = ctx.router.model_name
            state.stage = "assessment_complete"
        return state

    # ------------------------------------------------------------------
    def _assess_one(self, state: RunState, control: Control, facts: str, risk_tier: str) -> Finding:
        ctx = self.ctx
        neighbour_ids = state.retrieved.get(control.id, [control.id])
        retrieved_block = "\n\n".join(
            c.as_document() for c in (ctx.corpus.control(i) for i in neighbour_ids) if c is not None
        )

        raw_evidence = state.config.controls_documented.get(control.id, "")
        evidence = ctx.redactor.redact(raw_evidence).text if raw_evidence else ""
        evidence = ctx.scanner.sanitise(evidence)

        prompt = assessor_user_prompt(
            control_id=control.id,
            framework=control.framework.value,
            domain=control.domain,
            title=control.title,
            question=control.question,
            intent=control.intent,
            evidence_hints=control.evidence_hints,
            retrieved_block=retrieved_block,
            facts_block=facts,
            client_evidence=evidence,
            risk_tier=risk_tier,
        )

        response = ctx.router.complete(
            [ChatMessage("system", ASSESSOR_SYSTEM), ChatMessage("user", prompt)],
            json_only=True,
        )

        try:
            parsed = SchemaGate.parse_finding(response.text)
        except SchemaViolation as exc:
            state.note_guardrail(
                {"type": "schema_violation", "control_id": control.id, "detail": str(exc)[:300]}
            )
            self._audit("schema_violation", control_id=control.id)
            return self._unassessable(
                control, "The model returned output that did not match the required schema."
            )

        finding = Finding(
            control_id=control.id,
            framework=control.framework,
            domain=control.domain,
            status=parsed.status,
            confidence=parsed.confidence,
            rationale=parsed.rationale.strip(),
            evidence=parsed.evidence,
            gaps=parsed.gaps,
            recommendation=parsed.recommendation.strip(),
            citations=[
                Citation(
                    control_id=c.control_id or control.id,
                    source=c.source,  # type: ignore[arg-type]
                    quote=c.quote[:300],
                    locator=c.locator,
                )
                for c in parsed.citations
            ],
        )
        return ctx.citation_gate.enforce(finding, neighbour_ids)

    @staticmethod
    def _unassessable(control: Control, reason: str) -> Finding:
        return Finding(
            control_id=control.id,
            framework=control.framework,
            domain=control.domain,
            status=Status.NOT_ASSESSABLE,
            confidence=Confidence.HIGH,
            rationale=reason,
            gaps=["Control could not be assessed in this run."],
            recommendation="Re-run this control once the underlying issue is resolved.",
            citations=[Citation(control_id=control.id, source="corpus", locator=control.id)],
        )
