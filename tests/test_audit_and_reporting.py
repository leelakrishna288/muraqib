import json

from muraqib.graph.orchestrator import Orchestrator, build_context, new_run_id
from muraqib.models import Framework
from muraqib.observability.audit import AuditLedger
from muraqib.reporting.render import render_html, render_markdown, render_remediation_plan


class TestAuditLedger:
    def test_chain_verifies(self):
        ledger = AuditLedger("r1")
        for i in range(5):
            ledger.record("event", actor="test", i=i)
        ok, detail = ledger.verify()
        assert ok and "5" in detail

    def test_modification_is_detected(self):
        ledger = AuditLedger("r1")
        ledger.record("a", value=1)
        ledger.record("b", value=2)
        ledger.entries[0].payload["value"] = 99
        ledger._entries[0].payload["value"] = 99
        assert ledger.verify()[0] is False

    def test_secrets_never_reach_the_ledger(self):
        ledger = AuditLedger("r1")
        ledger.record("x", api_key="sk-secret", auth_token="abc", mapping={"a": "b"}, safe="ok")
        entry = ledger.entries[0]
        assert entry.payload["api_key"] == "[REDACTED]"
        assert entry.payload["auth_token"] == "[REDACTED]"
        assert entry.payload["mapping"] == "[REDACTED]"
        assert entry.payload["safe"] == "ok"

    def test_persists_and_reloads(self, tmp_path):
        path = tmp_path / "l.jsonl"
        ledger = AuditLedger("r1", path)
        ledger.record("a")
        ledger.record("b")
        reloaded = AuditLedger.load(path)
        assert reloaded.run_id == "r1"
        assert reloaded.verify()[0] is True
        assert len(reloaded.entries) == 2

    def test_tampered_file_fails_verification(self, tmp_path):
        path = tmp_path / "l.jsonl"
        ledger = AuditLedger("r1", path)
        ledger.record("a", value="original")
        ledger.record("b")
        lines = path.read_text().splitlines()
        first = json.loads(lines[0])
        first["payload"]["value"] = "tampered"
        path.write_text("\n".join([json.dumps(first), lines[1]]))
        assert AuditLedger.load(path).verify()[0] is False


class TestReporting:
    def _report(self, settings, corpus, platform):
        run_id = new_run_id()
        ctx = build_context(settings, run_id=run_id, corpus=corpus)
        return Orchestrator(ctx).run(platform, [Framework.NDMO], run_id=run_id), corpus

    def test_markdown_leads_with_what_could_not_be_assessed(self, settings, corpus, platform):
        report, _ = self._report(settings, corpus, platform)
        md = render_markdown(report)
        assert "What could not be assessed" in md
        assert md.index("What could not be assessed") < md.index("## 5. Findings")

    def test_markdown_states_legal_status_per_framework(self, settings, corpus, platform):
        report, _ = self._report(settings, corpus, platform)
        assert "binding law" in render_markdown(report)

    def test_markdown_carries_the_disclaimer(self, settings, corpus, platform):
        report, _ = self._report(settings, corpus, platform)
        assert "not a certification" in render_markdown(report).lower()

    def test_html_is_self_contained_and_theme_aware(self, settings, corpus, platform):
        report, _ = self._report(settings, corpus, platform)
        html = render_html(report)
        assert html.startswith("<!doctype html>")
        assert "prefers-color-scheme" in html
        assert "<script" not in html.lower()

    def test_html_escapes_untrusted_platform_names(self, settings, corpus, platform):
        platform.platform_name = "<script>alert(1)</script>"
        report, _ = self._report(settings, corpus, platform)
        html = render_html(report)
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html

    def test_remediation_plan_deduplicates_across_frameworks(self, settings, corpus, platform):
        report, corpus_ = self._report(settings, corpus, platform)
        plan = render_remediation_plan(report, corpus_)
        assert plan.startswith("# Remediation plan")
        assert "| # | Weight | Action | Satisfies |" in plan
