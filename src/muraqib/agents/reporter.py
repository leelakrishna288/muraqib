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

from collections import Counter

from ..graph.state import RunState
from ..models import (
    AssessmentReport,
    AssuranceClaim,
    AssuranceDomain,
    DomainCoverage,
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

            total_weight = earned_weight = earned_assurance = 0.0
            for finding in state.findings.values():
                control = ctx.corpus.control(finding.control_id)
                if control is None or not finding.counts_toward_coverage:
                    continue
                total_weight += control.weight
                earned_weight += control.weight * finding.score
                earned_assurance += control.weight * finding.assurance_score
            overall_weighted = (
                round(100.0 * earned_weight / total_weight, 2) if total_weight else 0.0
            )
            overall_assurance = (
                round(100.0 * earned_assurance / total_weight, 2) if total_weight else 0.0
            )

            domain_coverage = self._domain_coverage(state)
            claim = self._assurance_claim(state)
            evidence_profile = dict(
                Counter(f.evidence_maturity.value for f in state.findings.values())
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
                domain_coverage=domain_coverage,
                overall_coverage_pct=overall_cov,
                overall_weighted_pct=overall_weighted,
                overall_assurance_pct=overall_assurance,
                assurance_claim=claim,
                evidence_profile=evidence_profile,
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
                assurance_pct=overall_assurance,
                production_assurance_claim=claim.verdict,
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

        total_weight = earned = assured = 0.0
        for f in findings:
            control = self.ctx.corpus.control(f.control_id)
            if control is None or not f.counts_toward_coverage:
                continue
            total_weight += control.weight
            earned += control.weight * f.score
            assured += control.weight * f.assurance_score

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
            assurance_score_pct=round(100.0 * assured / total_weight, 2) if total_weight else 0.0,
        )

    def _domain_coverage(self, state: RunState) -> list[DomainCoverage]:
        """Roll every framework's controls up into four assurance domains.

        Clients do not want eight framework reports; they want to know which
        part of the estate is weak.
        """
        out: list[DomainCoverage] = []
        for domain in AssuranceDomain:
            controls = [
                c
                for c in self.ctx.corpus.controls(state.frameworks)
                if c.assurance_domain is domain
            ]
            if not controls:
                continue
            ids = {c.id for c in controls}
            findings = [f for f in state.findings.values() if f.control_id in ids]
            counts = Counter(f.status for f in findings)
            assessed = (
                counts[Status.COMPLIANT] + counts[Status.PARTIAL] + counts[Status.NON_COMPLIANT]
            )
            applicable = len(controls) - counts[Status.NOT_APPLICABLE]

            total_w = earned_w = assured_w = 0.0
            for f in findings:
                control = self.ctx.corpus.control(f.control_id)
                if control is None or not f.counts_toward_coverage:
                    continue
                total_w += control.weight
                earned_w += control.weight * f.score
                assured_w += control.weight * f.assurance_score

            crit = {c.id for c in controls if c.critical}
            out.append(
                DomainCoverage(
                    domain=domain,
                    total_controls=len(controls),
                    assessed=assessed,
                    not_assessable=counts[Status.NOT_ASSESSABLE],
                    coverage_pct=round(100.0 * assessed / applicable, 2) if applicable else 0.0,
                    weighted_score_pct=round(100.0 * earned_w / total_w, 2) if total_w else 0.0,
                    assurance_score_pct=round(100.0 * assured_w / total_w, 2) if total_w else 0.0,
                    critical_failures=sorted(
                        f.control_id
                        for f in findings
                        if f.control_id in crit and f.status is Status.NON_COMPLIANT
                    ),
                    critical_unevidenced=sorted(
                        f.control_id
                        for f in findings
                        if f.control_id in crit and f.status is Status.NOT_ASSESSABLE
                    ),
                )
            )
        return out

    def _assurance_claim(self, state: RunState) -> AssuranceClaim:
        """Can this platform claim production assurance?

        Separate from the compliance score, and stricter. Missing evidence is
        not a control failure - but a critical control that is unevidenced, or
        evidenced only by a document when the claim is about production, means
        you cannot honestly say the platform is assured.
        """
        blockers: list[str] = []
        production_grade = 0
        assessed = 0

        for finding in state.findings.values():
            control = self.ctx.corpus.control(finding.control_id)
            if control is None:
                continue
            if finding.status in (Status.COMPLIANT, Status.PARTIAL, Status.NON_COMPLIANT):
                assessed += 1
                # Only assessed findings may count here. Counting every
                # production-grade finding, including the not-assessable ones,
                # produced the report line "47 of 38 assessed controls carry
                # production-grade evidence" - a numerator drawn from a larger
                # population than its own denominator.
                if finding.production_grade:
                    production_grade += 1
            if not control.critical:
                continue
            if finding.status is Status.NON_COMPLIANT:
                blockers.append(f"{control.id} - critical control FAILS ({control.title})")
            elif finding.status is Status.NOT_ASSESSABLE:
                blockers.append(f"{control.id} - critical control NOT EVIDENCED ({control.title})")
            elif (
                finding.status in (Status.COMPLIANT, Status.PARTIAL)
                and not finding.production_grade
            ):
                blockers.append(
                    f"{control.id} - critical control evidenced only at "
                    f"'{finding.evidence_maturity.value}' maturity ({control.title})"
                )

        permitted = not blockers
        return AssuranceClaim(
            permitted=permitted,
            blockers=sorted(blockers),
            production_grade_controls=production_grade,
            total_assessed=assessed,
            rationale=(
                "Every critical control is satisfied and carries production-grade evidence "
                "(runtime-verified or verified configuration export)."
                if permitted
                else "A production assurance claim requires every critical control to be satisfied "
                "AND evidenced at runtime-verified or configuration-export maturity. Missing "
                "evidence is not itself a control failure, but it does prevent the claim."
            ),
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
