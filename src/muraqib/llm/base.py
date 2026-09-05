"""Provider-agnostic LLM interface.

Deliberately narrow: one method, ``complete``. Muraqib never streams and never
lets a model call a tool directly - the orchestrator decides what happens next.
That is what makes runs reproducible and auditable, which is the whole point of
a governance tool.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any


class ProviderError(RuntimeError):
    """Raised for provider-side failures. Carries whether a retry is safe."""

    def __init__(self, message: str, *, retryable: bool = False, status: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status = status


@dataclass(slots=True)
class ChatMessage:
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass(slots=True)
class LLMResponse:
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated_cost_usd: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)


class LLMProvider(abc.ABC):
    """Every provider implements exactly this."""

    name: str = "base"

    def __init__(self, model: str, api_key: str = "", timeout_s: int = 60):
        self.model = model
        self._api_key = api_key
        self.timeout_s = timeout_s

    @abc.abstractmethod
    def complete(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int = 1024,
        json_only: bool = False,
    ) -> LLMResponse: ...

    @property
    def requires_network(self) -> bool:
        return True

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<{type(self).__name__} model={self.model!r}>"


def approx_tokens(text: str) -> int:
    """Cheap token estimate (~4 chars/token). Used only for budget accounting
    when a provider does not report usage."""
    return max(1, len(text) // 4)
