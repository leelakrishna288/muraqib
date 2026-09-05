"""Agent package.

Same lazy pattern as ``muraqib.graph``: agents import ``graph.state`` and the
orchestrator imports agents, so eager re-export here would cycle.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .base import Agent, AgentContext

__all__ = [
    "Agent",
    "AgentContext",
    "IntakeAgent",
    "RetrievalAgent",
    "AssessorAgent",
    "CriticAgent",
    "RiskAgent",
    "ReporterAgent",
]

_LAZY = {
    "IntakeAgent": "intake",
    "RetrievalAgent": "retrieval",
    "AssessorAgent": "assessor",
    "CriticAgent": "critic",
    "RiskAgent": "risk",
    "ReporterAgent": "reporter",
}

if TYPE_CHECKING:  # pragma: no cover
    from .assessor import AssessorAgent
    from .critic import CriticAgent
    from .intake import IntakeAgent
    from .reporter import ReporterAgent
    from .retrieval import RetrievalAgent
    from .risk import RiskAgent


def __getattr__(name: str) -> Any:
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib  # noqa: PLC0415

    return getattr(importlib.import_module(f".{module}", __name__), name)
