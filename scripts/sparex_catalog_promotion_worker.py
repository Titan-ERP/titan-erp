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
DESCRIPTION_REPAIR_CONFIRMATION = "sparex-listing-description-repair"


def _archive_plan(artifact_uri_prefix: str, category: str, plan: list[dict]) -> tuple[str, str]:
    plan_bytes = json.dumps(plan, sort_keys=True, separators=(",", ":"), default=str).encode()
    plan_sha = hashlib.sha256(plan_bytes).hexdigest()
    artifact_uri = f"{artifact_uri_prefix.rstrip('/')}/{category}/{plan_sha}.json"
    bucket, key = parse_s3_uri(artifact_uri)
    import boto3

    boto3.client("s3").put_object(Bucket=bucket, Key=key, Body=plan_bytes, ContentType="application/json")
    return artifact_uri, plan_sha


def _chunks(values: list[int], size: int):
    for offset in range(0, len(values), size):
        yield values[offset : offset + size]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--odoo-env-file", type=Path, required=True)
    parser.add_argument("--artifact-uri-prefix", required=True)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--item-id", type=int, action="append", default=[])
    args = parser.parse_args()
    limit = max(1, min(args.limit, 100))
    client = OdooClient(OdooConfig.from_env(args.odoo_env_file)).connect()
    domain = [
        ("source_id.code", "=", "sparex"),
        ("readiness_blockers_json", "=", "[]"),
    ]
    if args.item_id:
        domain.append(("id", "in", list(dict.fromkeys(args.item_id))))
    else:
        domain.append(("catalog_state", "=", "ready_for_promotion"))
    rows = client.call(
        "southern.vendor.catalog.item",
        "search_read",
        domain=domain,
        fields=["id", "match_state"],
        order="promotion_requested desc, demand_count desc, last_seen_at, id",
        limit=limit,
    )
    missing_ids = [row["id"] for row in rows if row["match_state"] == "missing"]
    matched_ids = [row["id"] for row in rows if row["match_state"] == "matched"]
    results = {
        "promoted": [],
        "refreshed": [],
        "description_repaired": [],
        "description_artifacts": [],
    }
    if missing_ids:
        plan = client.call(
            "southern.vendor.catalog.item", "prepare_promotion_plan", item_ids=missing_ids, limit=len(missing_ids)
        )
        artifact_uri, plan_sha = _archive_plan(args.artifact_uri_prefix, "promotion", plan)
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
    affected_item_ids = [row["item_id"] for row in results["promoted"] + results["refreshed"]]
    if affected_item_ids:
        affected_rows = client.call(
            "southern.vendor.catalog.item",
            "search_read",
            domain=[("id", "in", affected_item_ids)],
            fields=["id", "normalized_sku", "product_id"],
            limit=len(affected_item_ids),
        )
        product_ids = [row["product_id"][0] for row in affected_rows if row.get("product_id")]
        normalized_skus = [row["normalized_sku"] for row in affected_rows]
        discovery_rows = client.call(
            "southern.sparex.discovery.item",
            "search_read",
            domain=[
                ("reconciliation_state", "=", "current"),
                ("matched_product_id", "in", product_ids),
                ("normalized_sku", "in", normalized_skus),
            ],
            fields=["id"],
            limit=len(affected_item_ids),
        )
        discovery_ids = [row["id"] for row in discovery_rows]
        for discovery_chunk in _chunks(discovery_ids, 50):
            description_plan = client.call(
                "southern.sparex.discovery.item",
                "prepare_description_repair_plan",
                limit=len(discovery_chunk),
                item_ids=discovery_chunk,
            )
            if not description_plan:
                continue
            artifact_uri, plan_sha = _archive_plan(
                args.artifact_uri_prefix, "description-repair", description_plan
            )
            results["description_artifacts"].append(
                {"artifact_uri": artifact_uri, "artifact_sha256": plan_sha}
            )
            repaired = client.call(
                "southern.sparex.discovery.item",
                "apply_description_repair_plan",
                records=description_plan,
                confirmation=DESCRIPTION_REPAIR_CONFIRMATION,
                reason=f"Targeted post-promotion description repair archived at {artifact_uri}",
            )
            results["description_repaired"].extend(repaired)
    print(json.dumps({"state": "complete", **results}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
