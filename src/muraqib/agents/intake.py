"""Intake: validate, scan, redact, and normalise the untrusted platform config.

Everything downstream depends on this node having done three things:
rejected obvious prompt injection, removed personal data from anything that
will be sent to a model, and flattened the config into a stable fact block.
"""

from __future__ import annotations

from ..graph.state import RunState
from ..models import PlatformConfig
from .base import Agent


class IntakeBlocked(RuntimeError):
    """Raised when untrusted input carries a high-severity injection attempt."""


def flatten_facts(config: PlatformConfig) -> str:
    lines = [
        f"platform_name: {config.platform_name}",
        f"owner_org: {config.owner_org or 'unknown'}",
        f"jurisdiction: {', '.join(config.jurisdiction) or 'unknown'}",
        f"purpose: {config.purpose or 'unstated'}",
        f"deployment: {config.deployment}",
        f"data_categories: {', '.join(c.value for c in config.data_categories) or 'none declared'}",
        f"data_residency: {config.data_residency or 'unknown'}",
        f"cross_border_transfer: {str(config.cross_border_transfer).lower()}",
        f"affects_individuals: {str(config.affects_individuals).lower()}",
        f"automated_decision_making: {str(config.automated_decision_making).lower()}",
        f"human_in_the_loop: {str(config.human_in_the_loop).lower()}",
        f"sources: {', '.join(config.sources) or 'none declared'}",
    ]
    for ep in config.endpoints:
        lines.append(
            f"endpoint: name={ep.name} method={ep.method} authenticated={str(ep.authenticated).lower()} "
            f"auth_scheme={ep.auth_scheme or 'none'} rate_limited={str(ep.rate_limited).lower()} "
            f"logs_requests={str(ep.logs_requests).lower()} pii_redacted={str(ep.pii_redacted).lower()}"
        )
    for m in config.models:
        lines.append(
            f"model: name={m.name} provider={m.provider or 'unknown'} hosting={m.hosting} "
            f"region={m.region or 'unknown'} fine_tuned={str(m.fine_tuned).lower()} "
            f"training_data_documented={str(m.training_data_documented).lower()} "
            f"human_oversight={str(m.human_oversight).lower()} "
            f"explainability={m.explainability_method or 'none'}"
        )
    if config.notes:
        lines.append(f"notes: {config.notes}")
    return "\n".join(lines)


class IntakeAgent(Agent):
    name = "intake"

    def run(self, state: RunState) -> RunState:
        ctx = self.ctx
        with ctx.tracer.span("agent.intake", run_id=state.run_id):
            raw = (
                flatten_facts(state.config)
                + "\n"
                + "\n".join(f"{k}: {v}" for k, v in state.config.controls_documented.items())
            )

            verdict = ctx.scanner.scan(raw)
            state.note_guardrail(verdict.as_event("platform_config"))
            self._audit(
                "injection_scan",
                clean=verdict.clean,
                severity=verdict.severity,
                rules=[m["rule"] for m in verdict.matches],
            )
            if verdict.should_block and ctx.settings.block_on_injection:
                self._audit("run_blocked", reason="high_severity_prompt_injection")
                raise IntakeBlocked(
                    "High-severity prompt-injection patterns were detected in the submitted "
                    "platform configuration. The run was stopped rather than producing an "
                    "assessment that may have been manipulated. Rules matched: "
                    + ", ".join(m["rule"] for m in verdict.matches)
                )

            redaction = ctx.redactor.redact(raw)
            state.note_guardrail(redaction.as_event("platform_config"))
            self._audit("pii_redaction", counts=redaction.counts)

            state.stage = "intake_complete"
            state.retrieved.setdefault("_facts", [redaction.text])
            self._audit(
                "intake_complete",
                platform=state.config.platform_name,
                frameworks=[f.value for f in state.frameworks],
                fingerprint=state.config.fingerprint,
            )
        return state

    @staticmethod
    def facts_of(state: RunState) -> str:
        return (state.retrieved.get("_facts") or [""])[0]
