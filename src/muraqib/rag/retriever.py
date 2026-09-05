"""Hybrid retrieval: BM25 lexical + dense vector, fused by weighted score.

Pure vector search underperforms badly on this corpus because control text is
full of exact terms that must match exactly - "cross-border transfer",
"Art. 22", "pgvector", "DPIA". BM25 catches those; the dense side catches the
paraphrases. The fusion weight is configurable and measured by the eval
harness rather than asserted.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

from ..models import Control
from .embeddings import Embedder, tokenize
from .store import InMemoryVectorStore, StoredDoc, VectorStore


@dataclass(slots=True)
class RetrievedChunk:
    control_id: str
    text: str
    score: float
    lexical_score: float = 0.0
    vector_score: float = 0.0
    metadata: dict[str, str] = field(default_factory=dict)


class BM25:
    """Standard Okapi BM25. ~60 lines, no dependency, fully testable."""

    def __init__(self, corpus: dict[str, str], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.doc_tokens = {doc_id: tokenize(text) for doc_id, text in corpus.items()}
        self.doc_len = {d: len(t) for d, t in self.doc_tokens.items()}
        self.avgdl = (sum(self.doc_len.values()) / len(self.doc_len)) if self.doc_len else 0.0
        self.tf = {d: Counter(t) for d, t in self.doc_tokens.items()}
        df: Counter[str] = Counter()
        for tokens in self.doc_tokens.values():
            df.update(set(tokens))
        n = max(1, len(self.doc_tokens))
        self.idf = {
            term: math.log(1 + (n - freq + 0.5) / (freq + 0.5)) for term, freq in df.items()
        }

    def score(self, query: str) -> dict[str, float]:
        q = tokenize(query)
        scores: dict[str, float] = {}
        for doc_id, tf in self.tf.items():
            dl = self.doc_len[doc_id] or 1
            s = 0.0
            for term in q:
                f = tf.get(term, 0)
                if not f:
                    continue
                idf = self.idf.get(term, 0.0)
                s += (
                    idf
                    * (f * (self.k1 + 1))
                    / (f + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
                )
            if s:
                scores[doc_id] = s
        return scores


def _normalise(scores: dict[str, float]) -> dict[str, float]:
    if not scores:
        return {}
    lo, hi = min(scores.values()), max(scores.values())
    if hi - lo < 1e-12:
        return dict.fromkeys(scores, 1.0)
    return {k: (v - lo) / (hi - lo) for k, v in scores.items()}


class HybridRetriever:
    def __init__(
        self,
        controls: list[Control],
        embedder: Embedder,
        store: VectorStore | None = None,
        alpha: float = 0.6,
    ):
        self.alpha = alpha
        self.embedder = embedder
        self.controls = {c.id: c for c in controls}
        self._texts = {c.id: c.as_document() for c in controls}
        self.bm25 = BM25(self._texts)
        self.store = store or InMemoryVectorStore()
        self._indexed = False

    def index(self) -> int:
        ids = list(self._texts)
        vectors = self.embedder.encode([self._texts[i] for i in ids])
        self.store.upsert(
            [
                StoredDoc(
                    id=cid,
                    text=self._texts[cid],
                    metadata={
                        "framework": self.controls[cid].framework.value,
                        "domain": self.controls[cid].domain,
                        "control_id": cid,
                    },
                    vector=vec,
                )
                for cid, vec in zip(ids, vectors, strict=True)
            ]
        )
        self._indexed = True
        return len(ids)

    def retrieve(
        self, query: str, top_k: int = 6, framework_filter: set[str] | None = None
    ) -> list[RetrievedChunk]:
        if not self._indexed:
            self.index()

        lexical = _normalise(self.bm25.score(query))
        qvec = self.embedder.encode([query])[0]
        dense_raw = {d.id: s for d, s in self.store.query(qvec, top_k=max(top_k * 4, 20))}
        dense = _normalise(dense_raw)

        fused: dict[str, float] = {}
        for cid in set(lexical) | set(dense):
            fused[cid] = self.alpha * dense.get(cid, 0.0) + (1 - self.alpha) * lexical.get(cid, 0.0)

        ranked = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)
        out: list[RetrievedChunk] = []
        for cid, score in ranked:
            control = self.controls.get(cid)
            if control is None:
                continue
            if framework_filter and control.framework.value not in framework_filter:
                continue
            out.append(
                RetrievedChunk(
                    control_id=cid,
                    text=self._texts[cid],
                    score=round(score, 6),
                    lexical_score=round(lexical.get(cid, 0.0), 6),
                    vector_score=round(dense.get(cid, 0.0), 6),
                    metadata={"framework": control.framework.value, "domain": control.domain},
                )
            )
            if len(out) >= top_k:
                break
        return out
