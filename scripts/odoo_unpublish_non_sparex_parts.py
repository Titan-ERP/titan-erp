"""Unpublish published non-Sparex parts while preserving product records.

Default mode is a dry run. ``--apply`` only clears website publication fields
on active, saleable, non-service products in the internal Parts category whose
Internal Reference does not look like a Sparex SKU (``S.%``).
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
from pathlib import Path
from typing import Any

from odoo_cleanup_published_placeholders import connect, execute


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "odoo_imports" / "product_master" / "automation_reports"


def chunks(values: list[int], size: int = 500):
    for index in range(0, len(values), size):
        yield values[index : index + size]


def field_names(fields: dict[str, Any], requested: list[str]) -> list[str]:
    return [name for name in requested if name in fields]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Dry-run or apply unpublishing for published non-Sparex parts."
    )
    parser.add_argument("--apply", action="store_true", help="Apply the unpublish writes.")
    args = parser.parse_args()

    db, uid, api_key, models = connect()
    fields = execute(
        models,
        db,
        uid,
        api_key,
        "product.template",
        "fields_get",
        [],
        {"attributes": ["readonly"]},
    )
    publish_fields = [
        name
        for name in ("is_published", "website_published")
        if name in fields and not fields[name].get("readonly")
    ]
    if not publish_fields:
        raise RuntimeError("No writable product publication field is available")
    published_field = (
        "website_published" if "website_published" in fields else "is_published"
    )
    type_field = "detailed_type" if "detailed_type" in fields else "type"

    domain: list[Any] = [
        ("active", "=", True),
        ("sale_ok", "=", True),
        (type_field, "!=", "service"),
        (published_field, "=", True),
        ("default_code", "not ilike", "S.%"),
        ("categ_id.complete_name", "ilike", "Parts"),
    ]
    product_ids = execute(
        models,
        db,
        uid,
        api_key,
        "product.template",
        "search",
        [domain],
        {"context": {"active_test": False}, "order": "default_code asc,id asc"},
    )

    read_fields = field_names(
        fields,
        [
            "id",
            "default_code",
            "name",
            "list_price",
            "categ_id",
            "public_categ_ids",
            "is_published",
            "website_published",
            type_field,
        ],
    )
    rows: list[dict[str, Any]] = []
    for id_chunk in chunks(product_ids):
        products = execute(
            models,
            db,
            uid,
            api_key,
            "product.template",
            "read",
            [id_chunk],
            {"fields": read_fields, "context": {"active_test": False}},
        )
        for product in products:
            rows.append(
                {
                    "Product ID": product["id"],
                    "Internal Reference": product.get("default_code") or "",
                    "Name": product.get("name") or "",
                    "Sales Price": product.get("list_price") or 0,
                    "Internal Category": (
                        product["categ_id"][1] if product.get("categ_id") else ""
                    ),
                    "Public Category Count": len(product.get("public_categ_ids") or []),
                    "Type": product.get(type_field) or "",
                    "Was Published": "Yes",
                    "Action": "Unpublished" if args.apply else "Would unpublish",
                    "Reason": "Published part is not a Sparex S.% SKU",
                }
            )

    if args.apply and product_ids:
        values = {field: False for field in publish_fields}
        for id_chunk in chunks(product_ids):
            execute(
                models,
                db,
                uid,
                api_key,
                "product.template",
                "write",
                [id_chunk, values],
            )

    remaining = execute(
        models,
        db,
        uid,
        api_key,
        "product.template",
        "search_count",
        [domain],
        {"context": {"active_test": False}},
    )

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = REPORT_DIR / f"non_sparex_parts_unpublish_{stamp}.csv"
    with report_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "Product ID",
                "Internal Reference",
                "Name",
                "Sales Price",
                "Internal Category",
                "Public Category Count",
                "Type",
                "Was Published",
                "Action",
                "Reason",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    print(
        {
            "mode": "apply" if args.apply else "dry_run",
            "matched": len(product_ids),
            "unpublished": len(product_ids) if args.apply else 0,
            "remaining_matches": remaining,
            "report": str(report_path),
        }
    )
    if args.apply and remaining:
        raise RuntimeError(f"{remaining} non-Sparex parts remain published")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
