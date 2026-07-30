"""Remove the empty legacy Parts website tree after taxonomy consolidation.

The script fails closed if any active or archived product still references a
target category. Default mode is a read-only dry run; use ``--apply`` to unlink
the verified-empty website categories, deepest children first.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime

from odoo_cleanup_published_placeholders import OUT_DIR, connect, execute


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
        {
            "fields": ["id", "name", "parent_id"],
            "limit": 0,
            "order": "id",
        },
    )
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

    paths = {category_id: path(category_id) for category_id in by_id}
    target_ids = sorted(
        category_id
        for category_id, category_path in paths.items()
        if category_path == "Parts"
        or category_path.startswith("Parts /")
        or category_path == "Miscellaneous"
    )
    referenced = execute(
        models,
        db,
        uid,
        key,
        "product.template",
        "search_count",
        [[("public_categ_ids", "in", target_ids)]],
        {"context": {"active_test": False}},
    )
    if referenced:
        raise RuntimeError(
            f"Refusing to remove legacy categories: {referenced} products reference them"
        )

    ordered_ids = sorted(
        target_ids,
        key=lambda category_id: paths[category_id].count(" / "),
        reverse=True,
    )
    rows = [
        {
            "Category ID": category_id,
            "Category Path": paths[category_id],
            "Status": "Removed" if args.apply else "Would remove",
        }
        for category_id in ordered_ids
    ]
    if args.apply:
        for category_id in ordered_ids:
            execute(
                models,
                db,
                uid,
                key,
                "product.public.category",
                "unlink",
                [[category_id]],
            )

    OUT_DIR.mkdir(exist_ok=True)
    report = OUT_DIR / (
        "legacy_website_taxonomy_cleanup_"
        + datetime.now().strftime("%Y%m%d_%H%M%S")
        + ".csv"
    )
    with report.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["Category ID", "Category Path", "Status"],
        )
        writer.writeheader()
        writer.writerows(rows)
    print(
        {
            "mode": "apply" if args.apply else "dry_run",
            "matched": len(rows),
            "product_references": referenced,
            "removed": len(rows) if args.apply else 0,
            "report": str(report),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
