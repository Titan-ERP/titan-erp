"""Build one full-dataset Product Master Quality queue from live Odoo.

This workflow is read-only. It emits versioned, hashed CSV and JSON artifacts.
"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
from typing import Any

from odoo_runtime import ArtifactStore, OdooClient, OdooConfig


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
DEFAULT_OUTPUT = ROOT / "outputs" / "product_master_quality"


def relation_name(value: Any) -> str:
    return value[1] if isinstance(value, list) and len(value) > 1 else ""


def normalized_reference(value: Any) -> str:
    return "".join(str(value or "").upper().split())


def issue_codes(row: dict[str, Any], duplicate_counts: Counter[str]) -> list[str]:
    issues: list[str] = []
    price = float(row.get("list_price") or 0)
    cost = float(row.get("standard_price") or 0)
    reference = normalized_reference(row.get("default_code"))
    published = bool(row.get("website_published"))
    evidence_count = sum(
        int(row.get(field) or 0)
        for field in (
            "southern_specification_count",
            "southern_fitment_count",
            "southern_oem_reference_count",
            "southern_catalog_page_count",
        )
    )
    if price <= 1.49:
        issues.append("placeholder_price")
    elif cost > 0 and price <= cost:
        issues.append("price_not_above_cost")
    if not row.get("southern_source_url") and evidence_count == 0:
        issues.append("missing_evidence")
    if published and not row.get("public_categ_ids"):
        issues.append("taxonomy_review")
    if reference and duplicate_counts[reference] > 1:
        issues.append("duplicate_reference")
    if published and not row.get("image_128"):
        issues.append("published_missing_image")
    if published and not (row.get("description_ecommerce") or row.get("description_sale")):
        issues.append("published_missing_description")
    if not published and price > max(cost, 1.49) and row.get("public_categ_ids") and row.get("image_128"):
        issues.append("publication_ready")
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ENV_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    client = OdooClient(OdooConfig.from_env(args.env_file)).connect()
    available = client.fields("product.template")
    desired = [
        "id",
        "name",
        "default_code",
        "active",
        "sale_ok",
        "website_published",
        "list_price",
        "standard_price",
        "categ_id",
        "public_categ_ids",
        "description_sale",
        "description_ecommerce",
        "image_128",
        "southern_enrichment_status",
        "southern_source_name",
        "southern_source_url",
        "southern_specification_count",
        "southern_fitment_count",
        "southern_oem_reference_count",
        "southern_catalog_page_count",
    ]
    fields = [field for field in desired if field == "id" or field in available]
    products = client.search_read_all(
        "product.template",
        [],
        fields,
        context={"active_test": False, "bin_size": True},
    )
    duplicate_counts = Counter(
        reference for reference in (normalized_reference(row.get("default_code")) for row in products) if reference
    )

    queue: list[dict[str, Any]] = []
    summary: Counter[str] = Counter()
    for product in products:
        issues = issue_codes(product, duplicate_counts)
        for issue in issues:
            summary[issue] += 1
            queue.append(
                {
                    "issue": issue,
                    "product_id": product["id"],
                    "internal_reference": product.get("default_code") or "",
                    "name": product.get("name") or "",
                    "active": bool(product.get("active")),
                    "published": bool(product.get("website_published")),
                    "sales_price": product.get("list_price") or 0,
                    "cost": product.get("standard_price") or 0,
                    "internal_category": relation_name(product.get("categ_id")),
                    "public_category_count": len(product.get("public_categ_ids") or []),
                    "enrichment_status": product.get("southern_enrichment_status") or "",
                    "source_name": product.get("southern_source_name") or "",
                    "source_url": product.get("southern_source_url") or "",
                }
            )

    store = ArtifactStore(args.output_dir.resolve(), schema_version="1.0")
    csv_manifest = store.write_csv(
        "product_master_quality_queue.csv",
        queue,
        [
            "issue",
            "product_id",
            "internal_reference",
            "name",
            "active",
            "published",
            "sales_price",
            "cost",
            "internal_category",
            "public_category_count",
            "enrichment_status",
            "source_name",
            "source_url",
        ],
    )
    json_manifest = store.write_json(
        "product_master_quality_summary.json",
        {
            "odoo_uid": client.uid,
            "products_scanned": len(products),
            "queue_items": len(queue),
            "issue_counts": dict(sorted(summary.items())),
        },
    )
    print(
        {
            "mode": "read_only",
            "products_scanned": len(products),
            "queue_items": len(queue),
            "issues": dict(sorted(summary.items())),
            "queue_sha256": csv_manifest["sha256"],
            "summary_sha256": json_manifest["sha256"],
            "output": str(args.output_dir.resolve()),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
