import pytest

from muraqib.llm.base import ChatMessage, LLMProvider, LLMResponse, ProviderError
from muraqib.llm.providers import OfflineProvider, _extract_json
from muraqib.llm.router import BudgetExceeded, ModelRouter, get_provider


class FlakyProvider(LLMProvider):
    name = "flaky"

    def __init__(self, fail_times, retryable=True):
        super().__init__("flaky-model")
        self.calls = 0
        self.fail_times = fail_times
        self.retryable = retryable

    def complete(self, messages, *, temperature=0.0, max_tokens=1024, json_only=False):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise ProviderError("boom", retryable=self.retryable, status=503)
        return LLMResponse(text="{}", model="flaky-model", prompt_tokens=10, completion_tokens=5)


class TestJsonExtraction:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ('{"a":1}', '{"a":1}'),
            ('```json\n{"a":1}\n```', '{"a":1}'),
            ('Here you go: {"a": {"b": 2}} hope that helps', '{"a": {"b": 2}}'),
            ('{"s":"a } brace inside a string"}', '{"s":"a } brace inside a string"}'),
        ],
    )
    def test_extracts_json_from_chatty_output(self, raw, expected):
        assert _extract_json(raw) == expected


class TestOfflineProvider:
    def test_needs_no_network_and_costs_nothing(self):
        p = OfflineProvider("offline")
        assert p.requires_network is False
        r = p.complete([ChatMessage("user", "<control_id>X</control_id>")])
        assert r.estimated_cost_usd == 0.0

    def test_is_deterministic(self):
        p = OfflineProvider("offline")
        msg = [
            ChatMessage(
                "user",
                "<control_id>X</control_id><client_evidence>documented and tested</client_evidence>",
            )
        ]
        assert p.complete(msg).text == p.complete(msg).text

    def test_negated_positive_terms_do_not_count(self):
        """The eval harness caught this: 'implemented' matched inside
        'Not implemented' and flipped a failing control to partial."""
        import json

        p = OfflineProvider("offline")
        r = p.complete(
            [
                ChatMessage(
                    "user",
                    "<control_id>X</control_id><client_evidence>Not implemented. No labels are applied.</client_evidence>",
                )
            ]
        )
        assert json.loads(r.text)["status"] == "non_compliant"

    def test_absent_evidence_abstains(self):
        import json

        p = OfflineProvider("offline")
        r = p.complete(
            [
                ChatMessage(
                    "user",
                    "<control_id>X</control_id><client_evidence>(none supplied for this control)</client_evidence>",
                )
            ]
        )
        assert json.loads(r.text)["status"] == "not_assessable"


class TestRouter:
    def test_retries_retryable_failures(self):
        provider = FlakyProvider(fail_times=2)
        router = ModelRouter(provider=provider)
        router._sleep = lambda _s: None
        router.complete([ChatMessage("user", "x")])
        assert provider.calls == 3

    def test_does_not_retry_non_retryable(self):
        provider = FlakyProvider(fail_times=1, retryable=False)
        router = ModelRouter(provider=provider)
        router._sleep = lambda _s: None
        with pytest.raises(ProviderError):
            router.complete([ChatMessage("user", "x")])
        assert provider.calls == 1

    def test_usage_accumulates(self):
        router = ModelRouter(provider=FlakyProvider(fail_times=0))
        router._sleep = lambda _s: None
        router.complete([ChatMessage("user", "x")])
        router.complete([ChatMessage("user", "x")])
        assert router.usage.calls == 2
        assert router.usage.prompt_tokens == 20

    def test_call_budget_is_enforced(self, monkeypatch):
        from muraqib.config import get_settings

        monkeypatch.setenv("MURAQIB_MAX_CALLS", "2")
        router = ModelRouter(
            provider=FlakyProvider(fail_times=0), settings=get_settings(refresh=True)
        )
        router.complete([ChatMessage("user", "x")])
        router.complete([ChatMessage("user", "x")])
        with pytest.raises(BudgetExceeded):
            router.complete([ChatMessage("user", "x")])

    def test_unknown_provider_is_rejected(self, monkeypatch):
        from muraqib.config import get_settings

        monkeypatch.setenv("MURAQIB_PROVIDER", "definitely-not-a-provider")
        with pytest.raises(ProviderError, match="unknown provider"):
            get_provider(get_settings(refresh=True))

    def test_provider_without_key_fails_clearly(self, monkeypatch):
        from muraqib.llm.providers import AnthropicProvider

        with pytest.raises(ProviderError, match="ANTHROPIC_API_KEY"):
            AnthropicProvider("claude-x", api_key="").complete([ChatMessage("user", "x")])
