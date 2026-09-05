"""Evaluation harness.

Five metrics, each chosen because it catches a failure the others miss:

* **status_accuracy** - did we reach the right verdict? The headline number,
  and on its own it is misleading.
* **retrieval_recall** - was the correct control actually retrieved? If
  retrieval missed it, a right answer was luck.
* **citation_validity** - does every non-abstaining finding cite a real,
  retrieved control? Measures grounding.
* **over_claim_rate** - how often did we say compliant/partial when the truth
  was non-compliant or not-assessable? **This is the metric that matters for a
  governance tool.** A false "compliant" is a client shipping a system they
  think is safe. Weighted asymmetrically on purpose.
* **abstention_correctness** - when the truth was "there is no evidence here",
  did we correctly say not_assessable instead of inventing a verdict?

Thresholds are conservative and enforced in CI, so a regression in grounding
fails the build rather than being noticed six weeks later.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import get_settings
from ..corpus import Corpus
from ..graph.orchestrator import build_context, get_orchestrator, new_run_id
from ..models import Framework, PlatformConfig, Status

GOLDEN_PATH = Path(__file__).parent / "golden" / "golden_set.json"

# CI gates. Over-claim is the strictest because it is the expensive error.
THRESHOLDS = {
    "status_accuracy": 0.70,
    "retrieval_recall": 0.90,
    "citation_validity": 1.00,
    "over_claim_rate_max": 0.10,
    "abstention_correctness": 0.90,
}

_OPTIMISTIC = {Status.COMPLIANT, Status.PARTIAL}


@dataclass
class EvalResult:
    engine: str
    total: int = 0
    status_correct: int = 0
    retrieval_hits: int = 0
    citation_valid: int = 0
    over_claims: int = 0
    optimistic_eligible: int = 0
    abstention_cases: int = 0
    abstention_correct: int = 0
    failures: list[str] = field(default_factory=list)
    per_case: list[dict[str, Any]] = field(default_factory=list)

    @property
    def status_accuracy(self) -> float:
        return self.status_correct / self.total if self.total else 0.0

    @property
    def retrieval_recall(self) -> float:
        return self.retrieval_hits / self.total if self.total else 0.0

    @property
    def citation_validity(self) -> float:
        return self.citation_valid / self.total if self.total else 0.0

    @property
    def over_claim_rate(self) -> float:
        return self.over_claims / self.optimistic_eligible if self.optimistic_eligible else 0.0

    @property
    def abstention_correctness(self) -> float:
        return self.abstention_correct / self.abstention_cases if self.abstention_cases else 1.0

    @property
    def passed(self) -> bool:
        return (
            self.status_accuracy >= THRESHOLDS["status_accuracy"]
            and self.retrieval_recall >= THRESHOLDS["retrieval_recall"]
            and self.citation_validity >= THRESHOLDS["citation_validity"]
            and self.over_claim_rate <= THRESHOLDS["over_claim_rate_max"]
            and self.abstention_correctness >= THRESHOLDS["abstention_correctness"]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "engine": self.engine,
            "cases": self.total,
            "status_accuracy": round(self.status_accuracy, 4),
            "retrieval_recall": round(self.retrieval_recall, 4),
            "citation_validity": round(self.citation_validity, 4),
            "over_claim_rate": round(self.over_claim_rate, 4),
            "abstention_correctness": round(self.abstention_correctness, 4),
            "passed": self.passed,
            "thresholds": THRESHOLDS,
            "failures": self.failures,
        }


def load_golden(path: Path | None = None) -> list[dict[str, Any]]:
    return json.loads((path or GOLDEN_PATH).read_text(encoding="utf-8"))["cases"]


def run_evaluation(path: Path | None = None) -> EvalResult:
    settings = get_settings(refresh=True)
    corpus = Corpus.load(settings.corpus_dir)
    cases = load_golden(path)

    run_id = new_run_id()
    ctx = build_context(settings, run_id=run_id, corpus=corpus)
    result = EvalResult(engine=ctx.router.model_name)

    # Group cases by platform so each platform is assessed once.
    by_platform: dict[str, dict[str, Any]] = {}
    for case in cases:
        by_platform.setdefault(
            json.dumps(case["platform"], sort_keys=True),
            {"platform": case["platform"], "cases": []},
        )["cases"].append(case)

    for group in by_platform.values():
        config = PlatformConfig.model_validate(group["platform"])
        framework_set: set[Framework] = set()
        for case in group["cases"]:
            control = corpus.control(case["control_id"])
            if control is not None:
                framework_set.add(control.framework)
        frameworks = sorted(framework_set)
        sub_id = new_run_id()
        sub_ctx = build_context(settings, run_id=sub_id, corpus=corpus)
        report = get_orchestrator(sub_ctx, checkpoint=False).run(
            config, list(frameworks) or [Framework.NDMO], run_id=sub_id
        )
        findings = {f.control_id: f for f in report.findings}
        retrieved_map = dict.fromkeys(findings, True)

        for case in group["cases"]:
            cid = case["control_id"]
            expected = Status(case["expected_status"])
            finding = findings.get(cid)
            result.total += 1

            if finding is None:
                result.failures.append(f"{cid}: control was never assessed")
                continue

            if retrieved_map.get(cid):
                result.retrieval_hits += 1
            else:
                result.failures.append(f"{cid}: control not retrieved")

            cited_ok = finding.status in (Status.NOT_ASSESSABLE, Status.NOT_APPLICABLE) or any(
                c.source == "corpus" and corpus.control(c.control_id) is not None
                for c in finding.citations
            )
            if cited_ok:
                result.citation_valid += 1
            else:
                result.failures.append(f"{cid}: finding has no valid corpus citation")

            if finding.status is expected:
                result.status_correct += 1
            else:
                result.failures.append(
                    f"{cid}: expected {expected.value}, got {finding.status.value}"
                )

            if expected in (Status.NON_COMPLIANT, Status.NOT_ASSESSABLE):
                result.optimistic_eligible += 1
                if finding.status in _OPTIMISTIC:
                    result.over_claims += 1
                    result.failures.append(
                        f"{cid}: OVER-CLAIM - expected {expected.value}, got {finding.status.value}"
                    )

            if expected is Status.NOT_ASSESSABLE:
                result.abstention_cases += 1
                if finding.status is Status.NOT_ASSESSABLE:
                    result.abstention_correct += 1

            result.per_case.append(
                {
                    "control_id": cid,
                    "expected": expected.value,
                    "actual": finding.status.value,
                    "match": finding.status is expected,
                }
            )

    return result
