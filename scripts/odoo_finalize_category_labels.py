"""Apply the final verified website category label cleanup."""

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
        "read",
        [[89, 114, 115]],
        {"fields": ["id", "name", "parent_id"]},
    )
    by_id = {row["id"]: row for row in categories}
    supplies = by_id.get(115)
    caps = by_id.get(89)
    if not supplies or supplies["name"] != "Shop Supplies":
        raise RuntimeError("Shop Supplies child guard failed")
    if not supplies.get("parent_id") or supplies["parent_id"][0] != 114:
        raise RuntimeError("Shop Supplies parent guard failed")
    if not caps or caps["name"] != "Caps":
        raise RuntimeError("Hydraulic Caps guard failed")

    caps_references = execute(
        models,
        db,
        uid,
        key,
        "product.template",
        "search_count",
        [[("public_categ_ids", "in", [89])]],
        {"context": {"active_test": False}},
    )
    caps_children = execute(
        models,
        db,
        uid,
        key,
        "product.public.category",
        "search_count",
        [[("parent_id", "=", 89)]],
    )
    if caps_references or caps_children:
        raise RuntimeError(
            "Refusing to remove Hydraulic / Caps because it is not empty"
        )

    if args.apply:
        execute(
            models,
            db,
            uid,
            key,
            "product.public.category",
            "write",
            [[115], {"name": "Tools & Consumables"}],
        )
        execute(
            models,
            db,
            uid,
            key,
            "product.public.category",
            "unlink",
            [[89]],
        )

    rows = [
        {
            "Category ID": 115,
            "Old Path": "Shop Supplies / Shop Supplies",
            "New Path": "Shop Supplies / Tools & Consumables",
            "Action": "Renamed" if args.apply else "Would rename",
        },
        {
            "Category ID": 89,
            "Old Path": "Hydraulic / Caps",
            "New Path": "",
            "Action": "Removed" if args.apply else "Would remove",
        },
    ]
    OUT_DIR.mkdir(exist_ok=True)
    report = OUT_DIR / (
        "final_category_label_cleanup_"
        + datetime.now().strftime("%Y%m%d_%H%M%S")
        + ".csv"
    )
    with report.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["Category ID", "Old Path", "New Path", "Action"],
        )
        writer.writeheader()
        writer.writerows(rows)
    print(
        {
            "mode": "apply" if args.apply else "dry_run",
            "updated": 2 if args.apply else 0,
            "caps_references": caps_references,
            "caps_children": caps_children,
            "report": str(report),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
