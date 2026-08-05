"""Publish evidence-complete staged Sparex products as quote-only website items."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

if __package__:
    from scripts.odoo_runtime import ApplyGate, ArtifactStore, OdooClient, OdooConfig
else:
    from odoo_runtime import ApplyGate, ArtifactStore, OdooClient, OdooConfig

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV = ROOT / "odoo_connection.env"
DEFAULT_ARTIFACT_ROOT = ROOT / "outputs" / "sparex_quote_only_publication"
DEFAULT_BUCKET = "southern-parts-catalog-artifacts-475369996980-us-east-1"
WORKFLOW = "sparex-quote-only-publication"
MAX_BATCH = 200


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--odoo-env-file", type=Path, default=DEFAULT_ENV)
    parser.add_argument("--artifact-root", type=Path, default=DEFAULT_ARTIFACT_ROOT)
    parser.add_argument("--s3-bucket", default=os.environ.get("SOUTHERN_PRODUCT_ARTIFACT_BUCKET", DEFAULT_BUCKET))
    parser.add_argument("--s3-prefix", default="sparex/quote-only-publication")
    parser.add_argument("--limit", type=int, default=MAX_BATCH)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--reason", default="")
    return parser


def archive(store: ArtifactStore, name: str, payload, bucket: str, prefix: str) -> dict:
    count = len(payload) if isinstance(payload, list) else 1
    record = store.write_json(name, payload, record_count=count)
    return store.archive_s3(record, bucket=bucket, prefix=prefix)


def main() -> int:
    args = build_parser().parse_args()
    limit = max(1, min(int(args.limit or MAX_BATCH), MAX_BATCH))
    client = OdooClient(OdooConfig.from_env(args.odoo_env_file)).connect()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    store = ArtifactStore(args.artifact_root / stamp, schema_version="1.0")
    prefix = f"{args.s3_prefix.strip('/')}/{stamp}"
    plan = client.call("southern.vendor.catalog.item", "prepare_quote_publication_plan", limit=limit)
    plan_record = archive(store, "plan.json", plan, args.s3_bucket, prefix)
    result = {
        "mode": "apply" if args.apply else "dry-run",
        "planned": len(plan),
        "published": 0,
        "plan_uri": plan_record["artifact_uri"],
        "plan_sha256": plan_record["sha256"],
    }
    if args.apply and plan:
        ApplyGate(WORKFLOW, True, args.confirm, args.reason, MAX_BATCH).authorize(len(plan))
        applied = client.call(
            "southern.vendor.catalog.item",
            "apply_quote_publication_plan",
            records=plan,
            artifact_uri=plan_record["artifact_uri"],
            artifact_sha256=plan_record["sha256"],
            confirmation=args.confirm,
            reason=args.reason,
        )
        rollback_record = archive(store, "rollback.json", applied, args.s3_bucket, prefix)
        result.update(
            {
                "published": len(applied),
                "rollback_uri": rollback_record["artifact_uri"],
                "rollback_sha256": rollback_record["sha256"],
                "products": [
                    {"product_id": row["product_id"], "sku": row["sku"], "public_path": row["public_path"]}
                    for row in applied
                ],
            }
        )
    result_record = archive(store, "result.json", result, args.s3_bucket, prefix)
    result["result_uri"] = result_record["artifact_uri"]
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
