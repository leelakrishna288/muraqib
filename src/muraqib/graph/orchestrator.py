"""The orchestrator: an explicit, inspectable state graph.

Nodes run in a declared order with declared preconditions. There is no
"let the model decide what to do next" loop, and that is a deliberate design
decision, not a limitation:

* a compliance assessment must be **reproducible** - the same input must produce
  the same sequence of steps, or the audit trail is worthless;
* it must be **bounded** - an unbounded agent loop over 121 controls is an
  unbounded bill;
* it must be **resumable** - state is checkpointed after every node.

The model is used where judgement is genuinely needed (assess, critique) and
nowhere else. This mirrors the graph-with-explicit-state pattern (LangGraph and
similar) rather than the autonomous-planner pattern.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..agents.assessor import AssessorAgent
from ..agents.base import AgentContext
from ..agents.critic import CriticAgent
from ..agents.intake import IntakeAgent
from ..agents.reporter import ReporterAgent
from ..agents.retrieval import RetrievalAgent
from ..agents.risk import RiskAgent
from ..config import Settings, get_settings
from ..corpus import Corpus
from ..guardrails import CitationGate, InjectionScanner, PIIRedactor
from ..llm.router import ModelRouter
from ..models import AssessmentReport, Framework, PlatformConfig
from ..observability.audit import AuditLedger
from ..observability.tracing import Tracer
from ..rag.embeddings import get_embedder
from ..rag.retriever import HybridRetriever
from ..rag.store import get_vector_store
from .state import RunState

log = logging.getLogger("muraqib.graph")


@dataclass(frozen=True)
class Node:
    name: str
    agent_cls: type
    requires_stage: tuple[str, ...]


PIPELINE: tuple[Node, ...] = (
    Node("intake", IntakeAgent, ("created",)),
    Node("risk", RiskAgent, ("intake_complete",)),
    Node("retrieval", RetrievalAgent, ("risk_complete",)),
    Node("assessor", AssessorAgent, ("retrieval_complete",)),
    Node("critic", CriticAgent, ("assessment_complete",)),
    Node("reporter", ReporterAgent, ("assessment_complete", "critique_complete")),
)


def new_run_id() -> str:
    return f"MRQ-{datetime.now(timezone.utc):%Y%m%d}-{uuid.uuid4().hex[:8]}"


def build_context(
    settings: Settings | None = None,
    *,
    run_id: str = "",
    corpus: Corpus | None = None,
    router: ModelRouter | None = None,
) -> AgentContext:
    s = settings or get_settings()
    corpus = corpus or Corpus.load(s.corpus_dir)
    embedder = get_embedder(s.embedding_backend, s.embedding_model)
    store = get_vector_store(s.vector_backend, path=str(s.index_dir), dimension=embedder.dimension)
    retriever = HybridRetriever(corpus.all_controls(), embedder, store, alpha=s.hybrid_alpha)
    ledger_path = (s.runs_dir / f"{run_id}.ledger.jsonl") if run_id else None
    return AgentContext(
        settings=s,
        corpus=corpus,
        router=router or ModelRouter(settings=s),
        retriever=retriever,
        ledger=AuditLedger(run_id or "unbound", ledger_path),
        tracer=Tracer(enabled_otel=s.otel_enabled),
        redactor=PIIRedactor(enabled=s.redact_pii),
        scanner=InjectionScanner(),
        citation_gate=CitationGate(
            {c.id for c in corpus.all_controls()}, require=s.require_citations
        ),
    )


class Orchestrator:
    def __init__(self, ctx: AgentContext, *, checkpoint: bool = True):
        self.ctx = ctx
        self.checkpoint = checkpoint

    # ------------------------------------------------------------------
    def run(
        self,
        config: PlatformConfig,
        frameworks: list[Framework] | None = None,
        *,
        run_id: str = "",
        on_progress: Callable[[str, RunState], None] | None = None,
    ) -> AssessmentReport:
        state = RunState(
            run_id=run_id or self.ctx.ledger.run_id or new_run_id(),
            config=config,
            frameworks=frameworks or self.ctx.corpus.frameworks,
        )
        return self.resume(state, on_progress=on_progress)

    def resume(
        self, state: RunState, *, on_progress: Callable[[str, RunState], None] | None = None
    ) -> AssessmentReport:
        ctx = self.ctx
        ctx.ledger.record(
            "run_started",
            actor="orchestrator",
            run_id=state.run_id,
            platform=state.config.platform_name,
            frameworks=[f.value for f in state.frameworks],
            settings=ctx.settings.redacted(),
            resumed_from=state.stage,
        )
        with ctx.tracer.span("run", run_id=state.run_id):
            for node in PIPELINE:
                if state.stage not in node.requires_stage:
                    log.debug("skipping node", extra={"node": node.name, "stage": state.stage})
                    continue
                log.info("node start", extra={"node": node.name, "run_id": state.run_id})
                agent = node.agent_cls(ctx)
                state = agent.run(state)
                if self.checkpoint:
                    state.checkpoint(ctx.settings.runs_dir)
                if on_progress:
                    on_progress(node.name, state)

        ok, detail = ctx.ledger.verify()
        ctx.ledger.record("run_finished", actor="orchestrator", ledger_verified=ok, detail=detail)

        if state.report is None:  # pragma: no cover - defensive
            raise RuntimeError(f"pipeline finished without a report (stage={state.stage})")
        return state.report

    @staticmethod
    def resume_from_file(path: Path, ctx: AgentContext) -> RunState:
        return RunState.load(path)
