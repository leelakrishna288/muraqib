"""Retrieval agent: pick the evidence each control is judged against.

For each in-scope control we retrieve the control itself plus its nearest
neighbours. The neighbours matter: an assessor that sees only the single
control it is judging cannot tell you that your gap in NDMO.CL.03 is the same
gap as GDPR.ART15.01. Cross-framework overlap is where the value is - clients
want one remediation list, not six.
"""

from __future__ import annotations

from ..graph.state import RunState
from ..models import Control
from .base import Agent


class RetrievalAgent(Agent):
    name = "retrieval"

    def run(self, state: RunState) -> RunState:
        ctx = self.ctx
        with ctx.tracer.span("agent.retrieval", run_id=state.run_id):
            controls = ctx.corpus.controls(state.frameworks)
            top_k = ctx.settings.retrieval_top_k
            for control in controls:
                chunks = ctx.retriever.retrieve(self._query(control), top_k=top_k)
                ids = [c.control_id for c in chunks]
                if control.id not in ids:
                    ids.insert(0, control.id)
                state.retrieved[control.id] = ids[:top_k]
            state.stage = "retrieval_complete"
            self._audit(
                "retrieval_complete",
                controls=len(controls),
                top_k=top_k,
                embedder=ctx.retriever.embedder.backend,
                store=ctx.retriever.store.backend,
            )
        return state

    @staticmethod
    def _query(control: Control) -> str:
        return f"{control.title}. {control.question} {control.intent}".strip()

    @staticmethod
    def neighbours(state: RunState, control_id: str) -> list[str]:
        return [c for c in state.retrieved.get(control_id, []) if c != control_id]
