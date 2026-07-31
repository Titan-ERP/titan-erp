from __future__ import annotations

import argparse
import csv
from datetime import datetime
from pathlib import Path

from odoo_fill_published_basic_descriptions import (
    OUT_DIR,
    chunks,
    clean_text,
    connect,
    customer_ready,
    execute,
    sale_copy,
    website_copy,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fill customer descriptions for priced/category/image-ready unpublished parts."
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
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
        {"attributes": ["type"]},
    )
    published_field = "website_published" if "website_published" in fields else "is_published"
    type_field = "detailed_type" if "detailed_type" in fields else "type"
    description_fields = [
        field
        for field in ("description_ecommerce", "website_description", "description_sale")
        if field in fields
    ]
    website_field = "website_description" if "website_description" in fields else description_fields[0]

    domain = [
        ("active", "=", True),
        ("sale_ok", "=", True),
        (published_field, "=", False),
        (type_field, "!=", "service"),
        ("list_price", ">", 1.0),
        ("public_categ_ids", "!=", False),
        ("image_1920", "!=", False),
    ]
    ids = execute(
        models,
        db,
        uid,
        api_key,
        "product.template",
        "search",
        [domain],
        {"order": "id asc", "limit": args.limit or 0},
    )

    rows = []
    updated = 0
    read_fields = ["id", "default_code", "name", "list_price"] + description_fields
    for id_chunk in chunks(ids):
        products = execute(
            models,
            db,
            uid,
            api_key,
            "product.template",
            "read",
            [id_chunk],
            {"fields": read_fields},
        )
        for product in products:
            code = clean_text(product.get("default_code"))
            name = clean_text(product.get("name"))
            has_ready = any(customer_ready(product.get(field)) for field in description_fields)
            if has_ready or not code or not name:
                continue
            values = {
                website_field: website_copy(name, code),
                "description_sale": sale_copy(name, code),
            }
            if "description_ecommerce" in description_fields:
                values["description_ecommerce"] = website_copy(name, code)
            if args.apply:
                execute(
                    models,
                    db,
                    uid,
                    api_key,
                    "product.template",
                    "write",
                    [[product["id"]], values],
                )
                updated += 1
            rows.append(
                {
                    "Product ID": product["id"],
                    "Internal Reference": code,
                    "Name": name,
                    "Sales Price": product.get("list_price") or 0,
                    "Status": "Updated" if args.apply else "Would update",
                }
            )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report = OUT_DIR / f"ready_unpublished_description_fill_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    with report.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "Product ID",
                "Internal Reference",
                "Name",
                "Sales Price",
                "Status",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)
    print(
        {
            "mode": "apply" if args.apply else "dry_run",
            "ready_unpublished_checked": len(ids),
            "matched": len(rows),
            "updated": updated,
            "report": str(report),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
