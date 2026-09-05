"""Model Context Protocol server over stdio.

Exposes Muraqib's deterministic capabilities as MCP tools so any MCP client
(Claude Desktop, Cursor, VS Code, Windsurf, a custom agent) can drive a
governance assessment. MCP was created by Anthropic in November 2024 and
donated to the Agentic AI Foundation under the Linux Foundation in December
2025, with Anthropic, Block and OpenAI as co-founders - so it is a
vendor-neutral standard, not a single vendor's protocol.

This is a hand-written JSON-RPC 2.0 implementation over stdio rather than a
wrapper around the SDK. Two reasons: it has zero dependencies, so the server
runs anywhere Python does; and the wire format is visible in the code, which is
what you want when you are explaining an agent stack to an auditor.

Design decisions worth noting:

* **Every exposed tool is deterministic or explicitly bounded.** A tool that
  could spend unbounded money is not exposed over MCP; ``assess_platform``
  enforces the same call and cost ceilings as every other entry point.
* **Tool results carry provenance.** Each result includes the run id and, where
  applicable, the audit-ledger verification status, because MCP has no
  standardised audit trail of its own - that gap is documented and this server
  fills it locally rather than pretending it does not exist.
* **Input is untrusted.** Arguments arrive from a model. They go through the
  same Pydantic validation, injection scan and PII redaction as an API request.
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Callable
from typing import Any

from .. import __version__
from ..agents.intake import IntakeBlocked
from ..config import get_settings
from ..corpus import Corpus
from ..models import Framework, PlatformConfig
from ..observability.logging_setup import configure_logging
from ..rag.embeddings import get_embedder
from ..rag.retriever import HybridRetriever

log = logging.getLogger("muraqib.mcp")

PROTOCOL_VERSION = "2025-06-18"

TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_frameworks",
        "description": (
            "List the AI governance and data protection instruments Muraqib can assess "
            "against, with their official names, issuing bodies, legal status (binding law "
            "vs non-binding guidance vs certifiable standard) and control counts."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "search_controls",
        "description": (
            "Search the control corpus using hybrid BM25 + vector retrieval. Use this to "
            "find which controls apply to a specific practice, e.g. 'deleting a customer "
            "record from a vector index' or 'sending prompts to a model hosted abroad'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural-language description of the practice or risk.",
                },
                "top_k": {"type": "integer", "minimum": 1, "maximum": 25, "default": 8},
                "frameworks": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional framework filter, e.g. ['NDMO','GDPR'].",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_control",
        "description": "Fetch one control by its id, including the assessment question and expected evidence.",
        "inputSchema": {
            "type": "object",
            "properties": {"control_id": {"type": "string"}},
            "required": ["control_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "classify_risk",
        "description": (
            "Compute the deterministic risk tier for a platform configuration. Returns the "
            "tier and every rule that fired. No model is involved, so the result is "
            "reproducible and explainable line by line."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"platform": {"type": "object", "description": "PlatformConfig object."}},
            "required": ["platform"],
            "additionalProperties": False,
        },
    },
    {
        "name": "assess_platform",
        "description": (
            "Run a full governance readiness assessment of an AI platform against the chosen "
            "frameworks. Returns coverage, weighted posture, risk tier, blocking gaps and "
            "per-control findings. This is a readiness and gap analysis, NOT a certification "
            "or legal advice. Bounded by the configured call and cost ceilings."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "platform": {"type": "object", "description": "PlatformConfig object."},
                "frameworks": {"type": "array", "items": {"type": "string"}},
                "format": {
                    "type": "string",
                    "enum": ["summary", "full", "markdown"],
                    "default": "summary",
                },
            },
            "required": ["platform"],
            "additionalProperties": False,
        },
    },
    {
        "name": "evaluate_transaction",
        "description": (
            "Evaluate ONE runtime transaction against the governance policy. Six gates run "
            "fail-closed in order - identity, entitlement, source authorization, "
            "pre-inference (residency, approved model, prompt minimisation, injection), "
            "output protection (DLP: allow/warn/mask/block), release. Returns the verdict, "
            "every gate result with reasons, the gate the journey stopped at, and a full "
            "reconstruction trace. Use this to answer 'is this specific request permitted "
            "right now', as opposed to assess_platform which answers 'is this platform "
            "governed' once."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "transaction": {"type": "object", "description": "TransactionContext object."},
                "policy_path": {
                    "type": "string",
                    "description": "Optional path to a governance policy YAML. Defaults to the built-in baseline.",
                },
            },
            "required": ["transaction"],
            "additionalProperties": False,
        },
    },
    {
        "name": "verify_audit_ledger",
        "description": (
            "Verify the hash chain of a Muraqib audit ledger file and report whether it has "
            "been altered since it was written."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to a .ledger.jsonl file."}
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
]


class ToolError(Exception):
    pass


class MuraqibMCPServer:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.corpus = Corpus.load(self.settings.corpus_dir)
        self._retriever: HybridRetriever | None = None
        self.handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "list_frameworks": self._list_frameworks,
            "search_controls": self._search_controls,
            "get_control": self._get_control,
            "classify_risk": self._classify_risk,
            "assess_platform": self._assess_platform,
            "evaluate_transaction": self._evaluate_transaction,
            "verify_audit_ledger": self._verify_ledger,
        }

    # -- lazily build the index; MCP clients expect a fast handshake -----
    @property
    def retriever(self) -> HybridRetriever:
        if self._retriever is None:
            embedder = get_embedder(self.settings.embedding_backend, self.settings.embedding_model)
            self._retriever = HybridRetriever(
                self.corpus.all_controls(), embedder, alpha=self.settings.hybrid_alpha
            )
            self._retriever.index()
        return self._retriever

    # ---------------- tool implementations ----------------
    def _list_frameworks(self, _: dict[str, Any]) -> dict[str, Any]:
        return {
            "frameworks": [
                {
                    "framework": p.framework.value,
                    "official_name": p.official_name,
                    "issuing_body": p.issuing_body,
                    "jurisdiction": p.jurisdiction,
                    "legal_status": p.obligation.value,
                    "status_note": p.status_note,
                    "source_url": p.source_url,
                    "controls": p.control_count,
                }
                for p in self.corpus.packs
            ],
            "total_controls": len(self.corpus.all_controls()),
            "note": (
                "Control text is paraphrased by Muraqib. Several instruments are non-binding "
                "guidance rather than law - check legal_status before describing one as a legal "
                "requirement."
            ),
        }

    def _search_controls(self, args: dict[str, Any]) -> dict[str, Any]:
        query = str(args.get("query", "")).strip()
        if not query:
            raise ToolError("query must not be empty")
        top_k = int(args.get("top_k", 8))
        fw_filter = {str(f).upper() for f in args.get("frameworks") or []} or None
        chunks = self.retriever.retrieve(query, top_k=top_k, framework_filter=fw_filter)
        results = []
        for chunk in chunks:
            control = self.corpus.control(chunk.control_id)
            results.append(
                {
                    "control_id": chunk.control_id,
                    "framework": chunk.metadata.get("framework"),
                    "domain": chunk.metadata.get("domain"),
                    "title": control.title if control else "",
                    "assurance_domain": control.assurance_domain.value if control else "",
                    "critical": control.critical if control else False,
                    "score": chunk.score,
                    "lexical_score": chunk.lexical_score,
                    "vector_score": chunk.vector_score,
                }
            )
        return {
            "query": query,
            "results": results,
            "retrieval": {
                "embedder": self.retriever.embedder.backend,
                "alpha": self.retriever.alpha,
            },
        }

    def _get_control(self, args: dict[str, Any]) -> dict[str, Any]:
        control = self.corpus.control(str(args.get("control_id", "")))
        if control is None:
            raise ToolError(f"unknown control id: {args.get('control_id')!r}")
        pack = self.corpus.pack(control.framework)
        return {
            "control": control.model_dump(mode="json"),
            "framework": {
                "official_name": pack.official_name,
                "issuing_body": pack.issuing_body,
                "legal_status": pack.obligation.value,
                "source_url": pack.source_url,
                "licence_note": pack.licence_note,
            },
        }

    def _classify_risk(self, args: dict[str, Any]) -> dict[str, Any]:
        from ..agents.risk import assess_risk  # noqa: PLC0415

        config = PlatformConfig.model_validate(args.get("platform") or {})
        risk = assess_risk(config)
        return {"platform": config.platform_name, "risk": risk.model_dump(mode="json")}

    def _assess_platform(self, args: dict[str, Any]) -> dict[str, Any]:
        from ..graph.orchestrator import (  # noqa: PLC0415
            build_context,
            get_orchestrator,
            new_run_id,
        )
        from ..reporting.render import render_markdown  # noqa: PLC0415

        config = PlatformConfig.model_validate(args.get("platform") or {})
        names = args.get("frameworks") or [f.value for f in self.corpus.frameworks]
        try:
            frameworks = [Framework(str(n).upper()) for n in names]
        except ValueError as exc:
            raise ToolError(f"unknown framework: {exc}") from exc

        run_id = new_run_id()
        ctx = build_context(self.settings, run_id=run_id, corpus=self.corpus)
        try:
            report = get_orchestrator(ctx).run(config, frameworks, run_id=run_id)
        except IntakeBlocked as exc:
            raise ToolError(str(exc)) from exc

        ledger_ok, ledger_detail = ctx.ledger.verify()
        fmt = str(args.get("format", "summary"))
        if fmt == "markdown":
            payload: dict[str, Any] = {"markdown": render_markdown(report)}
        elif fmt == "full":
            payload = {"report": json.loads(report.to_json())}
        else:
            payload = {
                "risk_tier": report.risk.tier.value,
                "coverage_pct": report.overall_coverage_pct,
                "weighted_pct": report.overall_weighted_pct,
                "blocking_gaps": report.blocking_gaps,
                "coverage": [c.model_dump(mode="json") for c in report.coverage],
                "not_assessable": [
                    f.control_id for f in report.findings if f.status.value == "not_assessable"
                ],
            }
        payload |= {
            "run_id": run_id,
            "engine": report.model_used,
            "model_calls": report.usage.calls,
            "estimated_cost_usd": report.usage.estimated_cost_usd,
            "audit_ledger_verified": ledger_ok,
            "audit_ledger_detail": ledger_detail,
            "disclaimer": report.disclaimer,
        }
        return payload

    def _evaluate_transaction(self, args: dict[str, Any]) -> dict[str, Any]:
        from ..observability.audit import AuditLedger  # noqa: PLC0415
        from ..runtime import (  # noqa: PLC0415
            GovernanceEngine,
            GovernancePolicy,
            TransactionContext,
            load_policy,
        )

        try:
            ctx = TransactionContext.model_validate(args.get("transaction") or {})
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"invalid transaction: {exc}") from exc

        policy_path = args.get("policy_path")
        try:
            policy = load_policy(policy_path) if policy_path else GovernancePolicy.default()
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"could not load policy: {exc}") from exc

        ledger = AuditLedger(run_id=ctx.transaction_id)
        decision = GovernanceEngine(policy, ledger=ledger).evaluate(ctx)
        return {
            "transaction_id": decision.transaction_id,
            "verdict": decision.verdict.value,
            "allowed": decision.allowed,
            "blocked_at": decision.blocked_at.value if decision.blocked_at else None,
            "masked": decision.masked,
            "gates": [
                {
                    "gate": g.gate.value,
                    "verdict": g.verdict.value,
                    "reasons": g.reasons,
                    "duration_ms": g.duration_ms,
                }
                for g in decision.gates
            ],
            "released_response": decision.released_response,
            "response_id": decision.response_id,
            "provenance": decision.provenance,
            "trace": decision.trace(),
            "policy": {
                "name": policy.name,
                "version": policy.version,
                "fail_closed": policy.fail_closed,
            },
            "audit_ledger_verified": decision.ledger_verified,
            "disclaimer": decision.disclaimer,
        }

    def _verify_ledger(self, args: dict[str, Any]) -> dict[str, Any]:
        from pathlib import Path  # noqa: PLC0415

        from ..observability.audit import AuditLedger  # noqa: PLC0415

        path = Path(str(args.get("path", "")))
        if not path.is_file():
            raise ToolError(f"ledger file not found: {path}")
        ledger = AuditLedger.load(path)
        ok, detail = ledger.verify()
        return {
            "run_id": ledger.run_id,
            "verified": ok,
            "detail": detail,
            "events": ledger.summary(),
        }

    # ---------------- JSON-RPC plumbing ----------------
    def handle(self, request: dict[str, Any]) -> dict[str, Any] | None:
        method = request.get("method", "")
        req_id = request.get("id")

        if method == "initialize":
            return self._ok(
                req_id,
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "muraqib", "version": __version__},
                    "instructions": (
                        "Muraqib does two things. (1) Assessment: it assesses AI platforms for "
                        "governance readiness against NDMO, SDAIA AI Ethics, KSA PDPL, UAE PDPL, "
                        "the EU AI Act, GDPR, NIST AI RMF and ISO/IEC 42001. (2) Runtime "
                        "governance: evaluate_transaction decides whether one specific request is "
                        "permitted right now, through six fail-closed gates. Results are a gap "
                        "analysis or a policy decision, never a certification or legal advice. "
                        "Check each framework's legal_status before calling it a legal "
                        "requirement - several are non-binding guidance. Note that "
                        "'not_assessable' and 'not_evidenced' mean the evidence did not settle "
                        "the question; they are not the same as a failure."
                    ),
                },
            )
        if method in ("notifications/initialized", "initialized"):
            return None
        if method == "ping":
            return self._ok(req_id, {})
        if method == "tools/list":
            return self._ok(req_id, {"tools": TOOLS})
        if method == "tools/call":
            params = request.get("params") or {}
            name = params.get("name", "")
            handler = self.handlers.get(name)
            if handler is None:
                return self._err(req_id, -32602, f"unknown tool: {name}")
            try:
                result = handler(params.get("arguments") or {})
            except ToolError as exc:
                return self._ok(
                    req_id,
                    {"content": [{"type": "text", "text": str(exc)}], "isError": True},
                )
            except Exception as exc:  # noqa: BLE001
                log.exception("tool failed", extra={"tool": name})
                return self._ok(
                    req_id,
                    {
                        "content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}],
                        "isError": True,
                    },
                )
            return self._ok(
                req_id,
                {
                    "content": [
                        {"type": "text", "text": json.dumps(result, indent=2, ensure_ascii=False)}
                    ],
                    "isError": False,
                },
            )
        return self._err(req_id, -32601, f"method not found: {method}")

    @staticmethod
    def _ok(req_id: Any, result: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    @staticmethod
    def _err(req_id: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}

    def serve_stdio(self, stdin=None, stdout=None) -> None:  # noqa: ANN001
        stdin = stdin or sys.stdin
        stdout = stdout or sys.stdout
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                stdout.write(json.dumps(self._err(None, -32700, "parse error")) + "\n")
                stdout.flush()
                continue
            response = self.handle(request)
            if response is not None:
                stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                stdout.flush()


def main() -> None:  # pragma: no cover
    # stdio is the transport - logs MUST go to stderr or they corrupt the stream.
    configure_logging(get_settings().log_level, json_output=True)
    MuraqibMCPServer().serve_stdio()


if __name__ == "__main__":  # pragma: no cover
    main()
