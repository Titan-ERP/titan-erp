"""Hide unfinished product records from website category browsing.

This script targets products that are not ready for the public catalog but still
have a website category. It preserves the product, inventory, internal
reference, costs, vendor lines, and images. The only storefront-facing writes are
to remove public website categories, unpublish if needed, and replace raw public
copy with a short internal review note.
"""

from __future__ import annotations

import argparse
import csv
import os
from datetime import datetime
from pathlib import Path
from typing import Any
import xmlrpc.client


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
OUT_DIR = ROOT / "outputs"

INTERNAL_MARKERS = (
    "Detail enrichment pending",
    "Pricing requires separate review",
    "Public Blumaq page harvested",
    "Sparex source:",
    "Source URL:",
)


def load_env() -> None:
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def connect():
    load_env()
    url = os.environ["ODOO_URL"].rstrip("/")
    db = os.environ["ODOO_DB"]
    username = os.environ["ODOO_USERNAME"]
    api_key = os.environ["ODOO_API_KEY"]
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(
        db, username, api_key, {}
    )
    if not uid:
        raise RuntimeError("Odoo authentication failed")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    return db, uid, api_key, models


def execute(models, db, uid, api_key, model: str, method: str, args: list[Any], kwargs: dict[str, Any] | None = None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def chunks(values: list[int], size: int = 400):
    for index in range(0, len(values), size):
        yield values[index : index + size]


def marker_domain(fields: list[str]) -> list[Any]:
    clauses: list[list[Any]] = []
    for field in fields:
        for marker in INTERNAL_MARKERS:
            clauses.append([(field, "ilike", marker)])
    if not clauses:
        return []
    return ["|"] * (len(clauses) - 1) + [token for clause in clauses for token in clause]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    db, uid, api_key, models = connect()
    fields_get = execute(
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
        field
        for field in ("is_published", "website_published")
        if field in fields_get and not fields_get[field].get("readonly")
    ]
    description_fields = [
        field
        for field in ("description_ecommerce", "website_description", "description_sale")
        if field in fields_get and not fields_get[field].get("readonly")
    ]

    unsafe_clauses: list[list[Any]] = [[("list_price", "<=", 1.0)]]
    internal_copy_domain = marker_domain(description_fields)
    unsafe_domain: list[Any]
    if internal_copy_domain:
        unsafe_domain = ["|", ("list_price", "<=", 1.0), *internal_copy_domain]
    else:
        unsafe_domain = [("list_price", "<=", 1.0)]

    domain: list[Any] = [
        ("active", "=", True),
        ("sale_ok", "=", True),
        ("public_categ_ids", "!=", False),
        *unsafe_domain,
    ]
    product_ids = execute(
        models,
        db,
        uid,
        api_key,
        "product.template",
        "search",
        [domain],
        {"limit": args.limit or 0, "order": "id"},
    )

    read_fields = [
        "id",
        "default_code",
        "name",
        "list_price",
        "public_categ_ids",
        "is_published",
        "website_published",
        *description_fields,
    ]
    read_fields = [field for field in read_fields if field in fields_get]
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
            reasons = []
            if float(product.get("list_price") or 0) <= 1.0:
                reasons.append("Placeholder price <= 1")
            for field in description_fields:
                text = str(product.get(field) or "")
                if any(marker.lower() in text.lower() for marker in INTERNAL_MARKERS):
                    reasons.append(f"Internal copy in {field}")
            rows.append(
                {
                    "Product ID": product["id"],
                    "Internal Reference": product.get("default_code") or "",
                    "Name": product.get("name") or "",
                    "Sales Price": product.get("list_price") or 0,
                    "Website Category Count": len(product.get("public_categ_ids") or []),
                    "Was Published": bool(product.get("is_published") or product.get("website_published")),
                    "Reason": "; ".join(reasons),
                    "Action": "Hidden from website categories" if args.apply else "Would hide from website categories",
                }
            )

    if args.apply and product_ids:
        review_note = (
            "Internal catalog record. Not published to the website until pricing, "
            "description, and product media are reviewed."
        )
        values: dict[str, Any] = {"public_categ_ids": [(5, 0, 0)]}
        values.update({field: False for field in publish_fields})
        for field in description_fields:
            values[field] = review_note
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

    OUT_DIR.mkdir(exist_ok=True)
    report_path = OUT_DIR / f"unclean_website_category_cleanup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    with report_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "Product ID",
                "Internal Reference",
                "Name",
                "Sales Price",
                "Website Category Count",
                "Was Published",
                "Reason",
                "Action",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    remaining = execute(
        models,
        db,
        uid,
        api_key,
        "product.template",
        "search_count",
        [domain],
    )
    print(
        {
            "mode": "apply" if args.apply else "dry_run",
            "matched": len(product_ids),
            "updated": len(product_ids) if args.apply else 0,
            "remaining_matches": remaining,
            "report": str(report_path),
        }
    )
    if args.apply and remaining:
        raise RuntimeError(f"{remaining} unclean categorized products remain")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
