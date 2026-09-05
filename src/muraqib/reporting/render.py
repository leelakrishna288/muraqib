"""Report rendering: Markdown, HTML and a prioritised remediation plan.

Every report leads with what it could NOT assess. A compliance report that
buries its own blind spots is worse than no report, because it converts
ignorance into false assurance.
"""

from __future__ import annotations

import html
from collections import defaultdict

from ..models import AssessmentReport, Obligation, Status

_STATUS_LABEL = {
    Status.COMPLIANT: "Compliant",
    Status.PARTIAL: "Partial",
    Status.NON_COMPLIANT: "Non-compliant",
    Status.NOT_APPLICABLE: "Not applicable",
    Status.NOT_ASSESSABLE: "Not assessable",
}
_STATUS_COLOUR = {
    Status.COMPLIANT: "#137547",
    Status.PARTIAL: "#B4690E",
    Status.NON_COMPLIANT: "#B3261E",
    Status.NOT_APPLICABLE: "#5F6368",
    Status.NOT_ASSESSABLE: "#3B4A6B",
}
_OBLIGATION_LABEL = {
    Obligation.BINDING_LAW: "binding law",
    Obligation.NON_BINDING_GUIDANCE: "non-binding guidance",
    Obligation.CERTIFIABLE_STANDARD: "certifiable standard",
}


def render_markdown(report: AssessmentReport) -> str:
    lines: list[str] = []
    a = lines.append

    a(f"# AI Governance Readiness Assessment - {report.platform_name}")
    a("")
    a(f"**Run ID:** `{report.run_id}`  ")
    a(f"**Generated:** {report.created_at:%Y-%m-%d %H:%M UTC}  ")
    a(f"**Organisation:** {report.owner_org or 'not stated'}  ")
    a(f"**Assessment engine:** `{report.model_used or 'unknown'}`  ")
    a(
        f"**Model calls:** {report.usage.calls} · **Estimated cost:** ${report.usage.estimated_cost_usd:.4f}"
    )
    a("")
    a(f"> {report.disclaimer}")
    a("")

    a("## 1. Headline")
    a("")
    a("| Measure | Value | What it means |")
    a("|---|---|---|")
    a(
        f"| Assessment coverage | **{report.overall_coverage_pct:.1f}%** | "
        "Share of applicable controls where the supplied evidence supported a usable verdict. "
        "Low coverage means the assessment is incomplete, not that the platform is bad. |"
    )
    a(
        f"| Weighted posture | **{report.overall_weighted_pct:.1f}%** | "
        "Share of available control weight satisfied. Compliant scores full, partial scores half. |"
    )
    a(
        f"| Assurance score | **{report.overall_assurance_pct:.1f}%** | "
        "Weighted posture discounted by how strong the evidence actually is. A control "
        "backed by a policy PDF does not score like one backed by production telemetry. |"
    )
    a(
        f"| Risk tier | **{report.risk.tier.value.upper()}** | Deterministic triage tier (see section 2). |"
    )
    a(
        f"| Blocking gaps | **{len(report.blocking_gaps)}** | High-weight failures on binding instruments. |"
    )
    a("")

    if report.assurance_claim is not None:
        claim = report.assurance_claim
        a(f"### Production assurance claim: **{claim.verdict}**")
        a("")
        a(claim.rationale)
        a("")
        if claim.blockers:
            a("Blockers:")
            a("")
            for b in claim.blockers:
                a(f"- {b}")
            a("")
        a(
            f"_{claim.production_grade_controls} of {claim.total_assessed} assessed controls "
            "carry production-grade evidence (runtime-verified or verified configuration export)._"
        )
        a("")

    if report.evidence_profile:
        a("### Evidence profile")
        a("")
        a("| Maturity | Controls |")
        a("|---|---:|")
        order = [
            "runtime_verified",
            "config_export",
            "document",
            "design",
            "simulated",
            "none",
        ]
        for key in order:
            if key in report.evidence_profile:
                a(f"| {key.replace('_', ' ')} | {report.evidence_profile[key]} |")
        a("")

    if report.domain_coverage:
        a("### Assurance domains")
        a("")
        a("A single view across every framework in scope. Clients want to know which part of")
        a("the estate is weak, not which of eight documents mentions it.")
        a("")
        a(
            "| Domain | Controls | Assessed | Coverage | Weighted | Assurance | Critical FAIL | Critical unevidenced |"
        )
        a("|---|---:|---:|---:|---:|---:|---:|---:|")
        for dom in report.domain_coverage:
            a(
                f"| {dom.domain.value.replace('_', ' ')} | {dom.total_controls} | {dom.assessed} | "
                f"{dom.coverage_pct:.0f}% | {dom.weighted_score_pct:.0f}% | "
                f"{dom.assurance_score_pct:.0f}% | "
                f"{len(dom.critical_failures)} | {len(dom.critical_unevidenced)} |"
            )
        a("")

    not_assessable = [f for f in report.findings if f.status is Status.NOT_ASSESSABLE]
    if not_assessable:
        a(f"### What could not be assessed ({len(not_assessable)} controls)")
        a("")
        a("These controls returned no usable verdict. Read this before reading the score.")
        a("")
        by_fw: dict[str, list[str]] = defaultdict(list)
        for f in not_assessable:
            by_fw[f.framework.value].append(f.control_id)
        for fw, ids in sorted(by_fw.items()):
            a(f"- **{fw}** ({len(ids)}): {', '.join(sorted(ids))}")
        a("")

    a("## 2. Risk tier")
    a("")
    a(f"**{report.risk.tier.value.upper()}** - assigned deterministically, not by a model.")
    a("")
    for driver in report.risk.drivers:
        a(f"- {driver}")
    a("")
    a(f"_{report.risk.rationale}_")
    a("")

    a("## 3. Coverage by framework")
    a("")
    a(
        "| Framework | Status in law | Controls | Assessed | Compliant | Partial | Non-compliant | Not assessable | Coverage | Weighted |"
    )
    a("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for c in report.coverage:
        a(
            f"| {c.framework.value} | {_OBLIGATION_LABEL[c.obligation]} | {c.total_controls} | {c.assessed} | "
            f"{c.compliant} | {c.partial} | {c.non_compliant} | {c.not_assessable} | "
            f"{c.coverage_pct:.1f}% | {c.weighted_score_pct:.1f}% |"
        )
    a("")

    if report.blocking_gaps:
        a("## 4. Blocking gaps")
        a("")
        a("High-weight failures against instruments that are binding law. Address before go-live.")
        a("")
        for g in report.blocking_gaps:
            a(f"- {g}")
        a("")

    a("## 5. Findings")
    a("")
    by_framework: dict[str, list] = defaultdict(list)
    for f in report.findings:
        by_framework[f.framework.value].append(f)
    for fw, findings in sorted(by_framework.items()):
        a(f"### {fw}")
        a("")
        for f in sorted(findings, key=lambda x: x.control_id):
            a(
                f"#### `{f.control_id}` - {_STATUS_LABEL[f.status]} ({f.confidence.value} confidence)"
            )
            a("")
            if f.rationale:
                a(f.rationale)
                a("")
            if f.gaps:
                a("**Gaps**")
                for g in f.gaps:
                    a(f"- {g}")
                a("")
            if f.recommendation:
                a(f"**Recommendation:** {f.recommendation}")
                a("")
            if f.evidence_maturity.value != "none":
                src = f" (source: {f.evidence_source})" if f.evidence_source else ""
                a(f"**Evidence maturity:** {f.evidence_maturity.value.replace('_', ' ')}{src}")
                a("")
            if f.critic_verdict != "not_reviewed":
                a(f"**Adversarial review:** {f.critic_verdict} - {f.critic_note}")
                a("")
            if f.citations:
                cites = ", ".join(f"`{c.control_id}` ({c.source})" for c in f.citations)
                a(f"**Citations:** {cites}")
                a("")

    if report.guardrail_events:
        a("## 6. Guardrail events")
        a("")
        a("| Type | Detail |")
        a("|---|---|")
        for e in report.guardrail_events:
            detail = ", ".join(f"{k}={v}" for k, v in e.items() if k != "type")
            a(f"| {e.get('type', 'event')} | {detail[:220]} |")
        a("")

    a("---")
    a("")
    a("Generated by [Muraqib](https://github.com/leelakrishna288/muraqib). ")
    a(
        "Control text is paraphrased by Muraqib and is not a reproduction of any official "
        "standard. Consult the official source for authoritative wording."
    )
    return "\n".join(lines)


def render_remediation_plan(report: AssessmentReport, corpus) -> str:  # noqa: ANN001
    """Effort-ordered remediation list: highest weight failures first, with the
    cross-framework overlap collapsed so a client fixes each thing once."""
    rows: list[tuple[int, str, str, list[str]]] = []
    seen: dict[str, list[str]] = defaultdict(list)

    for f in report.findings:
        if f.status not in (Status.NON_COMPLIANT, Status.PARTIAL):
            continue
        control = corpus.control(f.control_id)
        if control is None:
            continue
        seen[control.title.lower()].append(f.control_id)

    done: set[str] = set()
    for f in report.findings:
        if f.status not in (Status.NON_COMPLIANT, Status.PARTIAL):
            continue
        control = corpus.control(f.control_id)
        if control is None or control.title.lower() in done:
            continue
        done.add(control.title.lower())
        rows.append(
            (
                control.weight,
                control.title,
                f.recommendation or "Implement and evidence this control.",
                seen[control.title.lower()],
            )
        )

    rows.sort(key=lambda r: (-r[0], r[1]))
    out = [
        "# Remediation plan",
        "",
        "Ordered by control weight. Each item is listed once, with every framework it satisfies.",
        "",
    ]
    out.append("| # | Weight | Action | Satisfies |")
    out.append("|---:|---:|---|---|")
    for i, (weight, title, rec, ids) in enumerate(rows, 1):
        out.append(f"| {i} | {weight} | **{title}** - {rec} | {', '.join(f'`{c}`' for c in ids)} |")
    if not rows:
        out.append("| - | - | No non-compliant or partial findings in this run. | - |")
    return "\n".join(out)


def render_html(report: AssessmentReport) -> str:
    def esc(x: object) -> str:
        return html.escape(str(x))

    cards = "".join(
        f'<div class="card"><div class="k">{esc(c.framework.value)}</div>'
        f'<div class="v">{c.weighted_score_pct:.0f}%</div>'
        f'<div class="s">{c.assessed}/{c.total_controls} assessed · {c.not_assessable} not assessable</div></div>'
        for c in report.coverage
    )
    rows = "".join(
        f"<tr><td><code>{esc(f.control_id)}</code></td>"
        f'<td><span class="pill" style="background:{_STATUS_COLOUR[f.status]}">{_STATUS_LABEL[f.status]}</span></td>'
        f"<td>{esc(f.confidence.value)}</td><td>{esc(f.rationale)}</td>"
        f"<td>{'<br>'.join(esc(g) for g in f.gaps)}</td></tr>"
        for f in report.findings
    )
    blocking = (
        "".join(f"<li>{esc(g)}</li>" for g in report.blocking_gaps) or "<li>None identified.</li>"
    )
    domain_rows = (
        "".join(
            f"<tr><td>{esc(d.domain.value.replace('_', ' '))}</td><td>{d.total_controls}</td>"
            f"<td>{d.assessed}</td><td>{d.coverage_pct:.0f}%</td><td>{d.weighted_score_pct:.0f}%</td>"
            f"<td>{d.assurance_score_pct:.0f}%</td><td>{len(d.critical_failures)}</td>"
            f"<td>{len(d.critical_unevidenced)}</td></tr>"
            for d in report.domain_coverage
        )
        or "<tr><td colspan='8'>No domain data.</td></tr>"
    )

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Muraqib - {esc(report.platform_name)}</title>
<style>
:root{{--bg:#fbfbfa;--fg:#1a1a1a;--muted:#5f6368;--line:#e3e3e0;--card:#fff}}
@media (prefers-color-scheme:dark){{:root{{--bg:#16171a;--fg:#ededeb;--muted:#a0a3a8;--line:#2c2e33;--card:#1e2024}}}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}}
.wrap{{max-width:1100px;margin:0 auto;padding:32px 20px 80px}}
h1{{font-size:26px;margin:0 0 4px}} h2{{font-size:18px;margin:36px 0 12px;padding-bottom:6px;border-bottom:1px solid var(--line)}}
.meta{{color:var(--muted);font-size:13px}}
.note{{background:var(--card);border:1px solid var(--line);border-left:3px solid #B4690E;padding:12px 14px;border-radius:6px;margin:18px 0;font-size:13.5px;color:var(--muted)}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin:18px 0}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:14px}}
.card .k{{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}}
.card .v{{font-size:28px;font-weight:600;margin:4px 0}}
.card .s{{font-size:12px;color:var(--muted)}}
.tablewrap{{overflow-x:auto;border:1px solid var(--line);border-radius:8px;background:var(--card)}}
table{{border-collapse:collapse;width:100%;font-size:13.5px;min-width:760px}}
th,td{{text-align:left;padding:9px 12px;border-bottom:1px solid var(--line);vertical-align:top}}
th{{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}}
tr:last-child td{{border-bottom:none}}
.pill{{color:#fff;border-radius:999px;padding:2px 9px;font-size:11.5px;white-space:nowrap}}
code{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px}}
</style></head><body><div class="wrap">
<h1>AI Governance Readiness Assessment</h1>
<div class="meta">{esc(report.platform_name)} · {esc(report.owner_org or "organisation not stated")} ·
run <code>{esc(report.run_id)}</code> · {report.created_at:%Y-%m-%d %H:%M} UTC · engine <code>{esc(report.model_used)}</code></div>
<div class="note">{esc(report.disclaimer)}</div>
<div class="grid">
  <div class="card"><div class="k">Coverage</div><div class="v">{report.overall_coverage_pct:.0f}%</div><div class="s">of applicable controls given a usable verdict</div></div>
  <div class="card"><div class="k">Weighted posture</div><div class="v">{report.overall_weighted_pct:.0f}%</div><div class="s">of available control weight satisfied</div></div>
  <div class="card"><div class="k">Assurance</div><div class="v">{report.overall_assurance_pct:.0f}%</div><div class="s">posture discounted by evidence strength</div></div>
  <div class="card"><div class="k">Risk tier</div><div class="v">{esc(report.risk.tier.value.upper())}</div><div class="s">deterministic triage</div></div>
  <div class="card"><div class="k">Blocking gaps</div><div class="v">{len(report.blocking_gaps)}</div><div class="s">high weight, binding instruments</div></div>
</div>
<div class="note" style="border-left-color:{"#137547" if (report.assurance_claim and report.assurance_claim.permitted) else "#B3261E"}">
<strong>Production assurance claim: {esc(report.assurance_claim.verdict) if report.assurance_claim else "UNKNOWN"}</strong><br>
{esc(report.assurance_claim.rationale) if report.assurance_claim else ""}
{("<br>" + "<br>".join("&bull; " + esc(b) for b in report.assurance_claim.blockers)) if report.assurance_claim and report.assurance_claim.blockers else ""}
</div>
<h2>Assurance domains</h2>
<div class="tablewrap"><table>
<thead><tr><th>Domain</th><th>Controls</th><th>Assessed</th><th>Coverage</th><th>Weighted</th><th>Assurance</th><th>Critical FAIL</th><th>Critical unevidenced</th></tr></thead>
<tbody>{domain_rows}</tbody></table></div>
<h2>By framework</h2><div class="grid">{cards}</div>
<h2>Blocking gaps</h2><ul>{blocking}</ul>
<h2>Findings</h2>
<div class="tablewrap"><table>
<thead><tr><th>Control</th><th>Status</th><th>Confidence</th><th>Rationale</th><th>Gaps</th></tr></thead>
<tbody>{rows}</tbody></table></div>
</div></body></html>"""
