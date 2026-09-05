"""Evidence maturity, assurance scoring, criticality and the domain rollup.

These encode the distinction the whole addition exists for: a control backed by
a policy document and a control backed by production telemetry are not the same
control, and a score that treats them identically is misleading.
"""

import pytest

from muraqib.graph.orchestrator import Orchestrator, build_context, new_run_id
from muraqib.models import (
    MATURITY_WEIGHT,
    PRODUCTION_GRADE,
    AssuranceDomain,
    EvidenceItem,
    EvidenceMaturity,
    Finding,
    Framework,
    PlatformConfig,
    Status,
)


class TestEvidenceMaturity:
    def test_ladder_is_ordered_strongest_to_weakest(self):
        order = [
            EvidenceMaturity.RUNTIME_VERIFIED,
            EvidenceMaturity.CONFIG_EXPORT,
            EvidenceMaturity.DOCUMENT,
            EvidenceMaturity.DESIGN,
            EvidenceMaturity.SIMULATED,
            EvidenceMaturity.NONE,
        ]
        weights = [MATURITY_WEIGHT[m] for m in order]
        assert weights == sorted(weights, reverse=True)
        assert MATURITY_WEIGHT[EvidenceMaturity.NONE] == 0.0

    def test_only_runtime_and_config_are_production_grade(self):
        assert set(PRODUCTION_GRADE) == {
            EvidenceMaturity.RUNTIME_VERIFIED,
            EvidenceMaturity.CONFIG_EXPORT,
        }

    def test_bare_string_is_treated_as_a_document_not_as_proof(self):
        """An unlabelled assertion is a claim on paper until someone says otherwise."""
        assert EvidenceItem.parse("SSO is enforced").maturity is EvidenceMaturity.DOCUMENT

    def test_typed_evidence_is_parsed(self):
        item = EvidenceItem.parse(
            {"text": "x", "maturity": "runtime_verified", "source": "Entra sign-in logs"}
        )
        assert item.maturity is EvidenceMaturity.RUNTIME_VERIFIED
        assert item.source == "Entra sign-in logs"

    def test_unknown_maturity_falls_back_to_document(self):
        assert EvidenceItem.parse({"text": "x", "maturity": "vibes"}).maturity is (
            EvidenceMaturity.DOCUMENT
        )

    def test_assurance_score_discounts_weak_evidence(self):
        base = Finding(control_id="X", framework=Framework.NDMO, status=Status.COMPLIANT)
        runtime = base.model_copy(update={"evidence_maturity": EvidenceMaturity.RUNTIME_VERIFIED})
        document = base.model_copy(update={"evidence_maturity": EvidenceMaturity.DOCUMENT})
        design = base.model_copy(update={"evidence_maturity": EvidenceMaturity.DESIGN})

        assert runtime.score == document.score == design.score == 1.0
        assert runtime.assurance_score > document.assurance_score > design.assurance_score
        assert runtime.production_grade and not document.production_grade


class TestCorpusTagging:
    def test_every_control_has_an_assurance_domain(self, corpus):
        assert all(isinstance(c.assurance_domain, AssuranceDomain) for c in corpus.all_controls())

    def test_domains_partition_the_corpus(self, corpus):
        by_domain = corpus.by_domain()
        assert sum(len(v) for v in by_domain.values()) == len(corpus.all_controls())

    def test_critical_controls_are_high_weight_and_binding(self, corpus):
        for control in corpus.critical_controls():
            pack = corpus.pack(control.framework)
            assert control.weight == 5, f"{control.id} is critical but not weight 5"
            assert pack.obligation.value == "binding_law", f"{control.id} critical but not binding"

    def test_a_meaningful_number_of_controls_are_critical(self, corpus):
        n = len(corpus.critical_controls())
        assert 20 <= n <= 60, f"{n} critical controls - the threshold is probably miscalibrated"

    def test_identity_domain_is_sparse_and_that_is_honest(self, corpus):
        """These are data-protection and AI-governance instruments, not IAM
        standards. A thin identity domain is a true finding about the corpus,
        not a bug - and the docs say so."""
        assert len(corpus.by_domain()[AssuranceDomain.IDENTITY]) >= 1


class TestAssuranceReporting:
    @pytest.fixture
    def report(self, settings, corpus):
        config = PlatformConfig(
            platform_name="Assurance Fixture",
            data_categories=["personal"],
            affects_individuals=True,
            controls_documented={
                "UAE_PDPL.SEC.01": {
                    "text": "Encryption enforced at rest and in transit; access reviews logged quarterly.",
                    "maturity": "runtime_verified",
                    "source": "KMS export + sign-in logs",
                },
                "UAE_PDPL.XB.01": {
                    "text": "Transfer register maintained and reviewed; safeguards documented and signed.",
                    "maturity": "config_export",
                },
                "UAE_PDPL.RTS.01": {
                    "text": "Rights procedure documented and approved; erasure tested end to end.",
                    "maturity": "design",
                },
            },
        )
        run_id = new_run_id()
        ctx = build_context(settings, run_id=run_id, corpus=corpus)
        return Orchestrator(ctx).run(config, [Framework.UAE_PDPL], run_id=run_id)

    def test_assurance_is_never_above_weighted_posture(self, report):
        assert report.overall_assurance_pct <= report.overall_weighted_pct

    def test_weak_evidence_pulls_assurance_below_posture(self, report):
        assert report.overall_assurance_pct < report.overall_weighted_pct

    def test_evidence_profile_counts_each_maturity(self, report):
        profile = report.evidence_profile
        assert profile.get("runtime_verified") == 1
        assert profile.get("config_export") == 1
        assert profile.get("none", 0) >= 1

    def test_design_evidence_is_downgraded_by_the_critic(self, report):
        """Intent is not operation. The deterministic critic enforces this as a
        rule, so it holds without any model call."""
        f = next(f for f in report.findings if f.control_id == "UAE_PDPL.RTS.01")
        assert f.critic_verdict == "downgraded"
        assert f.status is not Status.COMPLIANT

    def test_production_assurance_claim_is_blocked_and_says_why(self, report):
        claim = report.assurance_claim
        assert claim is not None
        assert claim.permitted is False
        assert claim.verdict == "BLOCKED"
        assert claim.blockers
        assert any("NOT EVIDENCED" in b or "maturity" in b for b in claim.blockers)

    def test_claim_is_separate_from_the_compliance_score(self, report):
        """Missing evidence is not a control failure - but it does stop you
        claiming the platform is assured. Two different statements."""
        assert report.overall_weighted_pct > 0
        assert report.assurance_claim.permitted is False

    def test_domain_rollup_covers_only_domains_in_scope(self, report, corpus):
        in_scope = {c.assurance_domain for c in corpus.controls([Framework.UAE_PDPL])}
        assert {d.domain for d in report.domain_coverage} == in_scope

    def test_domain_totals_reconcile_with_the_corpus(self, report, corpus):
        expected = len(corpus.controls([Framework.UAE_PDPL]))
        assert sum(d.total_controls for d in report.domain_coverage) == expected

    def test_framework_coverage_carries_an_assurance_column(self, report):
        assert all(c.assurance_score_pct <= c.weighted_score_pct for c in report.coverage)


class TestAssuranceClaimPermitted:
    def test_claim_is_permitted_when_every_critical_control_is_production_evidenced(
        self, settings, corpus
    ):
        criticals = [c for c in corpus.controls([Framework.UAE_PDPL]) if c.critical]
        assert criticals, "fixture assumes UAE_PDPL has critical controls"
        evidence = {
            c.id: {
                "text": (
                    "Control is implemented, enforced and monitored; configuration reviewed "
                    "and access reviews logged quarterly."
                ),
                "maturity": "runtime_verified",
                "source": "production telemetry",
            }
            for c in criticals
        }
        config = PlatformConfig(
            platform_name="Fully Evidenced",
            data_categories=["personal"],
            affects_individuals=True,
            controls_documented=evidence,
        )
        run_id = new_run_id()
        ctx = build_context(settings, run_id=run_id, corpus=corpus)
        report = Orchestrator(ctx).run(config, [Framework.UAE_PDPL], run_id=run_id)

        assert report.assurance_claim.permitted is True, report.assurance_claim.blockers
        assert report.assurance_claim.verdict == "PERMITTED"
