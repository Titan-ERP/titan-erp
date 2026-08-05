"""Load an archived vendor catalog extract into Odoo's lightweight staging index."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections.abc import Iterable, Iterator
from pathlib import Path

from scripts.odoo_runtime import ApplyGate, OdooClient, OdooConfig

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV = ROOT / "odoo_connection.env"
CONFIRMATION = "vendor-catalog-stage-import"
MAX_BATCH_SIZE = 2_000
SUPPORTED_SUFFIXES = {".csv", ".jsonl", ".ndjson"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_path", type=Path)
    parser.add_argument("--source-code", required=True)
    parser.add_argument("--artifact-uri", required=True)
    parser.add_argument("--artifact-sha256")
    parser.add_argument("--schema-version", default="1.0")
    parser.add_argument("--batch-size", type=int, default=2_000)
    parser.add_argument("--odoo-env-file", type=Path, default=DEFAULT_ENV)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    parser.add_argument("--reason")
    return parser


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_records(path: Path) -> Iterator[dict]:
    suffix = path.suffix.casefold()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError("Vendor catalog input must be CSV, JSONL, or NDJSON.")
    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            yield from csv.DictReader(handle)
        return
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise TypeError(f"Catalog line {line_number} must contain one JSON object.")
            yield value


def batches(records: Iterable[dict], size: int) -> Iterator[list[dict]]:
    batch = []
    for record in records:
        batch.append(record)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def main() -> int:
    args = build_parser().parse_args()
    if not args.input_path.is_file():
        raise FileNotFoundError(args.input_path)
    batch_size = max(1, min(int(args.batch_size or MAX_BATCH_SIZE), MAX_BATCH_SIZE))
    artifact_sha256 = (args.artifact_sha256 or file_sha256(args.input_path)).casefold()
    gate = ApplyGate(
        workflow=CONFIRMATION,
        apply_requested=args.apply,
        confirmation=args.confirm or "",
        reason=args.reason or "",
        max_records=MAX_BATCH_SIZE,
    )
    summary = {
        "source_code": args.source_code,
        "input_sha256": file_sha256(args.input_path),
        "artifact_uri": args.artifact_uri,
        "artifact_sha256": artifact_sha256,
        "schema_version": args.schema_version,
        "apply": args.apply,
        "batches": 0,
        "observed": 0,
        "created": 0,
        "updated": 0,
        "unchanged": 0,
        "ready": 0,
    }
    client = None
    for batch in batches(read_records(args.input_path), batch_size):
        summary["batches"] += 1
        summary["observed"] += len(batch)
        if not args.apply:
            continue
        gate.authorize(len(batch))
        if client is None:
            client = OdooClient(OdooConfig.from_env(args.odoo_env_file)).connect()
        result = client.call(
            "southern.vendor.catalog.item",
            "upsert_catalog_items",
            source_code=args.source_code,
            records=batch,
            artifact_uri=args.artifact_uri,
            artifact_sha256=artifact_sha256,
            schema_version=args.schema_version,
        )
        for key in ("created", "updated", "unchanged", "ready"):
            summary[key] += int(result.get(key) or 0)
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
