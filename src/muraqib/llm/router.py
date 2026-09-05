"""Model routing, retry, and hard run budgets.

Two things here matter for a governance tool:

* **Budget enforcement.** A run cannot exceed a call count or a USD ceiling.
  Unbounded autonomous model invocation is a named, unsolved cost-governance
  problem in agent stacks; Muraqib bounds it explicitly and records the number.
* **Provider neutrality.** The assessment logic never knows which model it is
  talking to, so a client can run the identical assessment against a sovereign
  on-premise model and against a SaaS model and diff the results.
"""

from __future__ import annotations

import logging
import random
import time

from ..config import Settings, get_settings
from ..models import TokenUsage
from .base import ChatMessage, LLMProvider, LLMResponse, ProviderError
from .providers import (
    AnthropicProvider,
    GeminiProvider,
    OfflineProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
)

log = logging.getLogger("muraqib.llm")

_REGISTRY: dict[str, type[LLMProvider]] = {
    "offline": OfflineProvider,
    "anthropic": AnthropicProvider,
    "openai": OpenAICompatibleProvider,
    "azure_openai": OpenAICompatibleProvider,
    "groq": OpenAICompatibleProvider,
    "gemini": GeminiProvider,
    "ollama": OllamaProvider,
}


class BudgetExceeded(RuntimeError):
    """Raised when a run hits its call or cost ceiling. Never retried."""


def get_provider(settings: Settings | None = None) -> LLMProvider:
    s = settings or get_settings()
    cls = _REGISTRY.get(s.provider)
    if cls is None:
        raise ProviderError(
            f"unknown provider {s.provider!r}; available: {', '.join(sorted(_REGISTRY))}",
            retryable=False,
        )
    return cls(model=s.model, api_key=s.api_key_for(s.provider), timeout_s=s.request_timeout_s)


class ModelRouter:
    """Wraps a provider with retry, budget accounting and structured logging."""

    def __init__(self, provider: LLMProvider | None = None, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.provider = provider or get_provider(self.settings)
        self.usage = TokenUsage()
        self._sleep = time.sleep

    @property
    def model_name(self) -> str:
        return f"{self.provider.name}:{self.provider.model}"

    def _check_budget(self) -> None:
        s = self.settings
        if self.usage.calls >= s.max_llm_calls_per_run:
            raise BudgetExceeded(
                f"run exceeded max model calls ({s.max_llm_calls_per_run}); "
                "raise MURAQIB_MAX_CALLS or reduce framework scope"
            )
        if self.usage.estimated_cost_usd >= s.max_cost_usd_per_run:
            raise BudgetExceeded(
                f"run exceeded estimated cost ceiling (${s.max_cost_usd_per_run:.2f}); "
                "raise MURAQIB_MAX_COST_USD to continue"
            )

    def complete(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_only: bool = True,
    ) -> LLMResponse:
        self._check_budget()
        s = self.settings
        attempt = 0
        last: ProviderError | None = None
        while attempt < max(1, s.max_retries):
            attempt += 1
            try:
                resp = self.provider.complete(
                    messages,
                    temperature=s.temperature if temperature is None else temperature,
                    max_tokens=s.max_output_tokens if max_tokens is None else max_tokens,
                    json_only=json_only,
                )
            except ProviderError as exc:
                last = exc
                if not exc.retryable or attempt >= s.max_retries:
                    raise
                backoff = min(8.0, 0.5 * 2 ** (attempt - 1)) + random.uniform(0, 0.25)  # noqa: S311
                log.warning(
                    "llm retry",
                    extra={"attempt": attempt, "backoff_s": round(backoff, 2), "error": str(exc)},
                )
                self._sleep(backoff)
                continue
            self.usage = self.usage.add(
                TokenUsage(
                    prompt_tokens=resp.prompt_tokens,
                    completion_tokens=resp.completion_tokens,
                    calls=1,
                    estimated_cost_usd=resp.estimated_cost_usd,
                )
            )
            return resp
        raise last or ProviderError("exhausted retries", retryable=False)
