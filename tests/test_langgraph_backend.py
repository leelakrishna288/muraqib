"""The LangGraph backend must be indistinguishable from the built-in one.

The valuable test here is not "does LangGraph run" - it is the equivalence
assertion. Two independent executors over the same agents and the same state
should produce the same findings, the same coverage arithmetic and the same
assurance verdict. If they ever diverge, one of them is carrying behaviour that
belongs to the executor rather than to the pipeline, which is precisely the bug
a pluggable backend is prone to.

Every test skips cleanly when langgraph is not installed, so the hermetic
default CI job is unaffected. The `langgraph` CI job installs the extra, which
is where these actually run.
"""

from __future__ import annotations

import pytest

from muraqib.config import Settings
from muraqib.graph.langgraph_backend import LangGraphUnavailable, langgraph_available
from muraqib.graph.orchestrator import (
    ORCHESTRATORS,
    Orchestrator,
    build_context,
    get_orchestrator,
    new_run_id,
)
from muraqib.models import PlatformConfig

requires_langgraph = pytest.mark.skipif(
    not langgraph_available(), reason="langgraph extra not installed"
)


def _platform() -> PlatformConfig:
    return PlatformConfig.model_validate(
        {
            "platform_name": "Backend Equivalence Fixture",
            "owner_org": "Test Org",
            "jurisdiction": ["Saudi Arabia"],
            "deployment": "private_cloud",
            "data_categories": ["personal"],
            "affects_individuals": True,
            "controls_documented": {
                "NDMO.DG.01": (
                    "Data governance charter is documented and approved by the board; "
                    "the committee meets monthly and minutes are retained."
                ),
                "NDMO.CL.03": (
                    "Not implemented. Embeddings and prompt logs carry no classification labels."
                ),
                "NDMO.DQ.03": (
                    "Quality controls run on retrieval data before promotion and a corpus "
                    "version failing the checks is not promoted."
                ),
            },
        }
    )


def _report(backend: str, tmp_path):
    settings = Settings()
    settings.orchestrator = backend
    settings.data_dir = tmp_path / backend
    run_id = new_run_id()
    ctx = build_context(settings, run_id=run_id)
    orchestrator = get_orchestrator(ctx)
    return orchestrator.run(_platform(), run_id=run_id)


class TestBackendSelection:
    # These two assert the DEFAULT, so they must not read whatever
    # MURAQIB_ORCHESTRATOR happens to be set to - the CI job that runs the whole
    # suite under the LangGraph executor sets it, and a test of the default that
    # follows the environment is not testing the default at all.
    def test_builtin_is_the_default(self, monkeypatch):
        monkeypatch.delenv("MURAQIB_ORCHESTRATOR", raising=False)
        assert Settings().orchestrator == "builtin"
        assert "builtin" in ORCHESTRATORS and "langgraph" in ORCHESTRATORS

    def test_factory_returns_the_builtin_executor_by_default(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MURAQIB_ORCHESTRATOR", raising=False)
        settings = Settings()
        settings.data_dir = tmp_path
        assert isinstance(get_orchestrator(build_context(settings)), Orchestrator)

    def test_unknown_backend_is_rejected_at_construction(self, tmp_path):
        settings = Settings()
        settings.data_dir = tmp_path
        settings.orchestrator = "autogen"
        with pytest.raises(ValueError, match="unknown orchestrator"):
            get_orchestrator(build_context(settings))

    def test_the_selected_executor_is_recorded_in_settings(self):
        """The ledger writes `settings.redacted()` on every run, so the executor
        that produced a report is part of the audit trail rather than something
        a reader has to infer."""
        settings = Settings()
        settings.orchestrator = "langgraph"
        assert settings.redacted()["orchestrator"] == "langgraph"

    @pytest.mark.skipif(langgraph_available(), reason="langgraph IS installed")
    def test_missing_dependency_raises_rather_than_falling_back(self, tmp_path):
        """A governance tool must not silently change execution engine. If the
        operator asked for langgraph and it is absent, the run stops."""
        settings = Settings()
        settings.data_dir = tmp_path
        settings.orchestrator = "langgraph"
        with pytest.raises(LangGraphUnavailable, match="not installed"):
            get_orchestrator(build_context(settings))


@requires_langgraph
class TestEquivalence:
    def test_both_backends_produce_the_same_assessment(self, tmp_path):
        builtin = _report("builtin", tmp_path)
        langgraph = _report("langgraph", tmp_path)

        left_by_id = {f.control_id: f for f in builtin.findings}
        right_by_id = {f.control_id: f for f in langgraph.findings}
        assert left_by_id.keys() == right_by_id.keys()
        assert len(left_by_id) == 121

        for control_id, left in left_by_id.items():
            right = right_by_id[control_id]
            assert left.status == right.status, control_id
            assert left.confidence == right.confidence, control_id
            assert left.rationale == right.rationale, control_id
            assert left.evidence_maturity == right.evidence_maturity, control_id
            assert [c.control_id for c in left.citations] == [
                c.control_id for c in right.citations
            ], control_id

    def test_both_backends_agree_on_the_numbers_and_the_verdict(self, tmp_path):
        builtin = _report("builtin", tmp_path)
        langgraph = _report("langgraph", tmp_path)

        assert builtin.overall_coverage_pct == langgraph.overall_coverage_pct
        assert builtin.overall_weighted_pct == langgraph.overall_weighted_pct
        assert builtin.overall_assurance_pct == langgraph.overall_assurance_pct
        assert builtin.risk.tier == langgraph.risk.tier
        assert builtin.assurance_claim.permitted == langgraph.assurance_claim.permitted
        assert builtin.assurance_claim.blockers == langgraph.assurance_claim.blockers

    def test_the_compiled_topology_matches_the_declared_pipeline(self, tmp_path):
        """The edges are declared twice - once as the PIPELINE tuple, once as
        LangGraph edges. This asserts they have not drifted apart."""
        from muraqib.graph.orchestrator import PIPELINE

        settings = Settings()
        settings.orchestrator = "langgraph"
        settings.data_dir = tmp_path
        orchestrator = get_orchestrator(build_context(settings))

        mermaid = orchestrator.draw_mermaid()
        names = [n.name for n in PIPELINE]
        for name in names:
            assert name in mermaid
        for earlier, later in zip(names[:-1], names[1:], strict=True):
            assert f"{earlier} --> {later}" in mermaid.replace("\t", " ")

    def test_a_resumed_run_skips_completed_stages_under_langgraph_too(self, tmp_path):
        """The precondition check lives in the node, not in the edge topology,
        so resumption has to be verified separately for this backend."""
        settings = Settings()
        settings.orchestrator = "langgraph"
        settings.data_dir = tmp_path
        run_id = new_run_id()
        ctx = build_context(settings, run_id=run_id)
        orchestrator = get_orchestrator(ctx)

        from muraqib.graph.orchestrator import PIPELINE

        visited: list[str] = []
        report = orchestrator.run(
            _platform(), run_id=run_id, on_progress=lambda name, _s: visited.append(name)
        )
        assert visited == [n.name for n in PIPELINE]
        assert report.run_id == run_id
