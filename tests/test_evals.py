from muraqib.evals.harness import THRESHOLDS, load_golden, run_evaluation


def test_golden_set_is_well_formed(corpus):
    cases = load_golden()
    assert len(cases) >= 10
    for case in cases:
        assert corpus.control(case["control_id"]) is not None, f"{case['id']}: unknown control id"
        assert case["expected_status"] in {
            "compliant",
            "partial",
            "non_compliant",
            "not_applicable",
            "not_assessable",
        }


def test_golden_set_contains_abstention_and_failure_cases():
    """A golden set of only happy paths would let the engine drift toward
    confident guessing without the score moving."""
    statuses = [c["expected_status"] for c in load_golden()]
    assert statuses.count("not_assessable") >= 2
    assert statuses.count("non_compliant") >= 2


def test_baseline_engine_meets_every_threshold():
    result = run_evaluation()
    assert result.retrieval_recall >= THRESHOLDS["retrieval_recall"]
    assert result.citation_validity >= THRESHOLDS["citation_validity"]
    assert result.status_accuracy >= THRESHOLDS["status_accuracy"]
    assert result.over_claim_rate <= THRESHOLDS["over_claim_rate_max"], result.failures
    assert result.abstention_correctness >= THRESHOLDS["abstention_correctness"]
    assert result.passed, result.failures


def test_evaluation_is_reproducible():
    a, b = run_evaluation().to_dict(), run_evaluation().to_dict()
    for key in ("status_accuracy", "retrieval_recall", "over_claim_rate"):
        assert a[key] == b[key]
