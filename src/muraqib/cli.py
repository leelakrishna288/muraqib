"""Command line interface."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer
import yaml
from rich.console import Console
from rich.table import Table

from .config import get_settings
from .corpus import Corpus
from .graph.orchestrator import build_context, get_orchestrator, new_run_id
from .models import Framework, PlatformConfig
from .observability.audit import AuditLedger
from .observability.logging_setup import configure_logging
from .reporting.render import render_html, render_markdown, render_remediation_plan

app = typer.Typer(add_completion=False, help="Muraqib - AI governance readiness assessment.")
console = Console()


def _load_config(path: Path) -> PlatformConfig:
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw) if path.suffix.lower() == ".json" else yaml.safe_load(raw)
    return PlatformConfig.model_validate(data)


def _frameworks(values: list[str] | None, corpus: Corpus) -> list[Framework]:
    if not values:
        return corpus.frameworks
    out: list[Framework] = []
    for v in values:
        try:
            out.append(Framework(v.upper()))
        except ValueError:
            console.print(f"[red]Unknown framework: {v}[/red]")
            console.print(f"Available: {', '.join(f.value for f in corpus.frameworks)}")
            raise typer.Exit(2) from None
    return out


@app.command()
def frameworks() -> None:
    """List the governance instruments Muraqib can assess against."""
    corpus = Corpus.load()
    table = Table(title="Loaded frameworks", show_lines=False)
    for col in ("Framework", "Official name", "Status", "Controls"):
        table.add_column(col, overflow="fold")
    for pack in corpus.packs:
        table.add_row(
            pack.framework.value,
            pack.official_name[:80],
            pack.obligation.value.replace("_", " "),
            str(pack.control_count),
        )
    console.print(table)
    console.print(f"\n[dim]Total controls: {len(corpus.all_controls())}[/dim]")


@app.command()
def search(
    query: str = typer.Argument(..., help="Natural-language query"),
    top_k: int = typer.Option(8, "--top-k", "-k"),
) -> None:
    """Search the control corpus (hybrid BM25 + vector retrieval)."""
    from .rag.embeddings import get_embedder
    from .rag.retriever import HybridRetriever

    s = get_settings()
    corpus = Corpus.load()
    retriever = HybridRetriever(
        corpus.all_controls(),
        get_embedder(s.embedding_backend, s.embedding_model),
        alpha=s.hybrid_alpha,
    )
    table = Table(title=f"Top {top_k} controls for: {query}")
    for col, just in (
        ("Control", "left"),
        ("Framework", "left"),
        ("Score", "right"),
        ("Title", "left"),
    ):
        table.add_column(col, justify=just, overflow="fold")  # type: ignore[arg-type]
    for chunk in retriever.retrieve(query, top_k=top_k):
        control = corpus.control(chunk.control_id)
        table.add_row(
            chunk.control_id,
            chunk.metadata.get("framework", ""),
            f"{chunk.score:.3f}",
            control.title if control else "",
        )
    console.print(table)
    console.print(f"[dim]embedder: {retriever.embedder.backend}[/dim]")


@app.command()
def assess(
    config: Path = typer.Argument(
        ..., exists=True, readable=True, help="Platform config (.yaml/.json)"
    ),
    framework: list[str] = typer.Option(
        None, "--framework", "-f", help="Repeatable. Default: all."
    ),
    out: Path = typer.Option(Path("reports"), "--out", "-o", help="Output directory"),
    fmt: list[str] = typer.Option(["md", "json"], "--format", help="md, json, html, plan"),
    provider: str = typer.Option("", "--provider", help="Override MURAQIB_PROVIDER"),
    model: str = typer.Option("", "--model", help="Override MURAQIB_MODEL"),
    no_critic: bool = typer.Option(False, "--no-critic", help="Skip adversarial review"),
    quiet: bool = typer.Option(False, "--quiet", "-q"),
) -> None:
    """Run a full assessment and write reports."""
    import os

    if provider:
        os.environ["MURAQIB_PROVIDER"] = provider
    if model:
        os.environ["MURAQIB_MODEL"] = model
    if no_critic:
        os.environ["MURAQIB_ENABLE_CRITIC"] = "0"

    s = get_settings(refresh=True)
    configure_logging(s.log_level, json_output=True)

    platform = _load_config(config)
    corpus = Corpus.load(s.corpus_dir)
    chosen = _frameworks(framework, corpus)
    run_id = new_run_id()
    ctx = build_context(s, run_id=run_id, corpus=corpus)

    if not quiet:
        console.print(f"[bold]Assessing[/bold] {platform.platform_name}")
        console.print(f"  frameworks : {', '.join(f.value for f in chosen)}")
        console.print(f"  controls   : {len(corpus.controls(chosen))}")
        console.print(f"  engine     : {ctx.router.model_name}")
        console.print(f"  run id     : {run_id}\n")

    def progress(node: str, _state) -> None:  # noqa: ANN001
        if not quiet:
            console.print(f"  [green]done[/green] {node}")

    try:
        report = get_orchestrator(ctx).run(platform, chosen, run_id=run_id, on_progress=progress)
    except Exception as exc:  # noqa: BLE001
        console.print(f"\n[red]Assessment stopped:[/red] {exc}")
        raise typer.Exit(1) from exc

    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    if "json" in fmt:
        p = out / f"{run_id}.json"
        p.write_text(report.to_json(), encoding="utf-8")
        written.append(p)
    if "md" in fmt:
        p = out / f"{run_id}.md"
        p.write_text(render_markdown(report), encoding="utf-8")
        written.append(p)
    if "html" in fmt:
        p = out / f"{run_id}.html"
        p.write_text(render_html(report), encoding="utf-8")
        written.append(p)
    if "plan" in fmt:
        p = out / f"{run_id}.remediation.md"
        p.write_text(render_remediation_plan(report, corpus), encoding="utf-8")
        written.append(p)

    ok, detail = ctx.ledger.verify()

    table = Table(title="Result", show_header=False)
    table.add_column("k")
    table.add_column("v")
    table.add_row("Risk tier", report.risk.tier.value.upper())
    table.add_row("Coverage", f"{report.overall_coverage_pct:.1f}%")
    table.add_row("Weighted posture", f"{report.overall_weighted_pct:.1f}%")
    table.add_row("Assurance score", f"{report.overall_assurance_pct:.1f}%")
    if report.assurance_claim is not None:
        colour = "green" if report.assurance_claim.permitted else "red"
        table.add_row(
            "Production assurance",
            f"[{colour}]{report.assurance_claim.verdict}[/]"
            + (
                f" ({len(report.assurance_claim.blockers)} blockers)"
                if report.assurance_claim.blockers
                else ""
            ),
        )
    table.add_row("Blocking gaps", str(len(report.blocking_gaps)))
    table.add_row("Model calls", str(report.usage.calls))
    table.add_row("Estimated cost", f"${report.usage.estimated_cost_usd:.4f}")
    table.add_row("Audit ledger", f"{'verified' if ok else 'FAILED'} - {detail}")
    console.print(table)
    for p in written:
        console.print(f"  [cyan]{p}[/cyan]")

    if not ok:
        raise typer.Exit(3)


@app.command()
def govern(
    transaction: Path = typer.Argument(
        ..., exists=True, readable=True, help="Transaction YAML/JSON"
    ),
    policy: Path = typer.Option(
        Path("examples/governance_policy.yaml"), "--policy", "-p", exists=True
    ),
    ledger_out: Path = typer.Option(None, "--ledger", help="Write the decision ledger here"),
) -> None:
    """Evaluate one transaction against the runtime governance policy."""
    from .observability.audit import AuditLedger as _Ledger
    from .runtime import GovernanceEngine, TransactionContext, load_policy

    raw = transaction.read_text(encoding="utf-8")
    data = json.loads(raw) if transaction.suffix.lower() == ".json" else yaml.safe_load(raw)
    ctx = TransactionContext.model_validate(data)
    pol = load_policy(policy)
    ledger = _Ledger(run_id=ctx.transaction_id, path=ledger_out)
    decision = GovernanceEngine(pol, ledger=ledger).evaluate(ctx)

    colour = {
        "allow": "green",
        "warn": "yellow",
        "mask": "yellow",
        "block": "red",
        "not_evidenced": "magenta",
        "not_run": "dim",
    }
    console.print(
        f"\n[bold]{ctx.transaction_id}[/bold]  policy [dim]{pol.name} v{pol.version}[/dim]"
    )
    console.print(
        f"verdict: [{colour[decision.verdict.value]}]{decision.verdict.value.upper()}[/]"
        + (f"   stopped at: [red]{decision.blocked_at.value}[/]" if decision.blocked_at else "")
    )

    table = Table(show_header=True)
    for col in ("Gate", "Verdict", "Reason", "ms"):
        table.add_column(col, overflow="fold")
    for g in decision.gates:
        table.add_row(
            g.gate.value,
            f"[{colour[g.verdict.value]}]{g.verdict.value}[/]",
            g.reasons[0] if g.reasons else "",
            f"{g.duration_ms:.2f}",
        )
    console.print(table)

    if decision.released_response:
        console.print(
            f"\nreleased [dim]({decision.response_id})[/dim]: {decision.released_response}"
        )
        if decision.provenance:
            console.print(f"provenance: {', '.join(decision.provenance)}")
    else:
        console.print("\n[red]nothing released[/red]")

    ok, detail = ledger.verify()
    console.print(f"\naudit ledger: {'verified' if ok else 'FAILED'} - {detail}")
    console.print(f"trace: {' -> '.join(decision.trace())}")
    raise typer.Exit(0 if decision.allowed else 1)


@app.command("verify-ledger")
def verify_ledger(
    path: Path = typer.Argument(..., exists=True, help="A .ledger.jsonl file"),
) -> None:
    """Verify the hash chain of an audit ledger."""
    ledger = AuditLedger.load(path)
    ok, detail = ledger.verify()
    console.print(f"run_id: {ledger.run_id}")
    console.print(f"events: {json.dumps(ledger.summary())}")
    console.print(f"[{'green' if ok else 'red'}]{'VERIFIED' if ok else 'TAMPERED'}[/] - {detail}")
    raise typer.Exit(0 if ok else 1)


@app.command()
def evaluate(
    golden: Path = typer.Option(None, "--golden", help="Golden set JSON (defaults to bundled)"),
    provider: str = typer.Option("", "--provider"),
    model: str = typer.Option("", "--model"),
) -> None:
    """Run the evaluation harness against the golden set."""
    import os

    from .evals.harness import run_evaluation

    if provider:
        os.environ["MURAQIB_PROVIDER"] = provider
    if model:
        os.environ["MURAQIB_MODEL"] = model
    result = run_evaluation(golden)

    table = Table(title=f"Evaluation - {result.engine}")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_row("Cases", str(result.total))
    table.add_row("Status accuracy", f"{result.status_accuracy:.1%}")
    table.add_row("Retrieval recall@k", f"{result.retrieval_recall:.1%}")
    table.add_row("Citation validity", f"{result.citation_validity:.1%}")
    table.add_row("Over-claim rate", f"{result.over_claim_rate:.1%}")
    table.add_row("Abstention correctness", f"{result.abstention_correctness:.1%}")
    console.print(table)
    if result.failures:
        console.print("\n[yellow]Failures[/yellow]")
        for f in result.failures[:20]:
            console.print(f"  {f}")
    raise typer.Exit(0 if result.passed else 1)


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port"),
    reload: bool = typer.Option(False, "--reload"),
) -> None:
    """Run the REST API."""
    import uvicorn

    uvicorn.run("muraqib.api.app:app", host=host, port=port, reload=reload)


def main() -> None:  # pragma: no cover
    try:
        app()
    except KeyboardInterrupt:
        console.print("\n[yellow]interrupted[/yellow]")
        sys.exit(130)


if __name__ == "__main__":  # pragma: no cover
    main()
