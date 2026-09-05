"""Reporter: deterministic coverage mathematics and report assembly.

Coverage is computed, never generated. Two numbers are reported because they
answer different questions:

* **coverage_pct** - of the controls that apply, how many did we actually reach
  a usable verdict on? This is a measure of *evidence quality*.
* **weighted_score_pct** - of the available weight, how much is satisfied?
  Compliant scores 1.0, partial 0.5, everything else 0. This is a measure of
  *compliance posture*.

Reporting only one of them is how compliance dashboards end up lying. A
platform with 40% coverage and 95% weighted score has not been assessed; it has
been guessed at.
"""

from __future__ import annotations

from ..graph.state import RunState
from ..models import (
    AssessmentReport,
    Framework,
    FrameworkCoverage,
    Obligation,
    Status,
)
from .base import Agent


class ReporterAgent(Agent):
    name = "reporter"

    def run(self, state: RunState) -> RunState:
        ctx = self.ctx
        with ctx.tracer.span("agent.reporter", run_id=state.run_id):
            coverage = [self._coverage_for(state, fw) for fw in state.frameworks]
            state.coverage = coverage

            assessed = sum(c.assessed for c in coverage)
            applicable = sum(c.total_controls - c.not_applicable for c in coverage)
            overall_cov = round(100.0 * assessed / applicable, 2) if applicable else 0.0

            total_weight = earned_weight = 0.0
            for finding in state.findings.values():
                control = ctx.corpus.control(finding.control_id)
                if control is None or not finding.counts_toward_coverage:
                    continue
                total_weight += control.weight
                earned_weight += control.weight * finding.score
            overall_weighted = (
                round(100.0 * earned_weight / total_weight, 2) if total_weight else 0.0
            )

            report = AssessmentReport(
                run_id=state.run_id,
                platform_name=state.config.platform_name,
                owner_org=state.config.owner_org,
                frameworks=state.frameworks,
                risk=state.risk,  # type: ignore[arg-type]
                findings=sorted(
                    state.findings.values(),
                    key=lambda f: (f.framework.value, f.control_id),
                ),
                coverage=coverage,
                overall_coverage_pct=overall_cov,
                overall_weighted_pct=overall_weighted,
                blocking_gaps=self._blocking_gaps(state),
                usage=state.usage,
                model_used=state.model_used,
                guardrail_events=state.guardrail_events + ctx.citation_gate.as_events(),
            )
            state.report = report
            state.stage = "complete"
            self._audit(
                "report_generated",
                coverage_pct=overall_cov,
                weighted_pct=overall_weighted,
                findings=len(report.findings),
                blocking_gaps=len(report.blocking_gaps),
            )
        return state

    # ------------------------------------------------------------------
    def _coverage_for(self, state: RunState, framework: Framework) -> FrameworkCoverage:
        pack = self.ctx.corpus.pack(framework)
        findings = [f for f in state.findings.values() if f.framework is framework]
        counts = dict.fromkeys(Status, 0)
        for f in findings:
            counts[f.status] += 1

        applicable = pack.control_count - counts[Status.NOT_APPLICABLE]
        assessed = counts[Status.COMPLIANT] + counts[Status.PARTIAL] + counts[Status.NON_COMPLIANT]

        total_weight = earned = 0.0
        for f in findings:
            control = self.ctx.corpus.control(f.control_id)
            if control is None or not f.counts_toward_coverage:
                continue
            total_weight += control.weight
            earned += control.weight * f.score

        return FrameworkCoverage(
            framework=framework,
            official_name=pack.official_name,
            obligation=pack.obligation,
            total_controls=pack.control_count,
            assessed=assessed,
            compliant=counts[Status.COMPLIANT],
            partial=counts[Status.PARTIAL],
            non_compliant=counts[Status.NON_COMPLIANT],
            not_applicable=counts[Status.NOT_APPLICABLE],
            not_assessable=counts[Status.NOT_ASSESSABLE],
            coverage_pct=round(100.0 * assessed / applicable, 2) if applicable else 0.0,
            weighted_score_pct=round(100.0 * earned / total_weight, 2) if total_weight else 0.0,
        )

    def _blocking_gaps(self, state: RunState) -> list[str]:
        """High-weight failures on binding instruments. These are the ones a
        client must fix before go-live, not the long tail."""
        out: list[str] = []
        for finding in state.findings.values():
            if finding.status is not Status.NON_COMPLIANT:
                continue
            control = self.ctx.corpus.control(finding.control_id)
            if control is None or control.weight < 5:
                continue
            pack = self.ctx.corpus.pack(finding.framework)
            if pack.obligation is not Obligation.BINDING_LAW:
                continue
            out.append(
                f"{finding.control_id} - {control.title} ({pack.official_name.split('(')[0].strip()})"
            )
        return sorted(out)
