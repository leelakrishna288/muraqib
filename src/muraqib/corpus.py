"""Loads framework packs from YAML and exposes lookup helpers."""

from __future__ import annotations

import functools
from pathlib import Path

import yaml

from .config import get_settings
from .models import AssuranceDomain, Control, Framework, FrameworkPack, Obligation


class CorpusError(RuntimeError):
    pass


def _load_pack(path: Path) -> FrameworkPack:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise CorpusError(f"{path.name}: expected a mapping at the top level")
    try:
        framework = Framework(raw["framework"])
        obligation = Obligation(raw["obligation"])
    except (KeyError, ValueError) as exc:
        raise CorpusError(f"{path.name}: {exc}") from exc

    controls: list[Control] = []
    seen: set[str] = set()
    for item in raw.get("controls", []):
        cid = item["id"]
        if cid in seen:
            raise CorpusError(f"{path.name}: duplicate control id {cid}")
        seen.add(cid)
        controls.append(
            Control(
                id=cid,
                framework=framework,
                domain=item["domain"],
                title=item["title"],
                question=" ".join(item["question"].split()),
                intent=" ".join(item.get("intent", "").split()),
                evidence_hints=item.get("evidence_hints", []),
                weight=item.get("weight", 1),
                assurance_domain=AssuranceDomain(
                    item.get("assurance_domain", AssuranceDomain.CROSS_CUTTING.value)
                ),
                critical=bool(item.get("critical", False)),
                source_url=raw.get("source_url", ""),
                verbatim_text_included=False,
            )
        )
    if not controls:
        raise CorpusError(f"{path.name}: no controls")

    return FrameworkPack(
        framework=framework,
        official_name=raw["official_name"],
        issuing_body=raw["issuing_body"],
        jurisdiction=raw.get("jurisdiction", ""),
        obligation=obligation,
        status_note=" ".join(raw.get("status_note", "").split()),
        source_url=raw.get("source_url", ""),
        licence_note=" ".join(raw.get("licence_note", "").split()),
        controls=controls,
    )


@functools.lru_cache(maxsize=8)
def _load_all(corpus_dir: str) -> tuple[FrameworkPack, ...]:
    directory = Path(corpus_dir)
    if not directory.is_dir():
        raise CorpusError(f"corpus directory not found: {directory}")

    files = sorted(directory.glob("*.yaml"))
    # Pointing at the repository's "corpus/" rather than "corpus/frameworks/" is
    # the obvious mistake, and it cost a container build: the service started,
    # found no packs and died with a message that did not say where it looked.
    if not files and (directory / "frameworks").is_dir():
        directory = directory / "frameworks"
        files = sorted(directory.glob("*.yaml"))

    if not files:
        raise CorpusError(
            f"no framework yaml files found in {directory} "
            f"(also tried {Path(corpus_dir) / 'frameworks'}). "
            "Set MURAQIB_CORPUS_DIR to the directory containing the framework "
            "yaml files."
        )
    return tuple(_load_pack(p) for p in files)


class Corpus:
    """In-memory view of every loaded framework."""

    def __init__(self, packs: tuple[FrameworkPack, ...]):
        self.packs = packs
        self._by_framework = {p.framework: p for p in packs}
        self._by_control: dict[str, Control] = {}
        for pack in packs:
            for control in pack.controls:
                self._by_control[control.id] = control

    @classmethod
    def load(cls, corpus_dir: str | Path | None = None) -> Corpus:
        directory = str(corpus_dir or get_settings().corpus_dir)
        return cls(_load_all(directory))

    @property
    def frameworks(self) -> list[Framework]:
        return list(self._by_framework)

    def pack(self, framework: Framework) -> FrameworkPack:
        try:
            return self._by_framework[framework]
        except KeyError as exc:
            raise CorpusError(f"framework not loaded: {framework}") from exc

    def control(self, control_id: str) -> Control | None:
        return self._by_control.get(control_id)

    def controls(self, frameworks: list[Framework] | None = None) -> list[Control]:
        chosen = frameworks or self.frameworks
        out: list[Control] = []
        for fw in chosen:
            out.extend(self.pack(fw).controls)
        return out

    def all_controls(self) -> list[Control]:
        return list(self._by_control.values())

    def stats(self) -> dict[str, int]:
        return {p.framework.value: p.control_count for p in self.packs}

    def by_domain(
        self, frameworks: list[Framework] | None = None
    ) -> dict[AssuranceDomain, list[Control]]:
        out: dict[AssuranceDomain, list[Control]] = {d: [] for d in AssuranceDomain}
        for control in self.controls(frameworks):
            out[control.assurance_domain].append(control)
        return out

    def critical_controls(self, frameworks: list[Framework] | None = None) -> list[Control]:
        return [c for c in self.controls(frameworks) if c.critical]
