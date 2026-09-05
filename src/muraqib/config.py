"""Configuration. Secrets come from the environment only - never from a file in
the repo, never from a request body, never logged."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

_TRUE = {"1", "true", "yes", "on"}


def _flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    return default if raw is None else raw.strip().lower() in _TRUE


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(slots=True)
class Settings:
    # --- LLM ---
    provider: str = field(default_factory=lambda: os.getenv("MURAQIB_PROVIDER", "offline"))
    model: str = field(default_factory=lambda: os.getenv("MURAQIB_MODEL", "offline-deterministic"))
    temperature: float = field(default_factory=lambda: _float("MURAQIB_TEMPERATURE", 0.0))
    max_output_tokens: int = field(default_factory=lambda: _int("MURAQIB_MAX_TOKENS", 1400))
    request_timeout_s: int = field(default_factory=lambda: _int("MURAQIB_TIMEOUT", 60))
    max_retries: int = field(default_factory=lambda: _int("MURAQIB_MAX_RETRIES", 3))
    max_llm_calls_per_run: int = field(default_factory=lambda: _int("MURAQIB_MAX_CALLS", 400))
    max_cost_usd_per_run: float = field(default_factory=lambda: _float("MURAQIB_MAX_COST_USD", 2.0))

    # --- Corpus / RAG ---
    corpus_dir: Path = field(
        default_factory=lambda: Path(
            os.getenv("MURAQIB_CORPUS_DIR", str(REPO_ROOT / "corpus" / "frameworks"))
        )
    )
    vector_backend: str = field(
        default_factory=lambda: os.getenv("MURAQIB_VECTOR_BACKEND", "memory")
    )
    embedding_backend: str = field(
        default_factory=lambda: os.getenv("MURAQIB_EMBEDDING_BACKEND", "auto")
    )
    embedding_model: str = field(
        default_factory=lambda: os.getenv(
            "MURAQIB_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
        )
    )
    retrieval_top_k: int = field(default_factory=lambda: _int("MURAQIB_TOP_K", 6))
    hybrid_alpha: float = field(default_factory=lambda: _float("MURAQIB_HYBRID_ALPHA", 0.6))

    # --- Guardrails ---
    redact_pii: bool = field(default_factory=lambda: _flag("MURAQIB_REDACT_PII", True))
    block_on_injection: bool = field(default_factory=lambda: _flag("MURAQIB_BLOCK_INJECTION", True))
    require_citations: bool = field(
        default_factory=lambda: _flag("MURAQIB_REQUIRE_CITATIONS", True)
    )
    enable_critic: bool = field(default_factory=lambda: _flag("MURAQIB_ENABLE_CRITIC", True))

    # --- Orchestration ---
    # "builtin" is the dependency-free executor and the CI default. "langgraph"
    # runs the same six agents over the same RunState on a compiled StateGraph.
    # An unknown value is rejected at construction rather than at run time.
    orchestrator: str = field(
        default_factory=lambda: os.getenv("MURAQIB_ORCHESTRATOR", "builtin").strip().lower()
    )

    # --- Storage / audit ---
    data_dir: Path = field(
        default_factory=lambda: Path(os.getenv("MURAQIB_DATA_DIR", str(REPO_ROOT / "data")))
    )

    # --- API auth (OIDC / OAuth 2.1) ---
    auth_enabled: bool = field(default_factory=lambda: _flag("MURAQIB_AUTH_ENABLED", False))
    oidc_issuer: str = field(default_factory=lambda: os.getenv("MURAQIB_OIDC_ISSUER", ""))
    oidc_audience: str = field(default_factory=lambda: os.getenv("MURAQIB_OIDC_AUDIENCE", ""))
    oidc_jwks_uri: str = field(default_factory=lambda: os.getenv("MURAQIB_OIDC_JWKS_URI", ""))
    jwks_cache_seconds: int = field(default_factory=lambda: _int("MURAQIB_JWKS_TTL", 3600))
    dev_hs256_secret: str = field(default_factory=lambda: os.getenv("MURAQIB_DEV_HS256_SECRET", ""))

    # --- Observability ---
    log_level: str = field(default_factory=lambda: os.getenv("MURAQIB_LOG_LEVEL", "INFO"))
    otel_enabled: bool = field(default_factory=lambda: _flag("MURAQIB_OTEL_ENABLED", False))

    @property
    def runs_dir(self) -> Path:
        return self.data_dir / "runs"

    @property
    def index_dir(self) -> Path:
        return self.data_dir / "index"

    def api_key_for(self, provider: str) -> str:
        return os.getenv(
            {
                "anthropic": "ANTHROPIC_API_KEY",
                "openai": "OPENAI_API_KEY",
                "azure_openai": "AZURE_OPENAI_API_KEY",
                "gemini": "GEMINI_API_KEY",
                "groq": "GROQ_API_KEY",
            }.get(provider, "MURAQIB_API_KEY"),
            "",
        )

    def redacted(self) -> dict[str, object]:
        """Safe-to-log view. Never includes secrets."""
        return {
            "provider": self.provider,
            "model": self.model,
            "vector_backend": self.vector_backend,
            "embedding_backend": self.embedding_backend,
            "redact_pii": self.redact_pii,
            "block_on_injection": self.block_on_injection,
            "require_citations": self.require_citations,
            "enable_critic": self.enable_critic,
            "orchestrator": self.orchestrator,
            "auth_enabled": self.auth_enabled,
        }


_settings: Settings | None = None


def get_settings(refresh: bool = False) -> Settings:
    global _settings
    if _settings is None or refresh:
        _settings = Settings()
    return _settings
