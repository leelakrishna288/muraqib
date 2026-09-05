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

    # -- phrase patterns ------------------------------------------------
    #
    # These pin the fix for the last production-assurance blocker: the baseline
    # was blind to present-tense evidence because its vocabulary was all past
    # participles. A base-form verb list was tried first and reverted (the eval
    # harness measured over-claim 0% -> 16.7%); phrase patterns replaced it.
    # Every case below is a case the token list gets wrong.

    @staticmethod
    def _status(evidence: str) -> str:
        import json

        p = OfflineProvider("offline")
        r = p.complete(
            [
                ChatMessage(
                    "user",
                    f"<control_id>X</control_id><client_evidence>{evidence}</client_evidence>",
                )
            ]
        )
        return str(json.loads(r.text)["status"])

    def test_present_tense_evidence_is_no_longer_invisible(self):
        """NDMO.DQ.03 - the control that blocked production assurance.

        Zero past participles, so the token list scored it 0/0 and returned
        NOT_ASSESSABLE against evidence that plainly describes a running gate.
        """
        assert (
            self._status(
                "Quality controls run on retrieval data before promotion, covering "
                "duplication, staleness and completeness, and a corpus version failing "
                "the checks is not promoted."
            )
            == "partial"
        )

    def test_pattern_only_evidence_never_reaches_compliant(self):
        """Grammatical role inferred from a neighbour is weaker than an explicit
        claim, so pattern-only evidence is capped at partial by design. If this
        ever returns 'compliant' the cap has been lost."""
        assert self._status("Validation checks run before ingestion.") == "partial"

    def test_noun_is_not_mistaken_for_a_verb(self):
        """The exact regression that reverted the base-form verb list: 'logs' is
        a noun here. It must not read as the verb 'to log'."""
        assert (
            self._status(
                "Not implemented. Embeddings and prompt logs carry no classification labels."
            )
            == "non_compliant"
        )

    def test_negated_predicate_inside_the_matched_span_is_discounted(self):
        """The negator sits between subject and verb. The negation window is
        measured from the END of a pattern match so it still catches it - the
        phrase contributes nothing, and the explicit negation carries the
        verdict down to non_compliant rather than leaving it a false partial."""
        assert self._status("Quality checks do not run before promotion.") == "non_compliant"

    def test_a_negated_pattern_alone_does_not_become_a_partial(self):
        """Without the end-of-match window this reads as one implementation
        phrase and returns 'partial' - a roadmap scored as a running control."""
        from muraqib.llm.providers import _pattern_hits

        assert _pattern_hits("quality checks do not run before promotion.") == []

    def test_fail_closed_wording_counts_but_a_missing_protection_does_not(self):
        """'X failing the check is not promoted' is the control working.
        'Data is not encrypted' is the control missing. Both contain 'not'."""
        assert self._status("Personal data is not encrypted at rest.") == "non_compliant"

    def test_futurity_is_not_implementation(self):
        """A roadmap scored PARTIAL before this: 'established' and 'approved'
        both counted, and 'will be' is not a negator so the window never saw it.
        A half-built verdict on a platform that has built nothing is the most
        expensive error this tool can make."""
        assert (
            self._status(
                "A data governance charter is planned for Q4 and the governance "
                "committee will be established once the charter is approved."
            )
            == "non_compliant"
        )

    def test_futurity_is_scoped_to_its_own_clause(self):
        """Guards the fix against over-correcting. A future commitment in one
        clause must not discount controls running in the next: 'reviewed' is
        disqualified by 'will be', while 'enforced' and 'logged' survive.

        The overall verdict on this evidence is PARTIAL rather than COMPLIANT,
        because the pre-existing weight-of-evidence rule still counts the future
        commitment as a non-implementation indicator. That is the conservative
        direction and is asserted here so the interaction stays visible.
        """
        from muraqib.llm.providers import _negated

        text = (
            "the charter will be reviewed annually; access is enforced with mfa "
            "and reviews are logged."
        )
        assert _negated(text, text.index("reviewed")) is True
        assert _negated(text, text.index("enforced")) is False
        assert _negated(text, text.index("logged")) is False
        assert self._status(text) == "partial"

    def test_carry_no_is_read_as_a_negation(self):
        """Neither 'carry' nor 'labels' is in either vocabulary, so this scored
        0/0 and abstained on a control that had plainly failed."""
        assert (
            self._status(
                "Classification labels exist in the catalogue, but embedding vectors "
                "and prompt logs carry no labels."
            )
            == "non_compliant"
        )

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
