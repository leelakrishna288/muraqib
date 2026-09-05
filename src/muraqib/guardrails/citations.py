"""Citation gate - the anti-hallucination control.

Rule: a finding may only assert a compliance status if it cites at least one
control that was actually retrieved for it. If the citation is missing, or
names a control id that does not exist in the loaded corpus, the finding is
**downgraded to NOT_ASSESSABLE** rather than dropped.

Downgrading rather than dropping is deliberate. Silently discarding a finding
would inflate the coverage percentage - the tool would look *better* the more
it hallucinated. Downgrading makes hallucination visibly reduce the score,
which is the correct incentive for a governance instrument.
"""

from __future__ import annotations

from ..models import Citation, Confidence, Finding, Status


class CitationGate:
    def __init__(self, valid_control_ids: set[str], require: bool = True):
        self.valid = valid_control_ids
        self.require = require
        self.rejections: list[dict[str, str]] = []

    def enforce(self, finding: Finding, retrieved_ids: list[str]) -> Finding:
        if not self.require:
            return finding
        if finding.status in (Status.NOT_ASSESSABLE, Status.NOT_APPLICABLE):
            return finding

        allowed = set(retrieved_ids) | {finding.control_id}
        kept: list[Citation] = []
        for c in finding.citations:
            if c.source == "corpus":
                if c.control_id in self.valid and c.control_id in allowed:
                    kept.append(c)
                else:
                    self.rejections.append(
                        {
                            "control_id": finding.control_id,
                            "reason": "cited a control that was not retrieved or does not exist",
                            "cited": c.control_id,
                        }
                    )
            else:
                kept.append(c)

        grounded = any(c.source == "corpus" for c in kept)
        if grounded:
            return finding.model_copy(update={"citations": kept})

        self.rejections.append(
            {
                "control_id": finding.control_id,
                "reason": "no valid corpus citation; downgraded to not_assessable",
                "cited": ",".join(c.control_id for c in finding.citations) or "(none)",
            }
        )
        return finding.model_copy(
            update={
                "status": Status.NOT_ASSESSABLE,
                "confidence": Confidence.HIGH,
                "citations": kept,
                "gaps": [
                    *finding.gaps,
                    "Assessment was not grounded in a retrieved control and was rejected.",
                ],
                "rationale": (
                    "Downgraded by the citation gate: the assessment did not cite a control "
                    "that was retrieved for it, so it cannot be relied upon."
                ),
            }
        )

    def as_events(self) -> list[dict[str, object]]:
        return [{"type": "citation_rejected", **r} for r in self.rejections]
