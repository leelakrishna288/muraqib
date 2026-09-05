"""Retrieval benchmark.

A labelled query set with the controls a human expert says should be returned.
This is the test that would catch a corpus edit or a tokenizer change quietly
destroying recall - the kind of regression that shows up as "the assessments
got worse" three weeks later with no obvious cause.

The threshold is set against the HASHING fallback embedder, which is the worst
case. Installing the `rag` extra swaps in real semantic embeddings and recall
goes up, never down.
"""

import pytest

from muraqib.rag.embeddings import HashingEmbedder
from muraqib.rag.retriever import HybridRetriever

# query -> any one of these control ids is a correct top-k hit
QUERY_SET: dict[str, set[str]] = {
    "deleting personal data from the vector index and embeddings": {
        "NDMO.DC.02",
        "NDMO.PD.02",
        "GDPR.ART15.01",
        "GDPR.ART5.02",
    },
    "sending prompts to a model API hosted outside the country": {
        "KSA_PDPL.XB.01",
        "GDPR.ART44.01",
        "NDMO.PD.05",
        "UAE_PDPL.XB.01",
    },
    "who is accountable for the AI system": {"SDAIA.ACC.01", "NDMO.DG.01", "ISO42001.C5.01"},
    "prompt injection and poisoned tool calls": {"NDMO.SP.05"},
    "can a person ask a human to review a machine decision about them": {
        "SDAIA.HUM.01",
        "GDPR.ART22.01",
        "UAE_PDPL.ADM.01",
        "EUAIA.ART14.01",
    },
    "labelling documents as confidential or top secret": {"NDMO.CL.01", "NDMO.CL.02"},
    "testing the model for bias between demographic groups": {
        "SDAIA.FAIR.01",
        "SDAIA.FAIR.02",
        "EUAIA.ART10.01",
    },
    "keeping a log of which model answered which question": {
        "NDMO.SP.03",
        "SDAIA.ACC.02",
        "EUAIA.ART12.01",
    },
    "telling users they are talking to an AI": {"SDAIA.TRAN.01", "EUAIA.ART50.01"},
    "what happens when the model provider changes the model version": {
        "NIST.GOVERN.03",
        "NIST.MANAGE.03",
    },
    "backing up the data and testing that it restores": {"NDMO.DO.02"},
    "scanning dependencies for known vulnerabilities": {"NDMO.SP.04"},
}

RECALL_AT_5_FLOOR = 0.80


@pytest.fixture(scope="module")
def retriever(corpus):
    r = HybridRetriever(corpus.all_controls(), HashingEmbedder())
    r.index()
    return r


def test_recall_at_5_meets_the_floor(retriever):
    misses: list[str] = []
    for query, expected in QUERY_SET.items():
        got = {c.control_id for c in retriever.retrieve(query, top_k=5)}
        if not got & expected:
            misses.append(f"{query!r} -> {sorted(got)}")
    recall = 1 - len(misses) / len(QUERY_SET)
    assert recall >= RECALL_AT_5_FLOOR, f"recall@5 ={recall:.2f}; misses:\n" + "\n".join(misses)


def test_hybrid_fusion_is_not_worse_than_either_half(corpus):
    """If fusion ever underperforms both of its inputs, the weight is wrong and
    the extra complexity is not paying for itself."""
    controls = corpus.all_controls()

    def recall(alpha: float) -> float:
        r = HybridRetriever(controls, HashingEmbedder(), alpha=alpha)
        hits = sum(
            1
            for q, exp in QUERY_SET.items()
            if {c.control_id for c in r.retrieve(q, top_k=5)} & exp
        )
        return hits / len(QUERY_SET)

    lexical, dense, hybrid = recall(0.0), recall(1.0), recall(0.6)
    assert hybrid >= min(lexical, dense), (
        f"fusion (={hybrid:.2f}) is worse than both lexical (={lexical:.2f}) "
        f"and dense (={dense:.2f})"
    )
