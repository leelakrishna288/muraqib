"""Adversarial critic.

Only reviews findings where over-claiming is expensive: a "compliant" or
"partial" verdict on a control with weight >= 4, or any "compliant" verdict on
a binding-law framework. Reviewing all 121 would triple the cost for no gain -
nobody over-claims their way into a false NON_COMPLIANT.

A downgrade moves compliant -> partial and partial -> non_compliant. The critic
can never upgrade a finding. That asymmetry is deliberate.
"""

from __future__ import annotations

from ..graph.state import RunState
from ..guardrails.schema import SchemaGate
from ..llm.base import ChatMessage, ProviderError
from ..llm.router import BudgetExceeded
from ..models import Confidence, Finding, Obligation, Status
from .base import Agent
from .prompts import CRITIC_SYSTEM, critic_user_prompt

_DOWNGRADE = {Status.COMPLIANT: Status.PARTIAL, Status.PARTIAL: Status.NON_COMPLIANT}


class CriticAgent(Agent):
    name = "critic"

    def run(self, state: RunState) -> RunState:
        ctx = self.ctx
        if not ctx.settings.enable_critic:
            return state
        with ctx.tracer.span("agent.critic", run_id=state.run_id):
            reviewed = downgraded = 0
            for control_id, finding in list(state.findings.items()):
                control = ctx.corpus.control(control_id)
                if control is None or not self._worth_reviewing(finding, control.weight, ctx):
                    continue
                try:
                    verdict = self._review(state, finding, control.question)
                except (ProviderError, BudgetExceeded) as exc:
                    state.note_error(f"critic:{control_id}", str(exc))
                    continue
                reviewed += 1
                if verdict.verdict == "downgraded" and finding.status in _DOWNGRADE:
                    downgraded += 1
                    state.findings[control_id] = finding.model_copy(
                        update={
                            "status": _DOWNGRADE[finding.status],
                            "confidence": Confidence.MEDIUM,
                            "critic_verdict": "downgraded",
                            "critic_note": verdict.note,
                            "gaps": [*finding.gaps, f"Adversarial review: {verdict.note}"],
                        }
                    )
                    self._audit(
                        "critic_downgrade",
                        control_id=control_id,
                        from_status=finding.status.value,
                        to_status=_DOWNGRADE[finding.status].value,
                    )
                else:
                    state.findings[control_id] = finding.model_copy(
                        update={"critic_verdict": "upheld", "critic_note": verdict.note}
                    )
            state.usage = ctx.router.usage
            state.stage = "critique_complete"
            self._audit("critique_complete", reviewed=reviewed, downgraded=downgraded)
        return state

    def _worth_reviewing(self, finding: Finding, weight: int, ctx) -> bool:  # noqa: ANN001
        if finding.status not in _DOWNGRADE:
            return False
        if weight >= 4:
            return True
        pack = ctx.corpus.pack(finding.framework)
        return pack.obligation is Obligation.BINDING_LAW and finding.status is Status.COMPLIANT

    def _review(self, state: RunState, finding: Finding, question: str):  # noqa: ANN202
        evidence = state.config.controls_documented.get(finding.control_id, "")
        evidence = self.ctx.redactor.redact(evidence).text if evidence else ""
        prompt = critic_user_prompt(
            control_id=finding.control_id,
            question=question,
            finding_json=finding.model_dump_json(
                include={"status", "confidence", "rationale", "evidence", "gaps"}
            ),
            client_evidence=self.ctx.scanner.sanitise(evidence),
        )
        resp = self.ctx.router.complete(
            [ChatMessage("system", CRITIC_SYSTEM), ChatMessage("user", prompt)],
            json_only=True,
            max_tokens=300,
        )
        return SchemaGate.parse_critic(resp.text)
