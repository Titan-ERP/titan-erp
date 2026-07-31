"""Clean safe duplicate/empty website category housekeeping issues."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime

from odoo_cleanup_published_placeholders import OUT_DIR, connect, execute


EMPTY_PATHS = {
    "Engine / Exhaust Parts",
    "Hardware / Hardware & Fasteners",
    "Hydraulic / Hydraulic Parts",
    "Hydraulic / Valves",
    "Filters / General Filters",
}
COLLAPSE_PATHS = {
    "Shop Supplies / Shop Supplies": "Shop Supplies",
}


def category_paths(categories):
    by_id = {row["id"]: row for row in categories}

    def path(category_id):
        parts = []
        seen = set()
        row = by_id.get(category_id)
        while row and row["id"] not in seen:
            seen.add(row["id"])
            parts.append(row["name"])
            parent = row.get("parent_id")
            row = by_id.get(parent[0]) if parent else None
        return " / ".join(reversed(parts))

    return {category_id: path(category_id) for category_id in by_id}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    db, uid, key, models = connect()
    categories = execute(
        models,
        db,
        uid,
        key,
        "product.public.category",
        "search_read",
        [[]],
        {"fields": ["id", "name", "parent_id"], "limit": 0, "order": "id"},
    )
    paths = category_paths(categories)
    ids_by_path = {}
    for category_id, path in paths.items():
        ids_by_path.setdefault(path, []).append(category_id)

    rows = []
    for source_path, target_path in COLLAPSE_PATHS.items():
        source_ids = ids_by_path.get(source_path, [])
        target_ids = ids_by_path.get(target_path, [])
        if len(source_ids) != 1 or len(target_ids) != 1:
            continue
        source_id, target_id = source_ids[0], target_ids[0]
        product_ids = execute(
            models,
            db,
            uid,
            key,
            "product.template",
            "search",
            [[("public_categ_ids", "in", [source_id])]],
            {"limit": 0, "context": {"active_test": False}},
        )
        if args.apply and product_ids:
            execute(
                models,
                db,
                uid,
                key,
                "product.template",
                "write",
                [product_ids, {"public_categ_ids": [(6, 0, [target_id])]}],
            )
        still_referenced = execute(
            models,
            db,
            uid,
            key,
            "product.template",
            "search_count",
            [[("public_categ_ids", "in", [source_id])]],
            {"context": {"active_test": False}},
        )
        if args.apply and still_referenced == 0:
            execute(
                models,
                db,
                uid,
                key,
                "product.public.category",
                "unlink",
                [[source_id]],
            )
        rows.append(
            {
                "Category ID": source_id,
                "Category Path": source_path,
                "Action": f"Collapsed to {target_path}",
                "Products Moved": len(product_ids),
                "Status": "Updated" if args.apply else "Would update",
            }
        )

    for path in sorted(EMPTY_PATHS):
        for category_id in ids_by_path.get(path, []):
            referenced = execute(
                models,
                db,
                uid,
                key,
                "product.template",
                "search_count",
                [[("public_categ_ids", "in", [category_id])]],
                {"context": {"active_test": False}},
            )
            if referenced:
                continue
            if args.apply:
                execute(
                    models,
                    db,
                    uid,
                    key,
                    "product.public.category",
                    "unlink",
                    [[category_id]],
                )
            rows.append(
                {
                    "Category ID": category_id,
                    "Category Path": path,
                    "Action": "Remove empty duplicate/category",
                    "Products Moved": 0,
                    "Status": "Removed" if args.apply else "Would remove",
                }
            )

    OUT_DIR.mkdir(exist_ok=True)
    report = OUT_DIR / (
        "website_category_housekeeping_"
        + datetime.now().strftime("%Y%m%d_%H%M%S")
        + ".csv"
    )
    with report.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "Category ID",
                "Category Path",
                "Action",
                "Products Moved",
                "Status",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)
    print(
        {
            "mode": "apply" if args.apply else "dry_run",
            "matched": len(rows),
            "report": str(report),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
