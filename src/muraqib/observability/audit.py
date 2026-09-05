"""Hash-chained, append-only audit ledger.

Why this exists: "no standardized audit trail" is a documented open gap in
agentic tool protocols, and NDMO.SP.03 / SDAIA.ACC.02 / EU AI Act Art. 12 all
require that you can reconstruct why a system produced a given output. A tool
that assesses others on record-keeping had better keep records itself.

Each entry stores the SHA-256 of the previous entry, so any edit or deletion in
the middle of the file breaks verification. This is tamper-EVIDENT, not
tamper-PROOF - anyone who can rewrite the whole file can recompute the chain.
Real tamper-proofing needs an append-only sink (WORM storage, a managed
immutable log). The tool states this honestly rather than overclaiming.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

GENESIS = "0" * 64


@dataclass(slots=True)
class LedgerEntry:
    seq: int
    ts: str
    run_id: str
    event: str
    actor: str
    payload: dict[str, Any] = field(default_factory=dict)
    prev_hash: str = GENESIS
    entry_hash: str = ""

    def compute_hash(self) -> str:
        body = json.dumps(
            {
                "seq": self.seq,
                "ts": self.ts,
                "run_id": self.run_id,
                "event": self.event,
                "actor": self.actor,
                "payload": self.payload,
                "prev_hash": self.prev_hash,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(body.encode("utf-8")).hexdigest()


class AuditLedger:
    def __init__(self, run_id: str, path: Path | None = None):
        self.run_id = run_id
        self.path = path
        self._entries: list[LedgerEntry] = []
        self._lock = threading.Lock()
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event: str, actor: str = "system", **payload: Any) -> LedgerEntry:
        with self._lock:
            prev = self._entries[-1].entry_hash if self._entries else GENESIS
            entry = LedgerEntry(
                seq=len(self._entries),
                ts=datetime.now(timezone.utc).isoformat(),
                run_id=self.run_id,
                event=event,
                actor=actor,
                payload=_safe(payload),
                prev_hash=prev,
            )
            entry.entry_hash = entry.compute_hash()
            self._entries.append(entry)
            if self.path is not None:
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")
                    fh.flush()
                    os.fsync(fh.fileno())
            return entry

    @property
    def entries(self) -> list[LedgerEntry]:
        return list(self._entries)

    def verify(self) -> tuple[bool, str]:
        prev = GENESIS
        for e in self._entries:
            if e.prev_hash != prev:
                return False, f"chain broken at seq {e.seq}: prev_hash mismatch"
            if e.compute_hash() != e.entry_hash:
                return False, f"entry {e.seq} has been modified"
            prev = e.entry_hash
        return True, f"verified {len(self._entries)} entries"

    @classmethod
    def load(cls, path: Path) -> AuditLedger:
        ledger = cls(run_id="", path=None)
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                ledger._entries.append(LedgerEntry(**json.loads(line)))
        if ledger._entries:
            ledger.run_id = ledger._entries[0].run_id
        return ledger

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for e in self._entries:
            counts[e.event] = counts.get(e.event, 0) + 1
        return counts


def _safe(payload: dict[str, Any]) -> dict[str, Any]:
    """Ledger entries must never contain secrets or raw PII mappings."""
    out: dict[str, Any] = {}
    for k, v in payload.items():
        if any(
            t in k.lower()
            for t in ("key", "secret", "token", "password", "mapping", "authorization")
        ):
            out[k] = "[REDACTED]"
            continue
        try:
            json.dumps(v)
            out[k] = v
        except (TypeError, ValueError):
            out[k] = str(v)
    return out
