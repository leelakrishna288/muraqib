"""Graph package.

``Orchestrator`` is exposed lazily: it imports every agent, and the agents in
turn import ``graph.state``. Eagerly importing it here would create a cycle.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .state import RunState

__all__ = ["RunState", "Orchestrator", "build_context", "new_run_id"]

if TYPE_CHECKING:  # pragma: no cover
    from .orchestrator import Orchestrator, build_context, new_run_id


def __getattr__(name: str) -> Any:
    if name in {"Orchestrator", "build_context", "new_run_id", "PIPELINE"}:
        from . import orchestrator  # noqa: PLC0415

        return getattr(orchestrator, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
