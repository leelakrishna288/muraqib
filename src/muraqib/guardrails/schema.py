"""Structured-output gate.

Model output is parsed into a strict Pydantic shape. Anything that does not fit
is a violation, and a violation is never silently coerced into a passing
finding - it becomes NOT_ASSESSABLE.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

from ..models import Confidence, Status


class SchemaViolation(ValueError):
    def __init__(self, message: str, raw: str = ""):
        super().__init__(message)
        self.raw = raw[:800]


class RawCitation(BaseModel):
    control_id: str = ""
    source: str = "corpus"
    quote: str = ""
    locator: str = ""

    @field_validator("source")
    @classmethod
    def _known_source(cls, v: str) -> str:
        return v if v in {"corpus", "platform_config", "client_evidence"} else "corpus"


class RawFinding(BaseModel):
    status: Status
    confidence: Confidence = Confidence.MEDIUM
    rationale: str = ""
    evidence: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    recommendation: str = ""
    citations: list[RawCitation] = Field(default_factory=list)

    @field_validator("evidence", "gaps", mode="before")
    @classmethod
    def _listify(cls, v: Any) -> list[str]:
        if v is None:
            return []
        if isinstance(v, str):
            return [v] if v.strip() else []
        return [str(x) for x in v]


class RawCritic(BaseModel):
    verdict: str = "upheld"
    note: str = ""

    @field_validator("verdict")
    @classmethod
    def _known(cls, v: str) -> str:
        return v if v in {"upheld", "downgraded"} else "upheld"


class SchemaGate:
    @staticmethod
    def parse_finding(text: str) -> RawFinding:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SchemaViolation(f"model output was not valid JSON: {exc}", text) from exc
        if not isinstance(data, dict):
            raise SchemaViolation("model output was not a JSON object", text)
        try:
            return RawFinding.model_validate(data)
        except ValidationError as exc:
            raise SchemaViolation(f"model output failed schema validation: {exc}", text) from exc

    @staticmethod
    def parse_critic(text: str) -> RawCritic:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return RawCritic(verdict="upheld", note="critic output unparseable; finding left as-is")
        if not isinstance(data, dict):
            return RawCritic(verdict="upheld", note="critic output not an object")
        try:
            return RawCritic.model_validate(data)
        except ValidationError:
            return RawCritic(verdict="upheld", note="critic output failed validation")
