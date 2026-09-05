"""Concrete providers.

The default provider is ``offline`` - a deterministic, rule-based assessor that
needs no API key, no network and no money. It exists for three reasons:

1. the entire test suite and CI run with zero cost and zero secrets;
2. it is the *baseline* the eval harness scores every LLM against, so we can
   prove an LLM actually adds accuracy rather than assuming it;
3. air-gapped and sovereign-cloud deployments can run Muraqib with no external
   model at all, which is a real requirement in KSA/UAE public sector work.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx

from .base import ChatMessage, LLMProvider, LLMResponse, ProviderError, approx_tokens

# Indicative prices (USD per 1M tokens). Used for run-budget accounting only,
# clearly an ESTIMATE - providers change prices and we do not scrape them.
PRICE_TABLE: dict[str, tuple[float, float]] = {
    "claude-sonnet-4-5": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4o-mini": (0.15, 0.60),
    "gemini-2.0-flash": (0.10, 0.40),
    "llama-3.3-70b-versatile": (0.59, 0.79),
}


def _price(model: str, prompt: int, completion: int) -> float:
    for key, (pin, pout) in PRICE_TABLE.items():
        if key in model:
            return round(prompt / 1e6 * pin + completion / 1e6 * pout, 6)
    return 0.0


def _extract_json(text: str) -> str:
    """Pull the first JSON object out of a model response.

    Models wrap JSON in prose and fences no matter how firmly you ask them not
    to, so we do not trust the format - we extract and then validate.
    """
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    start = text.find("{")
    if start == -1:
        return text.strip()
    depth, in_str, esc = 0, False, False
    for i, ch in enumerate(text[start:], start):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return text[start:].strip()


# --------------------------------------------------------------------------
# Offline deterministic provider
# --------------------------------------------------------------------------

# Word-boundary matched. Substring matching was a real bug caught by the eval
# harness: "implemented" matched inside "Not implemented", flipping a
# non-compliant control to partial. Boundaries and explicit negation phrases fix it.
_POSITIVE = (
    "documented",
    "implemented",
    "enforced",
    "approved",
    "signed",
    "tested",
    "automated",
    "reviewed",
    "certified",
    "encrypted",
    "monitored",
    "logged",
    "retained",
    "assigned",
    "published",
    "maintained",
    "listed",
    "executed",
    "operating",
    "operational",
    "in place",
    "signed off",
    "carried out",
    "completed",
    "verified",
    "configured",
    "enabled",
)

# Explicit negation phrases. Checked BEFORE positives, and a positive term that
# falls inside a negation window is discounted.
_NEGATIVE = (
    "not implemented",
    "not documented",
    "not tested",
    "not reviewed",
    "not enforced",
    "not encrypted",
    "not retained",
    "not applied",
    "not re-indexed",
    "not reindexed",
    "not carried out",
    "not completed",
    "not in place",
    "no evidence",
    "no redaction",
    "none",
    "missing",
    "absent",
    "planned",
    "to be done",
    "todo",
    "tbd",
    "manual only",
    "ad hoc",
    "ad-hoc",
    "unknown",
    "not yet",
    "does not",
    "do not",
    "database only",
    "primary database only",
)

_NEG_WINDOW = 40  # characters before a positive term that are scanned for a negator
_NEGATORS = re.compile(r"\b(?:no|not|never|without|lacks?|lacking|fails?|failed)\b", re.I)


def _word_hits(text: str, terms: tuple[str, ...]) -> list[tuple[str, int]]:
    """Return (term, offset) for each term present at a word boundary."""
    hits: list[tuple[str, int]] = []
    for term in terms:
        for m in re.finditer(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text, re.I):
            hits.append((term, m.start()))
    return hits


def _negated(text: str, offset: int) -> bool:
    """True if a negator appears shortly before this position, in the same clause."""
    start = max(0, offset - _NEG_WINDOW)
    window = text[start:offset]
    window = window.rsplit(";", 1)[-1].rsplit(".", 1)[-1]
    return bool(_NEGATORS.search(window))


class OfflineProvider(LLMProvider):
    """Deterministic rule-based assessor. No network, no key, no cost.

    Given the retrieved control and the platform facts, it applies explicit
    heuristics and emits the same JSON schema an LLM is asked for. Same input,
    same output, every time - which is exactly what you want as a regression
    baseline for an evaluation harness.
    """

    name = "offline"

    @property
    def requires_network(self) -> bool:
        return False

    def complete(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int = 1024,
        json_only: bool = False,
    ) -> LLMResponse:
        prompt_tokens = approx_tokens("\n".join(m.content for m in messages))
        # Parse ONLY the final user turn. The system prompt legitimately mentions
        # the data-block tag names, so scanning the whole conversation would match
        # the instructions instead of the data.
        user_turns = [m.content for m in messages if m.role == "user"]
        payload = user_turns[-1] if user_turns else (messages[-1].content if messages else "")
        body = self._assess(payload)
        text = json.dumps(body, ensure_ascii=False)
        return LLMResponse(
            text=text,
            model="offline-deterministic",
            prompt_tokens=prompt_tokens,
            completion_tokens=approx_tokens(text),
            estimated_cost_usd=0.0,
            raw={"deterministic": True},
        )

    # -- heuristics ------------------------------------------------------
    @staticmethod
    def _facts_block(payload: str) -> str:
        m = re.search(r"<platform_facts>(.*?)</platform_facts>", payload, re.DOTALL)
        return (m.group(1) if m else payload).lower()

    @staticmethod
    def _control_id(payload: str) -> str:
        m = re.search(r"<control_id>(.*?)</control_id>", payload, re.DOTALL)
        return m.group(1).strip() if m else ""

    @staticmethod
    def _declared_evidence(payload: str) -> str:
        m = re.search(r"<client_evidence>(.*?)</client_evidence>", payload, re.DOTALL)
        return (m.group(1) if m else "").strip()

    def _assess(self, payload: str) -> dict[str, Any]:
        if "<critic_task>" in payload:
            return {
                "verdict": "upheld",
                "note": "Deterministic critic: schema and citation present.",
            }

        control_id = self._control_id(payload)
        evidence = self._declared_evidence(payload)
        # The prompt substitutes an explicit placeholder when nothing was supplied.
        if evidence.startswith("(none supplied"):
            evidence = ""
        facts = self._facts_block(payload)
        ev_low = evidence.lower()

        if not evidence:
            return {
                "status": "not_assessable",
                "confidence": "high",
                "rationale": (
                    "No client-declared evidence was supplied for this control and the "
                    "platform configuration does not contain a fact that settles it."
                ),
                "evidence": [],
                "gaps": ["No evidence supplied for this control."],
                "recommendation": (
                    "Collect and attach the evidence artefacts listed for this control, "
                    "then re-run the assessment."
                ),
                "citations": [
                    {
                        "control_id": control_id,
                        "source": "corpus",
                        "quote": "",
                        "locator": control_id,
                    }
                ],
            }

        neg_hits = _word_hits(ev_low, _NEGATIVE)
        pos_hits = [(t, o) for t, o in _word_hits(ev_low, _POSITIVE) if not _negated(ev_low, o)]
        neg, pos = len(neg_hits), len(pos_hits)

        # Weight of evidence, not mere presence. A control whose evidence carries
        # more negation than implementation is not "partially" satisfied - the
        # obligation is not being met. This matters most for rights-fulfilment
        # controls, where partial fulfilment means the right was not delivered.
        if neg > pos:
            status, conf = "non_compliant", "medium"
            gaps = [
                "Declared evidence carries more non-implementation indicators than "
                "implementation indicators."
            ]
            rec = "Implement the missing element and re-evidence the control end to end."
        elif neg and pos >= 1:
            status, conf = "partial", "medium"
            gaps = ["Evidence contains both implementation and non-implementation indicators."]
            rec = "Close the outstanding element and re-evidence the whole control."
        elif pos >= 2:
            status, conf = "compliant", "medium"
            gaps = []
            rec = "Maintain the control and re-verify at the next review cycle."
        elif pos == 1:
            status, conf = "partial", "low"
            gaps = ["Only one implementation indicator found; evidence is thin."]
            rec = "Supply a second, independent artefact demonstrating the control operating."
        else:
            status, conf = "not_assessable", "high"
            gaps = ["Supplied evidence does not clearly establish implementation."]
            rec = "Supply an artefact that demonstrates the control operating."

        # Cross-check a few high-signal platform facts.
        if (
            status == "compliant"
            and "cross_border_transfer: true" in facts
            and control_id.endswith(("XB.01", "PD.05", "ART44.01"))
        ):
            status, conf = "partial", "medium"
            gaps.append("Cross-border transfer is declared; confirm the safeguard mechanism.")

        return {
            "status": status,
            "confidence": conf,
            "rationale": (
                f"Deterministic assessment of {control_id}: matched {pos} un-negated "
                f"implementation indicator(s) and {neg} negation phrase(s) in the declared evidence."
            ),
            "evidence": [evidence[:400]],
            "gaps": gaps,
            "recommendation": rec,
            "citations": [
                {"control_id": control_id, "source": "corpus", "quote": "", "locator": control_id},
                {
                    "control_id": control_id,
                    "source": "client_evidence",
                    "quote": evidence[:200],
                    "locator": "declared",
                },
            ],
        }


# --------------------------------------------------------------------------
# HTTP providers
# --------------------------------------------------------------------------


class _HttpProvider(LLMProvider):
    def _post(self, url: str, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
        try:
            resp = httpx.post(url, headers=headers, json=body, timeout=self.timeout_s)
        except httpx.TimeoutException as exc:
            raise ProviderError(f"{self.name}: request timed out", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.name}: transport error: {exc}", retryable=True) from exc
        if resp.status_code >= 400:
            retryable = resp.status_code in (408, 409, 425, 429, 500, 502, 503, 504)
            raise ProviderError(
                f"{self.name}: HTTP {resp.status_code}: {resp.text[:300]}",
                retryable=retryable,
                status=resp.status_code,
            )
        return resp.json()


class AnthropicProvider(_HttpProvider):
    name = "anthropic"
    ENDPOINT = "https://api.anthropic.com/v1/messages"

    def complete(
        self, messages, *, temperature=0.0, max_tokens=1024, json_only=False
    ) -> LLMResponse:
        if not self._api_key:
            raise ProviderError("anthropic: ANTHROPIC_API_KEY is not set", retryable=False)
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        turns = [{"role": m.role, "content": m.content} for m in messages if m.role != "system"]
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": turns or [{"role": "user", "content": system}],
        }
        if system and turns:
            body["system"] = system
        data = self._post(
            self.ENDPOINT,
            {
                "x-api-key": self._api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            body,
        )
        text = "".join(
            b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"
        )
        usage = data.get("usage", {})
        pt, ct = usage.get("input_tokens", 0), usage.get("output_tokens", 0)
        return LLMResponse(
            text=_extract_json(text) if json_only else text,
            model=data.get("model", self.model),
            prompt_tokens=pt,
            completion_tokens=ct,
            estimated_cost_usd=_price(self.model, pt, ct),
        )


class OpenAICompatibleProvider(_HttpProvider):
    """Works with OpenAI, Groq, Together, vLLM, Ollama and Azure OpenAI - all of
    which speak the /chat/completions shape."""

    name = "openai"
    DEFAULT_BASE = "https://api.openai.com/v1"

    def __init__(self, model: str, api_key: str = "", timeout_s: int = 60, base_url: str = ""):
        super().__init__(model, api_key, timeout_s)
        self.base_url = (base_url or os.getenv("MURAQIB_BASE_URL") or self.DEFAULT_BASE).rstrip("/")

    def complete(
        self, messages, *, temperature=0.0, max_tokens=1024, json_only=False
    ) -> LLMResponse:
        body: dict[str, Any] = {
            "model": self.model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        if json_only:
            body["response_format"] = {"type": "json_object"}
        headers = {"content-type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        data = self._post(f"{self.base_url}/chat/completions", headers, body)
        choice = (data.get("choices") or [{}])[0]
        text = (choice.get("message") or {}).get("content", "") or ""
        usage = data.get("usage", {})
        pt, ct = usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
        return LLMResponse(
            text=_extract_json(text) if json_only else text,
            model=data.get("model", self.model),
            prompt_tokens=pt,
            completion_tokens=ct,
            estimated_cost_usd=_price(self.model, pt, ct),
        )


class OllamaProvider(OpenAICompatibleProvider):
    """Local models. Zero cost, no data leaves the machine - the deployment
    shape that KSA/UAE sovereign-hosting requirements actually ask for."""

    name = "ollama"
    DEFAULT_BASE = "http://localhost:11434/v1"

    @property
    def requires_network(self) -> bool:
        return False


class GeminiProvider(_HttpProvider):
    name = "gemini"
    BASE = "https://generativelanguage.googleapis.com/v1beta/models"

    def complete(
        self, messages, *, temperature=0.0, max_tokens=1024, json_only=False
    ) -> LLMResponse:
        if not self._api_key:
            raise ProviderError("gemini: GEMINI_API_KEY is not set", retryable=False)
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        contents = [
            {"role": "user" if m.role != "assistant" else "model", "parts": [{"text": m.content}]}
            for m in messages
            if m.role != "system"
        ] or [{"role": "user", "parts": [{"text": system}]}]
        body: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
        }
        if system and len(contents) > 0:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if json_only:
            body["generationConfig"]["responseMimeType"] = "application/json"
        data = self._post(
            f"{self.BASE}/{self.model}:generateContent?key={self._api_key}",
            {"content-type": "application/json"},
            body,
        )
        cands = data.get("candidates") or [{}]
        parts = (cands[0].get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts)
        um = data.get("usageMetadata", {})
        pt, ct = um.get("promptTokenCount", 0), um.get("candidatesTokenCount", 0)
        return LLMResponse(
            text=_extract_json(text) if json_only else text,
            model=self.model,
            prompt_tokens=pt,
            completion_tokens=ct,
            estimated_cost_usd=_price(self.model, pt, ct),
        )
