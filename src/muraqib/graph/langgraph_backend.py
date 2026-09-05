"""A LangGraph execution backend for the same six-node pipeline.

Muraqib's default orchestrator is a hand-rolled loop over an explicit `PIPELINE`
tuple. That is deliberate - it has no dependencies, it is trivially auditable,
and CI stays hermetic. This module runs the *same agents over the same
`RunState`* on `langgraph.graph.StateGraph` instead, selected with
`MURAQIB_ORCHESTRATOR=langgraph`.

Why keep both rather than migrating:

* **The contract is the graph, not the library.** Six nodes, declared order,
  declared preconditions, checkpoint after every node. If that contract is real
  it should hold under either executor, and `tests/test_langgraph_backend.py`
  asserts the two produce byte-identical findings on the same input. An
  equivalence test between two implementations catches things one
  implementation plus its own tests never will.
* **CI must not need the dependency.** The default backend has zero extra
  imports, so the hermetic offline build is unchanged. LangGraph is an extra
  (`pip install -e ".[graph]"`) and its job in CI installs it explicitly.
* **A governance tool must not switch execution engine silently.** If
  `MURAQIB_ORCHESTRATOR=langgraph` is set and LangGraph is not installed, this
  raises. It does not quietly fall back, because the audit ledger would then
  record a run that did not happen the way the operator asked for it. That is
  the opposite of the retrieval backend, which *does* fall back - retrieval
  degrades to a lexical embedder and records which one it used, because a
  degraded search still answers the question. A different executor is not a
  degraded executor; it is a different thing, and asking for it is an explicit
  instruction.

What LangGraph adds here is real but narrow: a declarative edge topology, a
checkpointer interface, and a compiled graph that can be drawn. What it does
not add is autonomy - there is still no "let the model decide what to do next"
loop, for the reasons in `orchestrator.py`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Annotated, Any, TypedDict

from ..agents.base import AgentContext
from ..models import AssessmentReport, Framework, PlatformConfig
from .orchestrator import PIPELINE, new_run_id
from .state import RunState

log = logging.getLogger("muraqib.graph.langgraph")

__all__ = ["LangGraphOrchestrator", "langgraph_available", "LangGraphUnavailable"]


class LangGraphUnavailable(RuntimeError):
    """Raised when the LangGraph backend is requested but not installed."""


def langgraph_available() -> bool:
    try:  # pragma: no cover - trivial import probe
        import langgraph.graph  # noqa: F401
    except ImportError:
        return False
    return True


def _last(_current: Any, incoming: Any) -> Any:
    """Reducer: the newest value wins.

    Nodes run strictly in sequence and each returns the whole mutated state, so
    there is nothing to merge. Declaring the reducer explicitly rather than
    relying on LangGraph's default keeps the intent visible: this graph has no
    concurrent branches, and if one is ever added this line is where the merge
    semantics have to be decided.
    """
    return incoming


class GraphState(TypedDict):
    """The LangGraph channel schema.

    `RunState` is carried whole rather than being flattened into channels. It is
    already the single serialisable object every agent reads and writes, and
    splitting it would create two sources of truth for the same run.
    """

    run: Annotated[RunState, _last]


class LangGraphOrchestrator:
    """Runs the Muraqib pipeline on a compiled LangGraph StateGraph.

    Signature-compatible with `Orchestrator`, so callers do not know or care
    which executor they were given.
    """

    def __init__(self, ctx: AgentContext, *, checkpoint: bool = True):
        if not langgraph_available():
            raise LangGraphUnavailable(
                "MURAQIB_ORCHESTRATOR=langgraph was requested but langgraph is not "
                'installed. Install it with: pip install -e ".[graph]" - or unset the '
                "variable to use the built-in orchestrator. This does not fall back "
                "silently: the audit ledger must record the executor that actually ran."
            )
        self.ctx = ctx
        self.checkpoint = checkpoint
        self._on_progress: Callable[[str, RunState], None] | None = None
        self._graph = self._build()

    # ------------------------------------------------------------------
    def _build(self) -> Any:
        from langgraph.graph import END, START, StateGraph

        builder = StateGraph(GraphState)

        for node in PIPELINE:
            # LangGraph's `add_node` overload set does not admit a plain
            # `Callable[[GraphState], GraphState]`, though that is exactly what it
            # accepts at run time. The ignore is scoped to this line rather than
            # loosening the node signature to `Any`, which would remove the only
            # place the channel schema is actually checked.
            builder.add_node(node.name, self._make_node(node))  # type: ignore[call-overload]

        # A linear topology, declared as edges rather than implied by list order.
        # The precondition check lives inside each node (see `_make_node`) so a
        # resumed run still skips the stages it has already passed - the edge
        # topology is fixed, the work is not.
        names = [n.name for n in PIPELINE]
        builder.add_edge(START, names[0])
        for earlier, later in zip(names[:-1], names[1:], strict=True):
            builder.add_edge(earlier, later)
        builder.add_edge(names[-1], END)

        return builder.compile()

    def _make_node(self, node: Any) -> Callable[[GraphState], GraphState]:
        def _run(state: GraphState) -> GraphState:
            run = state["run"]
            if run.stage not in node.requires_stage:
                log.debug("skipping node", extra={"node": node.name, "stage": run.stage})
                return GraphState(run=run)
            log.info("node start", extra={"node": node.name, "run_id": run.run_id})
            run = node.agent_cls(self.ctx).run(run)
            if self.checkpoint:
                run.checkpoint(self.ctx.settings.runs_dir)
            if self._on_progress:
                self._on_progress(node.name, run)
            return GraphState(run=run)

        _run.__name__ = f"node_{node.name}"
        return _run

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
        self._on_progress = on_progress
        ctx.ledger.record(
            "run_started",
            actor="orchestrator",
            run_id=state.run_id,
            platform=state.config.platform_name,
            frameworks=[f.value for f in state.frameworks],
            settings=ctx.settings.redacted(),
            resumed_from=state.stage,
            executor="langgraph",
        )
        with ctx.tracer.span("run", run_id=state.run_id, executor="langgraph"):
            final: GraphState = self._graph.invoke(GraphState(run=state))
        state = final["run"]

        ok, detail = ctx.ledger.verify()
        ctx.ledger.record("run_finished", actor="orchestrator", ledger_verified=ok, detail=detail)

        if state.report is None:  # pragma: no cover - defensive
            raise RuntimeError(f"pipeline finished without a report (stage={state.stage})")
        return state.report

    # ------------------------------------------------------------------
    def draw_mermaid(self) -> str:
        """The compiled topology as Mermaid, straight from LangGraph.

        Documentation that cannot drift, because it is generated from the graph
        that actually executes.
        """
        return str(self._graph.get_graph().draw_mermaid())
