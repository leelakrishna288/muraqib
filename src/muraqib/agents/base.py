"""Agent contract.

An "agent" here is a node with a single responsibility that reads and writes
the shared RunState. Only the AssessorAgent and CriticAgent talk to a model;
intake, retrieval, risk and reporting are deterministic on purpose. Putting a
model where a rule will do is how agent systems become unauditable and
expensive.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass

from ..config import Settings
from ..corpus import Corpus
from ..graph.state import RunState
from ..guardrails import CitationGate, InjectionScanner, PIIRedactor
from ..llm.router import ModelRouter
from ..observability.audit import AuditLedger
from ..observability.tracing import Tracer
from ..rag.retriever import HybridRetriever


@dataclass
class AgentContext:
    settings: Settings
    corpus: Corpus
    router: ModelRouter
    retriever: HybridRetriever
    ledger: AuditLedger
    tracer: Tracer
    redactor: PIIRedactor
    scanner: InjectionScanner
    citation_gate: CitationGate


class Agent(abc.ABC):
    name: str = "agent"

    def __init__(self, ctx: AgentContext):
        self.ctx = ctx

    @abc.abstractmethod
    def run(self, state: RunState) -> RunState: ...

    def _audit(self, event: str, **payload: object) -> None:
        self.ctx.ledger.record(event, actor=self.name, **payload)
