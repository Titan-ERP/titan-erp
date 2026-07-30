"""Read-only audit of Odoo product taxonomy and storefront search labels."""

from __future__ import annotations

import csv
import re
from collections import Counter
from datetime import datetime

from odoo_cleanup_published_placeholders import OUT_DIR, connect, execute


WEAK_TERMS = re.compile(
    r"\b(?:misc(?:ellaneous)?|other|uncategorized|unknown|tbd)\b",
    re.IGNORECASE,
)
GENERIC_PRODUCT_NAMES = {
    "adapter",
    "alternator",
    "bearing",
    "belt",
    "bolt",
    "filter",
    "fitting",
    "gasket",
    "hose",
    "kit",
    "nut",
    "part",
    "pin",
    "pump",
    "seal",
    "sensor",
    "switch",
    "washer",
}


def chunks(values, size=500):
    for offset in range(0, len(values), size):
        yield values[offset : offset + size]


def write_csv(path, rows, fields):
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    db, uid, key, models = connect()
    fields = execute(
        models,
        db,
        uid,
        key,
        "product.template",
        "fields_get",
        [],
        {"attributes": ["readonly"]},
    )
    published_field = (
        "website_published" if "website_published" in fields else "is_published"
    )

    public_categories = execute(
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
    public_by_id = {row["id"]: row for row in public_categories}
    children = {row["id"]: [] for row in public_categories}
    for row in public_categories:
        if row.get("parent_id"):
            children.setdefault(row["parent_id"][0], []).append(row["id"])

    def descendant_ids(category_id):
        result = {category_id}
        pending = list(children.get(category_id, []))
        while pending:
            child_id = pending.pop()
            if child_id in result:
                continue
            result.add(child_id)
            pending.extend(children.get(child_id, []))
        return sorted(result)

    def public_path(category_id):
        parts = []
        seen = set()
        current = public_by_id.get(category_id)
        while current and current["id"] not in seen:
            seen.add(current["id"])
            parts.append(current["name"])
            parent = current.get("parent_id")
            current = public_by_id.get(parent[0]) if parent else None
        return " / ".join(reversed(parts))

    public_rows = []
    for category in public_categories:
        active_count = execute(
            models,
            db,
            uid,
            key,
            "product.template",
            "search_count",
            [[("active", "=", True), ("public_categ_ids", "in", [category["id"]])]],
        )
        published_count = execute(
            models,
            db,
            uid,
            key,
            "product.template",
            "search_count",
            [
                [
                    ("active", "=", True),
                    (published_field, "=", True),
                    ("public_categ_ids", "in", [category["id"]]),
                ]
            ],
        )
        subtree_active_count = execute(
            models,
            db,
            uid,
            key,
            "product.template",
            "search_count",
            [
                [
                    ("active", "=", True),
                    ("public_categ_ids", "in", descendant_ids(category["id"])),
                ]
            ],
        )
        path = public_path(category["id"])
        issues = []
        if WEAK_TERMS.search(category["name"]):
            issues.append("Weak label")
        if subtree_active_count == 0:
            issues.append("Empty category")
        public_rows.append(
            {
                "Category ID": category["id"],
                "Category Path": path,
                "Active Products": active_count,
                "Subtree Active Products": subtree_active_count,
                "Published Products": published_count,
                "Issues": "; ".join(issues),
            }
        )

    duplicate_leaf_names = {
        name.lower(): count
        for name, count in Counter(
            row["name"].strip() for row in public_categories
        ).items()
        if count > 1
    }
    for row in public_rows:
        leaf = row["Category Path"].split(" / ")[-1].lower()
        if leaf in duplicate_leaf_names:
            row["Issues"] = "; ".join(
                filter(None, [row["Issues"], "Duplicate leaf label"])
            )

    product_ids = execute(
        models,
        db,
        uid,
        key,
        "product.template",
        "search",
        [
            [
                ("active", "=", True),
                (published_field, "=", True),
            ]
        ],
        {"limit": 0, "order": "name,id"},
    )
    products = []
    for id_chunk in chunks(product_ids):
        products.extend(
            execute(
                models,
                db,
                uid,
                key,
                "product.template",
                "read",
                [id_chunk],
                {"fields": ["id", "default_code", "name", "public_categ_ids"]},
            )
        )
    name_counts = Counter(
        re.sub(r"\s+", " ", (row.get("name") or "").strip()).lower()
        for row in products
    )
    product_rows = []
    for product in products:
        name = re.sub(r"\s+", " ", (product.get("name") or "").strip())
        issues = []
        if name.lower() in GENERIC_PRODUCT_NAMES:
            issues.append("Generic name")
        if name_counts[name.lower()] > 1:
            issues.append("Duplicate published name")
        if not product.get("default_code"):
            issues.append("Missing internal reference")
        if issues:
            product_rows.append(
                {
                    "Product ID": product["id"],
                    "Internal Reference": product.get("default_code") or "",
                    "Name": name,
                    "Website Categories": "; ".join(
                        public_path(category_id)
                        for category_id in product.get("public_categ_ids", [])
                    ),
                    "Issues": "; ".join(issues),
                }
            )

    OUT_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    category_report = OUT_DIR / f"taxonomy_category_audit_{stamp}.csv"
    product_report = OUT_DIR / f"taxonomy_product_name_audit_{stamp}.csv"
    write_csv(
        category_report,
        public_rows,
        [
            "Category ID",
            "Category Path",
            "Active Products",
            "Subtree Active Products",
            "Published Products",
            "Issues",
        ],
    )
    write_csv(
        product_report,
        product_rows,
        [
            "Product ID",
            "Internal Reference",
            "Name",
            "Website Categories",
            "Issues",
        ],
    )
    print(
        {
            "website_categories": len(public_rows),
            "categories_with_issues": sum(bool(row["Issues"]) for row in public_rows),
            "empty_categories": sum(
                "Empty category" in row["Issues"] for row in public_rows
            ),
            "weak_label_categories": sum(
                "Weak label" in row["Issues"] for row in public_rows
            ),
            "published_products": len(products),
            "published_without_exactly_one_category": sum(
                len(row.get("public_categ_ids", [])) != 1 for row in products
            ),
            "products_with_name_issues": len(product_rows),
            "category_report": str(category_report),
            "product_report": str(product_report),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

