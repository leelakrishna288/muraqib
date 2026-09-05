"""Deterministic risk tiering.

A model is NOT asked to guess the risk tier. Tiering drives which controls are
mandatory and how a report reads, so it must be reproducible and explainable
line by line. Rules are derived from the structure used by the EU AI Act risk
pyramid and SDAIA's four-tier model; the mapping is Muraqib's own and is
stated as such - it is not a legal classification.
"""

from __future__ import annotations

from ..graph.state import RunState
from ..models import DataCategory, RiskAssessment, RiskTier
from .base import Agent

_SENSITIVE = {
    DataCategory.SENSITIVE_PERSONAL,
    DataCategory.HEALTH,
    DataCategory.BIOMETRIC,
    DataCategory.GOVERNMENT_ID,
    DataCategory.CHILDREN,
}


def assess_risk(config) -> RiskAssessment:  # noqa: ANN001 - PlatformConfig
    drivers: list[str] = []
    tier = RiskTier.MINIMAL

    has_personal = any(c in {DataCategory.PERSONAL, *_SENSITIVE} for c in config.data_categories)
    has_sensitive = any(c in _SENSITIVE for c in config.data_categories)

    if has_personal:
        drivers.append("Processes personal data.")
        tier = RiskTier.LIMITED
    if config.affects_individuals:
        drivers.append("Outputs affect individuals.")
        tier = RiskTier.LIMITED
    if has_sensitive:
        drivers.append("Processes special-category or sensitive personal data.")
        tier = RiskTier.HIGH
    if config.automated_decision_making and config.affects_individuals:
        drivers.append("Makes automated decisions that affect individuals.")
        tier = RiskTier.HIGH
    if (
        config.automated_decision_making
        and config.affects_individuals
        and not config.human_in_the_loop
    ):
        drivers.append("No human in the loop over decisions affecting individuals.")
        tier = RiskTier.HIGH
    if DataCategory.CHILDREN in config.data_categories:
        drivers.append("Processes data relating to children.")
        tier = RiskTier.HIGH
    if (
        DataCategory.BIOMETRIC in config.data_categories
        and config.automated_decision_making
        and not config.human_in_the_loop
    ):
        drivers.append(
            "Biometric data drives fully automated decisions - screen against EU AI Act "
            "Art. 5 prohibitions before proceeding."
        )
        tier = RiskTier.UNACCEPTABLE
    if config.cross_border_transfer and has_personal:
        drivers.append("Personal data crosses a border, engaging transfer controls.")
        if tier is RiskTier.MINIMAL:
            tier = RiskTier.LIMITED
    if not drivers:
        drivers.append("No personal data, individual impact or automated decisioning declared.")

    return RiskAssessment(
        tier=tier,
        drivers=drivers,
        rationale=(
            "Tier assigned deterministically from the declared platform configuration using "
            "Muraqib's documented rule set. This is a triage aid for prioritising controls, "
            "not a legal classification under any instrument."
        ),
        deterministic=True,
    )


class RiskAgent(Agent):
    name = "risk"

    def run(self, state: RunState) -> RunState:
        with self.ctx.tracer.span("agent.risk", run_id=state.run_id):
            state.risk = assess_risk(state.config)
            state.stage = "risk_complete"
            self._audit("risk_assessed", tier=state.risk.tier.value, drivers=state.risk.drivers)
        return state
