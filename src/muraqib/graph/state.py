"""Explicit, serialisable run state.

Every node reads and writes this one object. Because it serialises cleanly, a
run can be checkpointed after each node and resumed - which matters when an
assessment spans 121 controls and a provider rate-limits you at control 80.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..models import (
    AssessmentReport,
    Finding,
    Framework,
    FrameworkCoverage,
    PlatformConfig,
    RiskAssessment,
    TokenUsage,
)


@dataclass
class RunState:
    run_id: str
    config: PlatformConfig
    frameworks: list[Framework]
    stage: str = "created"
    retrieved: dict[str, list[str]] = field(default_factory=dict)
    findings: dict[str, Finding] = field(default_factory=dict)
    risk: RiskAssessment | None = None
    coverage: list[FrameworkCoverage] = field(default_factory=list)
    report: AssessmentReport | None = None
    usage: TokenUsage = field(default_factory=TokenUsage)
    guardrail_events: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)
    model_used: str = ""
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    # ------------------------------------------------------------------
    def note_guardrail(self, event: dict[str, Any]) -> None:
        self.guardrail_events.append(event)

    def note_error(self, where: str, error: str) -> None:
        self.errors.append({"where": where, "error": error[:500]})

    def checkpoint(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self.run_id}.state.json"
        path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "stage": self.stage,
            "started_at": self.started_at,
            "config": self.config.model_dump(mode="json"),
            "frameworks": [f.value for f in self.frameworks],
            "retrieved": self.retrieved,
            "findings": {k: v.model_dump(mode="json") for k, v in self.findings.items()},
            "risk": self.risk.model_dump(mode="json") if self.risk else None,
            "coverage": [c.model_dump(mode="json") for c in self.coverage],
            "usage": self.usage.model_dump(mode="json"),
            "guardrail_events": self.guardrail_events,
            "errors": self.errors,
            "model_used": self.model_used,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunState:
        state = cls(
            run_id=data["run_id"],
            config=PlatformConfig.model_validate(data["config"]),
            frameworks=[Framework(f) for f in data["frameworks"]],
            stage=data.get("stage", "created"),
        )
        state.started_at = data.get("started_at", state.started_at)
        state.retrieved = data.get("retrieved", {})
        state.findings = {k: Finding.model_validate(v) for k, v in data.get("findings", {}).items()}
        state.risk = RiskAssessment.model_validate(data["risk"]) if data.get("risk") else None
        state.coverage = [FrameworkCoverage.model_validate(c) for c in data.get("coverage", [])]
        state.usage = TokenUsage.model_validate(data.get("usage", {}))
        state.guardrail_events = data.get("guardrail_events", [])
        state.errors = data.get("errors", [])
        state.model_used = data.get("model_used", "")
        return state

    @classmethod
    def load(cls, path: Path) -> RunState:
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
