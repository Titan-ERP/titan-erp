"""Consolidate the duplicate Parts website tree into customer-facing families.

The internal inventory category remains the operational source of truth.
Website categories are reduced to one canonical, specific path per active part.
Default mode is read-only; use ``--apply`` to update Odoo.
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from datetime import datetime

from odoo_cleanup_published_placeholders import OUT_DIR, connect, execute
from odoo_reclassify_miscellaneous_products import ensure_public_path


CANONICAL_ROOTS = {
    "Bearings",
    "Brakes",
    "Cab & Body",
    "Cooling",
    "Electrical",
    "Engine",
    "Filters",
    "Fuel System",
    "General Parts",
    "Ground Engaging Tools",
    "Hardware",
    "Heavy Equipment",
    "Hydraulic",
    "Implements & Attachments",
    "Lubricants",
    "Paint",
    "PTO & Driveline",
    "Seals",
    "Shop Supplies",
    "Undercarriage",
}

INTERNAL_TO_PUBLIC = {
    "Parts": "General Parts",
    "Parts / Bearings": "Bearings / Ball & Roller Bearings",
    "Parts / Bearings / Bearing Kits": "Bearings / Bearing Kits",
    "Parts / Belts": "Engine / Belts & Tensioners",
    "Parts / Brakes": "Brakes / Brake Parts",
    "Parts / Cab": "Cab & Body / Cab & Body Parts",
    "Parts / Cooling": "Cooling / Cooling Parts",
    "Parts / Driveline": "PTO & Driveline / Driveline Parts",
    "Parts / Electrical": "Electrical / Electrical Parts",
    "Parts / Engine": "Engine / Engine Parts",
    "Parts / Engine / Exhaust": "Engine / Exhaust Parts",
    "Parts / Engine / Gaskets": "Engine / Gaskets",
    "Parts / Filters": "Filters / General Filters",
    "Parts / Filters / Air Filters": "Filters / Air & Cabin Filters",
    "Parts / Filters / Cab Filters": "Filters / Air & Cabin Filters",
    "Parts / Filters / Engine Oil Filters": "Filters / Engine Oil Filters",
    "Parts / Filters / Fuel Filters": "Filters / Fuel Filters",
    "Parts / Filters / Fuel Water Separators": "Filters / Fuel Water Separators",
    "Parts / Filters / Hydraulic Filters": "Filters / Hydraulic Filters",
    "Parts / Fuel System": "Fuel System / Fuel System Parts",
    "Parts / Ground Engaging Tools": (
        "Ground Engaging Tools / Cutting Edges & Teeth"
    ),
    "Parts / Ground Engaging Tools / Cutting Edges": (
        "Ground Engaging Tools / Cutting Edges & Teeth"
    ),
    "Parts / Hardware": "Hardware / Hardware & Fasteners",
    "Parts / Heavy Equipment": "Heavy Equipment / Heavy Equipment Parts",
    "Parts / Heavy Equipment / Caterpillar": (
        "Heavy Equipment / Caterpillar Parts"
    ),
    "Parts / Hydraulic": "Hydraulic / Hydraulic Parts",
    "Parts / Hydraulic / Hydraulic Adapters": "Hydraulic / Adapters",
    "Parts / Hydraulic / Hydraulic Couplers": "Hydraulic / Couplers",
    "Parts / Hydraulic / Hydraulic Cylinders": "Hydraulic / Cylinders",
    "Parts / Hydraulic / Hydraulic Elbows": "Hydraulic / Elbows",
    "Parts / Hydraulic / Hydraulic Hoses": "Hydraulic / Hoses",
    "Parts / Hydraulic / Hydraulic Plugs": "Hydraulic / Plugs",
    "Parts / Hydraulic / Hydraulic Tees": "Hydraulic / Tees",
    "Parts / Hydraulic / Hydraulic Valves": "Hydraulic / Valves",
    "Parts / Implements": "Implements & Attachments / Implement Parts",
    "Parts / Linkage": "PTO & Driveline / Hitch & Linkage",
    "Parts / Lubricants": "Lubricants / Oils, Fluids & Grease",
    "Parts / Paint": "Paint / Spray Paint",
    "Parts / PTO": "PTO & Driveline / PTO Parts",
    "Parts / Seals": "Seals / General Seals",
    "Parts / Seals / Hydraulic Seal Kits": "Seals / Seal Kits",
    "Parts / Seals / Hydraulic Seals": "Seals / Hydraulic Seals",
    "Parts / Seals / Oil Seals": "Seals / Oil Seals",
    "Parts / Seals / Wheel Seals": "Seals / Wheel Seals",
    "Parts / Shop Supplies": "Shop Supplies",
    "Parts / Undercarriage": "Undercarriage / Undercarriage Parts",
}

RENAMES = {
    "Filters / Other Filters": "General Filters",
    "Hardware / Miscellaneous Hardware": "Hardware & Fasteners",
}


def chunks(values, size=100):
    for offset in range(0, len(values), size):
        yield values[offset : offset + size]


def path_map(categories):
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
    categories = execute(
        models,
        db,
        uid,
        key,
        "product.public.category",
        "search_read",
        [[]],
        {
            "fields": ["id", "name", "parent_id", "website_id"],
            "limit": 0,
            "order": "id",
        },
    )
    paths = path_map(categories)

    rename_rows = []
    for old_path, new_name in RENAMES.items():
        category_id = next(
            (category_id for category_id, path in paths.items() if path == old_path),
            None,
        )
        if category_id:
            if args.apply:
                execute(
                    models,
                    db,
                    uid,
                    key,
                    "product.public.category",
                    "write",
                    [[category_id], {"name": new_name}],
                )
            rename_rows.append(
                {
                    "Category ID": category_id,
                    "Old Path": old_path,
                    "New Label": new_name,
                    "Status": "Renamed" if args.apply else "Would rename",
                }
            )

    product_ids = execute(
        models,
        db,
        uid,
        key,
        "product.template",
        "search",
        [[("active", "=", True), ("categ_id.complete_name", "=ilike", "Parts%")]],
        {"limit": 0, "order": "id"},
    )
    products = []
    for id_chunk in chunks(product_ids, 500):
        products.extend(
            execute(
                models,
                db,
                uid,
                key,
                "product.template",
                "read",
                [id_chunk],
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
    internal_ids = sorted(
        {row["categ_id"][0] for row in products if row.get("categ_id")}
    )
    internal_categories = execute(
        models,
        db,
        uid,
        key,
        "product.category",
        "read",
        [internal_ids],
        {"fields": ["complete_name"]},
    )
    internal_paths = {
        row["id"]: row["complete_name"] for row in internal_categories
    }

    target_paths = {}
    rows = []
    conflicts = 0
    for product in products:
        current_paths = [
            paths.get(category_id, "")
            for category_id in product.get("public_categ_ids", [])
        ]
        canonical = [
            path
            for path in current_paths
            if path.split(" / ")[0] in CANONICAL_ROOTS
            and "Miscellaneous" not in path
            and "Other Filters" not in path
        ]
        distinct_canonical = sorted(set(canonical))
        if len(distinct_canonical) > 1:
            conflicts += 1
        internal_path = internal_paths.get(product["categ_id"][0], "")
        target_path = INTERNAL_TO_PUBLIC.get(internal_path)
        if not target_path and distinct_canonical:
            target_path = max(
                distinct_canonical,
                key=lambda value: (value.count(" / "), value),
            )
        if not target_path:
            rows.append(
                {
                    "Product ID": product["id"],
                    "Internal Reference": product.get("default_code") or "",
                    "Name": product.get("name") or "",
                    "Internal Category": internal_path,
                    "Old Website Categories": "; ".join(current_paths),
                    "New Website Category": "",
                    "Status": "Needs review",
                }
            )
            continue
        already_single_category = len(current_paths) == 1
        if not already_single_category:
            target_paths.setdefault(target_path, []).append(product["id"])
        rows.append(
            {
                "Product ID": product["id"],
                "Internal Reference": product.get("default_code") or "",
                "Name": product.get("name") or "",
                "Internal Category": internal_path,
                "Old Website Categories": "; ".join(current_paths),
                "New Website Category": target_path,
                "Status": (
                    "Already one category"
                    if already_single_category
                    else "Updated"
                    if args.apply
                    else "Would update"
                ),
            }
        )

    if args.apply:
        public_ids = {
            target_path: ensure_public_path(
                models,
                db,
                uid,
                key,
                website["id"],
                target_path,
                True,
            )
            for target_path in target_paths
        }
        for target_path, ids in target_paths.items():
            public_id = public_ids[target_path]
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
                        {"public_categ_ids": [(6, 0, [public_id])]},
                    ],
                )

    OUT_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    product_report = OUT_DIR / f"taxonomy_consolidation_{stamp}.csv"
    rename_report = OUT_DIR / f"taxonomy_category_renames_{stamp}.csv"
    with product_report.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "Product ID",
                "Internal Reference",
                "Name",
                "Internal Category",
                "Old Website Categories",
                "New Website Category",
                "Status",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)
    with rename_report.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["Category ID", "Old Path", "New Label", "Status"],
        )
        writer.writeheader()
        writer.writerows(rename_rows)

    statuses = Counter(row["Status"] for row in rows)
    print(
        {
            "mode": "apply" if args.apply else "dry_run",
            "products_reviewed": len(products),
            "target_families": len(target_paths),
            "canonical_conflicts": conflicts,
            "statuses": dict(statuses),
            "renames": len(rename_rows),
            "product_report": str(product_report),
            "rename_report": str(rename_report),
        }
    )
    return int(bool(statuses.get("Needs review")))


if __name__ == "__main__":
    raise SystemExit(main())

