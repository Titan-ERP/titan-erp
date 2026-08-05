from __future__ import annotations

import csv
import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
        target = self.root
        while not target.exists() and target != target.parent:
            target = target.parent
        free = shutil.disk_usage(target).free
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
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "data": value,
        }
        path.write_text(json.dumps(envelope, indent=2, default=str) + "\n", encoding="utf-8")
        return self._record(path, record_count, "json")

    def archive_s3(
        self,
        record: dict[str, Any],
        *,
        bucket: str,
        prefix: str,
        s3_client: Any | None = None,
    ) -> dict[str, Any]:
        """Upload one recorded artifact with immutable integrity metadata."""
        if not bucket.strip():
            raise ValueError("An S3 bucket is required.")
        artifact = Path(record["path"]).resolve()
        expected_root = self.root.resolve()
        if expected_root not in artifact.parents or not artifact.is_file():
            raise ValueError("Artifact must be a file inside the configured store.")
        if sha256_file(artifact) != record["sha256"]:
            raise RuntimeError("Artifact checksum changed before archival.")
        if s3_client is None:
            import boto3

            s3_client = boto3.client("s3")
        key = "/".join(
            part.strip("/")
            for part in (prefix, self.schema_version, artifact.name)
            if part.strip("/")
        )
        metadata = {
            "sha256": record["sha256"],
            "schema-version": str(record["schema_version"]),
            "record-count": str(record["record_count"]),
        }
        s3_client.upload_file(
            str(artifact),
            bucket,
            key,
            ExtraArgs={"Metadata": metadata},
        )
        remote = s3_client.head_object(Bucket=bucket, Key=key)
        remote_metadata = remote.get("Metadata", {})
        if remote_metadata.get("sha256") != record["sha256"]:
            raise RuntimeError("S3 archive integrity metadata did not verify.")
        return {
            **record,
            "artifact_uri": f"s3://{bucket}/{key}",
            "archive_verified": True,
        }

    def prune(self, *, retention_days: int = 90, now: datetime | None = None) -> dict[str, int]:
        """Delete expired, manifest-owned artifacts while preserving an audit record."""
        if retention_days < 1:
            raise ValueError("retention_days must be at least one day.")
        manifest = self.root / "manifest.jsonl"
        if not manifest.exists():
            return {"deleted": 0, "retained": 0}
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=retention_days)
        retained: list[dict[str, Any]] = []
        deleted = 0
        for line in manifest.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            created = datetime.fromisoformat(row["created_at_utc"])
            artifact = Path(row["path"]).resolve()
            if created < cutoff and self.root.resolve() in artifact.parents:
                artifact.unlink(missing_ok=True)
                row["local_deleted_at_utc"] = datetime.now(timezone.utc).isoformat()
                deleted += 1
            retained.append(row)
        manifest.write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in retained),
            encoding="utf-8",
        )
        return {"deleted": deleted, "retained": len(retained)}
