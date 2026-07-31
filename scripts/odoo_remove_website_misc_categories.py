"""Replace obsolete website Miscellaneous tags using internal category paths.

Default mode is read-only. With ``--apply``, active products tagged with any
website category whose name contains "Misc" are assigned to the matching
customer-facing path derived from their non-Miscellaneous internal category.
Service/non-parts records have website categories cleared.
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from datetime import datetime

from odoo_cleanup_published_placeholders import OUT_DIR, connect, execute
from odoo_consolidate_website_taxonomy import INTERNAL_TO_PUBLIC
from odoo_reclassify_miscellaneous_products import ensure_public_path


def chunks(values, size=100):
    for offset in range(0, len(values), size):
        yield values[offset : offset + size]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    db, uid, key, models = connect()
    websites = execute(
        models,
        db,
        uid,
        key,
        "website",
        "search_read",
        [[]],
        {"fields": ["id", "name"], "limit": 100, "order": "id"},
    )
    website = next(
        (row for row in websites if "southern" in row["name"].lower()),
        websites[0],
    )
    misc_ids = execute(
        models,
        db,
        uid,
        key,
        "product.public.category",
        "search",
        [[("name", "ilike", "Misc")]],
        {"limit": 0},
    )
    product_ids = execute(
        models,
        db,
        uid,
        key,
        "product.template",
        "search",
        [[("active", "=", True), ("public_categ_ids", "in", misc_ids)]],
        {"limit": 0, "order": "id"},
    )
    products = []
    for product_chunk in chunks(product_ids, 500):
        products.extend(
            execute(
                models,
                db,
                uid,
                key,
                "product.template",
                "read",
                [product_chunk],
                {
                    "fields": [
                        "id",
                        "default_code",
                        "name",
                        "categ_id",
                        "public_categ_ids",
                    ]
                },
            )
        )

    category_ids = sorted(
        {product["categ_id"][0] for product in products if product.get("categ_id")}
    )
    categories = execute(
        models,
        db,
        uid,
        key,
        "product.category",
        "read",
        [category_ids],
        {"fields": ["complete_name"]},
    ) if category_ids else []
    internal_paths = {row["id"]: row["complete_name"] for row in categories}

    public_targets = {}
    updates = defaultdict(list)
    rows = []
    for product in products:
        internal_path = (
            internal_paths.get(product["categ_id"][0], "")
            if product.get("categ_id")
            else ""
        )
        target_path = INTERNAL_TO_PUBLIC.get(internal_path, "")
        if target_path not in public_targets:
            public_targets[target_path] = (
                ensure_public_path(
                    models,
                    db,
                    uid,
                    key,
                    website["id"],
                    target_path,
                    args.apply,
                )
                if target_path
                else None
            )
        public_id = public_targets[target_path]
        updates[public_id].append(product["id"])
        rows.append(
            {
                "Product ID": product["id"],
                "Internal Reference": product.get("default_code") or "",
                "Name": product.get("name") or "",
                "Internal Category": internal_path,
                "New Website Category": target_path or "(cleared)",
                "Status": "Updated" if args.apply else "Would update",
            }
        )

    if args.apply:
        for public_id, ids in updates.items():
            for id_chunk in chunks(ids):
                execute(
                    models,
                    db,
                    uid,
                    key,
                    "product.template",
                    "write",
                    [
                        id_chunk,
                        {
                            "public_categ_ids": [
                                (6, 0, [public_id] if public_id else [])
                            ]
                        },
                    ],
                )

    OUT_DIR.mkdir(exist_ok=True)
    report = OUT_DIR / (
        "website_misc_category_cleanup_"
        + datetime.now().strftime("%Y%m%d_%H%M%S")
        + ".csv"
    )
    with report.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "Product ID",
                "Internal Reference",
                "Name",
                "Internal Category",
                "New Website Category",
                "Status",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    remaining = execute(
        models,
        db,
        uid,
        key,
        "product.template",
        "search_count",
        [[("active", "=", True), ("public_categ_ids", "in", misc_ids)]],
    )
    print(
        {
            "mode": "apply" if args.apply else "dry_run",
            "matched": len(products),
            "updated": len(products) if args.apply else 0,
            "remaining": remaining,
            "report": str(report),
        }
    )
    return int(args.apply and remaining)


if __name__ == "__main__":
    raise SystemExit(main())
