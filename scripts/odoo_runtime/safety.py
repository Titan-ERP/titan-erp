from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class WriteBlocked(RuntimeError):
    """Raised when a workflow has not satisfied every write control."""


@dataclass(frozen=True)
class ApplyGate:
    workflow: str
    apply_requested: bool
    confirmation: str = ""
    reason: str = ""
    max_records: int = 100

    def authorize(self, record_count: int) -> None:
        if not self.apply_requested:
            raise WriteBlocked("Dry-run mode: --apply was not supplied.")
        if os.environ.get("ODOO_WRITE_ENABLED", "").strip().casefold() not in {"1", "true", "yes"}:
            raise WriteBlocked("Set ODOO_WRITE_ENABLED=true for a supervised write window.")
        if self.confirmation.strip() != self.workflow:
            raise WriteBlocked(f"Pass --confirm {self.workflow!r} to identify this workflow.")
        if not self.reason.strip():
            raise WriteBlocked("Pass --reason with the approved business reason.")
        if record_count < 0 or record_count > self.max_records:
            raise WriteBlocked(
                f"Batch has {record_count} records, outside the supervised limit of {self.max_records}."
            )

    def idempotency_key(self, payload: Any) -> str:
        canonical = json.dumps(
            {"workflow": self.workflow, "payload": payload},
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def audit_row(self, payload: Any, record_count: int) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "workflow": self.workflow,
            "record_count": record_count,
            "reason": self.reason,
            "idempotency_key": self.idempotency_key(payload),
            "authorized_at_utc": datetime.now(timezone.utc).isoformat(),
        }


def append_audit(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
