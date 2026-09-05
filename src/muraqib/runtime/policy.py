"""Governance policy: the rules the gates enforce.

Kept entirely in data, not code, for the same reason the control corpus is:
a policy an auditor cannot read is a policy nobody can check. It loads from
YAML, validates strictly, and refuses to silently accept unknown fields that
would otherwise look enforced and do nothing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from .models import Classification


class Entitlement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agents: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)

    def permits_agent(self, name: str) -> bool:
        return "*" in self.agents or name in self.agents

    def permits_tool(self, name: str) -> bool:
        return "*" in self.tools or name in self.tools

    def permits_source(self, name: str) -> bool:
        return "*" in self.sources or name in self.sources


class IdentityPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    require_authentication: bool = True
    require_mfa: bool = True
    require_delegated_identity: bool = True
    allowed_channels: list[str] = Field(default_factory=lambda: ["web", "chat", "api"])


class ResidencyPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed_regions: list[str] = Field(default_factory=list)
    allow_cross_border: bool = False
    approved_models: list[str] = Field(default_factory=list)
    approved_providers: list[str] = Field(default_factory=list)
    require_prompt_redaction: bool = True
    block_classifications_cross_border: list[Classification] = Field(
        default_factory=lambda: [Classification.CONFIDENTIAL, Classification.RESTRICTED]
    )

    def region_allowed(self, region: str) -> bool:
        return not self.allowed_regions or region in self.allowed_regions


class DLPPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mask_pii: bool = True
    block_on_secrets: bool = True
    max_records: int = 0  # 0 disables the bulk-extraction check
    block_classifications: list[Classification] = Field(
        default_factory=lambda: [Classification.RESTRICTED]
    )
    blocked_phrases: list[str] = Field(default_factory=list)


class GovernancePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = "1.0"
    name: str = "default"
    fail_closed: bool = Field(
        default=True,
        description=(
            "When true, a gate that cannot establish a required fact "
            "(NOT_EVIDENCED) stops the transaction. Turning this off is a "
            "deliberate, recorded decision - it is never the default."
        ),
    )
    identity: IdentityPolicy = Field(default_factory=IdentityPolicy)
    entitlements: dict[str, Entitlement] = Field(default_factory=dict)
    residency: ResidencyPolicy = Field(default_factory=ResidencyPolicy)
    dlp: DLPPolicy = Field(default_factory=DLPPolicy)

    def effective_entitlement(self, keys: tuple[str, ...]) -> Entitlement:
        """Union of every entitlement the principal holds.

        Union, not intersection: holding two roles grants what either grants.
        A principal with no matching entitlement gets an empty one, which
        permits nothing - the deny-by-default that makes the gate meaningful.
        """
        agents: list[str] = []
        tools: list[str] = []
        sources: list[str] = []
        for key in keys:
            ent = self.entitlements.get(key)
            if ent is None:
                continue
            agents += ent.agents
            tools += ent.tools
            sources += ent.sources
        return Entitlement(
            agents=sorted(set(agents)), tools=sorted(set(tools)), sources=sorted(set(sources))
        )

    @classmethod
    def default(cls) -> GovernancePolicy:
        return cls()


def load_policy(path: str | Path) -> GovernancePolicy:
    raw: Any = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: policy file must contain a mapping")
    return GovernancePolicy.model_validate(raw)
