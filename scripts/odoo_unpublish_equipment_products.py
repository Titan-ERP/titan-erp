"""Unpublish published equipment products while preserving product records."""

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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Dry-run or apply unpublishing for published equipment products."
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
        (type_field, "!=", "service"),
        (published_field, "=", True),
        "|",
        ("categ_id.complete_name", "ilike", "Equipment"),
        ("public_categ_ids.name", "ilike", "Equipment"),
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

    read_fields = [
        field
        for field in [
            "id",
            "default_code",
            "name",
            "list_price",
            "categ_id",
            "public_categ_ids",
            "is_published",
            "website_published",
            type_field,
        ]
        if field in fields
    ]
    public_category_names: dict[int, str] = {}
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
        public_ids = sorted(
            {
                category_id
                for product in products
                for category_id in (product.get("public_categ_ids") or [])
                if category_id not in public_category_names
            }
        )
        if public_ids:
            categories = execute(
                models,
                db,
                uid,
                api_key,
                "product.public.category",
                "read",
                [public_ids],
                {"fields": ["id", "name"]},
            )
            public_category_names.update(
                {category["id"]: category.get("name") or "" for category in categories}
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
                    "Website Categories": "; ".join(
                        public_category_names.get(category_id, str(category_id))
                        for category_id in (product.get("public_categ_ids") or [])
                    ),
                    "Type": product.get(type_field) or "",
                    "Action": "Unpublished" if args.apply else "Would unpublish",
                    "Reason": "Published equipment product",
                }
            )

    if args.apply and product_ids:
        values = {field: False for field in publish_fields}
        for id_chunk in chunks(product_ids):
            execute(models, db, uid, api_key, "product.template", "write", [id_chunk, values])

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
    report_path = REPORT_DIR / f"equipment_unpublish_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    with report_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "Product ID",
                "Internal Reference",
                "Name",
                "Sales Price",
                "Internal Category",
                "Website Categories",
                "Type",
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
        raise RuntimeError(f"{remaining} equipment products remain published")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
