"""Promote or refresh one bounded batch of evidence-complete Sparex catalog items."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from scripts.odoo_runtime import OdooClient, OdooConfig
from scripts.sparex_catalog_manifest import parse_s3_uri

PROMOTION_CONFIRMATION = "vendor-catalog-product-promotion"
OPERATIONAL_CONFIRMATION = "sparex-operational-batch-write"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--odoo-env-file", type=Path, required=True)
    parser.add_argument("--artifact-uri-prefix", required=True)
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    limit = max(1, min(args.limit, 100))
    client = OdooClient(OdooConfig.from_env(args.odoo_env_file)).connect()
    rows = client.call(
        "southern.vendor.catalog.item",
        "search_read",
        domain=[
            ("source_id.code", "=", "sparex"),
            ("catalog_state", "=", "ready_for_promotion"),
            ("readiness_blockers_json", "=", "[]"),
        ],
        fields=["id", "match_state"],
        order="promotion_requested desc, demand_count desc, last_seen_at, id",
        limit=limit,
    )
    missing_ids = [row["id"] for row in rows if row["match_state"] == "missing"]
    matched_ids = [row["id"] for row in rows if row["match_state"] == "matched"]
    results = {"promoted": [], "refreshed": []}
    if missing_ids:
        plan = client.call(
            "southern.vendor.catalog.item", "prepare_promotion_plan", item_ids=missing_ids, limit=len(missing_ids)
        )
        plan_bytes = json.dumps(plan, sort_keys=True, separators=(",", ":"), default=str).encode()
        plan_sha = hashlib.sha256(plan_bytes).hexdigest()
        artifact_uri = f"{args.artifact_uri_prefix.rstrip('/')}/{plan_sha}.json"
        bucket, key = parse_s3_uri(artifact_uri)
        import boto3

        boto3.client("s3").put_object(Bucket=bucket, Key=key, Body=plan_bytes, ContentType="application/json")
        results["promoted"] = client.call(
            "southern.vendor.catalog.item",
            "apply_promotion_plan",
            records=plan,
            artifact_uri=artifact_uri,
            artifact_sha256=plan_sha,
            confirmation=PROMOTION_CONFIRMATION,
            reason="Evidence-complete durable Sparex catalog promotion",
        )
    if matched_ids:
        results["refreshed"] = client.call(
            "southern.vendor.catalog.item",
            "apply_operational_batch",
            item_ids=matched_ids,
            confirmation=OPERATIONAL_CONFIRMATION,
            reason="Evidence-complete durable Sparex catalog refresh",
        )
    print(json.dumps({"state": "complete", **results}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
