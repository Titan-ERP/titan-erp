from __future__ import annotations

import csv
import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


MIN_FREE_GB = 2
MIN_FREE_BYTES = MIN_FREE_GB * 1024**3


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class ArtifactStore:
    root: Path
    schema_version: str = "1.0"
    minimum_free_bytes: int = MIN_FREE_BYTES

    def ensure_capacity(self) -> None:
        free = shutil.disk_usage(self.root).free
        if free < self.minimum_free_bytes:
            raise RuntimeError(
                f"Artifact write blocked: {free / 1024**3:.2f} GB free; "
                f"{self.minimum_free_bytes / 1024**3:.0f} GB required."
            )

    def _record(self, artifact: Path, record_count: int, kind: str) -> dict[str, Any]:
        row = {
            "schema_version": self.schema_version,
            "kind": kind,
            "path": str(artifact.resolve()),
            "sha256": sha256_file(artifact),
            "bytes": artifact.stat().st_size,
            "record_count": record_count,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        manifest = self.root / "manifest.jsonl"
        with manifest.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
        return row

    def write_csv(self, name: str, rows: Iterable[dict[str, Any]], fields: list[str]) -> dict[str, Any]:
        self.root.mkdir(parents=True, exist_ok=True)
        self.ensure_capacity()
        materialized = list(rows)
        path = self.root / name
        with path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(materialized)
        return self._record(path, len(materialized), "csv")

    def write_json(self, name: str, value: Any, *, record_count: int = 1) -> dict[str, Any]:
        self.root.mkdir(parents=True, exist_ok=True)
        self.ensure_capacity()
        path = self.root / name
        envelope = {
            "schema_version": self.schema_version,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "data": value,
        }
        path.write_text(json.dumps(envelope, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
        return self._record(path, record_count, "json")
