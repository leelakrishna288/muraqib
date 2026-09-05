"""Embeddings with an honest zero-dependency fallback.

Two backends:

* ``sentence-transformers`` - real semantic embeddings, local, free, offline
  after the first model download. Used when the extra is installed.
* ``hashing`` - a deterministic hashed word/character n-gram vector. This is
  **lexical, not semantic**. It is a fallback so the system runs anywhere with
  no downloads, and so CI is hermetic. It is not claimed to be as good; the
  eval harness measures the difference.

Choosing "auto" prefers the real model and falls back silently, recording which
backend was used in the run manifest so a report never misrepresents how it was
produced.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
from typing import Protocol

log = logging.getLogger("muraqib.rag")

_TOKEN = re.compile(r"[a-z0-9]+")

# Light suffix stripping. Not a full Porter stemmer - just enough to collapse the
# morphological variants that actually cost recall on this corpus: a client writes
# "erasing customer records", the control says "erasure"; a client writes
# "classified", the control says "classification". Ordered longest-first so
# "-ation" is stripped before "-ion".
_SUFFIXES = (
    "ational",
    "ization",
    "isation",
    "iveness",
    "fulness",
    "ousness",
    "ation",
    "ition",
    "ement",
    "ances",
    "ences",
    "ingly",
    "ments",
    "ing",
    "ion",
    "ies",
    "ive",
    "ise",
    "ize",
    "ers",
    "ure",
    "ed",
    "es",
    "ly",
    "s",
)
_MIN_STEM = 4

# Words where stripping would destroy the term or merge unrelated concepts.
_NO_STEM = frozenset(
    {
        "access",
        "process",
        "address",
        "business",
        "class",
        "less",
        "loss",
        "status",
        "analysis",
        "basis",
        "bias",
        "gaps",
        "risks",
        "logs",
        "aims",
        "data",
        "gdpr",
        "ndmo",
        "sdaia",
        "pdpl",
        "iso",
        "nist",
        "eu",
        "uae",
        "ksa",
    }
)


def _stem(word: str) -> str:
    if word in _NO_STEM or len(word) <= _MIN_STEM or word.isdigit():
        return word
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= _MIN_STEM:
            return word[: -len(suffix)]
    return word


def tokenize(text: str) -> list[str]:
    """Lowercase, split on word characters, then light-stem."""
    return [_stem(w) for w in _TOKEN.findall(text.lower())]


class Embedder(Protocol):
    backend: str
    dimension: int

    def encode(self, texts: list[str]) -> list[list[float]]: ...


class HashingEmbedder:
    """Deterministic hashed n-gram vectors. No model, no download, no network."""

    backend = "hashing"

    def __init__(self, dimension: int = 512):
        self.dimension = dimension

    def _features(self, text: str) -> list[str]:
        words = tokenize(text)
        feats = list(words)
        feats += [f"{a}_{b}" for a, b in zip(words, words[1:], strict=False)]
        for w in words:
            if len(w) > 5:
                feats += [w[i : i + 4] for i in range(len(w) - 3)]
        return feats

    def encode(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            vec = [0.0] * self.dimension
            for feat in self._features(text):
                digest = hashlib.blake2b(feat.encode(), digest_size=8).digest()
                idx = int.from_bytes(digest[:4], "big") % self.dimension
                sign = 1.0 if digest[4] & 1 else -1.0
                vec[idx] += sign
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out


class SentenceTransformerEmbedder:
    """Real semantic embeddings. Runs locally on CPU; no API cost."""

    backend = "sentence-transformers"

    def __init__(self, model_name: str):
        from sentence_transformers import SentenceTransformer  # noqa: PLC0415

        self._model = SentenceTransformer(model_name)
        self.dimension = int(self._model.get_sentence_embedding_dimension())
        self.model_name = model_name

    def encode(self, texts: list[str]) -> list[list[float]]:
        vectors = self._model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return [list(map(float, v)) for v in vectors]


def get_embedder(
    backend: str = "auto", model_name: str = "sentence-transformers/all-MiniLM-L6-v2"
) -> Embedder:
    if backend == "hashing":
        return HashingEmbedder()
    if backend in ("auto", "sentence-transformers"):
        try:
            return SentenceTransformerEmbedder(model_name)
        except Exception as exc:  # noqa: BLE001 - fallback is the point
            if backend == "sentence-transformers":
                raise
            log.info(
                "sentence-transformers unavailable (%s); using hashing embedder", type(exc).__name__
            )
            return HashingEmbedder()
    raise ValueError(f"unknown embedding backend: {backend}")


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))
