"""Read-only status report for Southern Odoo ecommerce product readiness."""

from __future__ import annotations

import csv
import os
import socket
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
import xmlrpc.client

from odoo_website_taxonomy_agent import clean_text, is_service_or_hidden, map_category, rel_name


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
REPORT_DIR = ROOT / "odoo_imports/product_master/review_reports"


def load_env() -> None:
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def connect():
    socket.setdefaulttimeout(90)
    load_env()
    url = os.environ["ODOO_URL"].rstrip("/")
    db = os.environ["ODOO_DB"]
    username = os.environ["ODOO_USERNAME"]
    api_key = os.environ["ODOO_API_KEY"]
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Odoo authentication failed.")
    return db, uid, api_key, xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")


def execute(models, db, uid, api_key, model: str, method: str, args: list[Any], kwargs: dict[str, Any] | None = None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def row_from_product(product: dict[str, Any], product_type_field: str) -> dict[str, str]:
    return {
        "Internal Reference": clean_text(product.get("default_code")),
        "Product Name": clean_text(product.get("name")),
        "Product Category": rel_name(product.get("categ_id")),
        "Product Type": clean_text(product.get(product_type_field)),
        "Sales Price": str(product.get("list_price") or 0),
    }


def main() -> int:
    db, uid, api_key, models = connect()
    fields_get = execute(models, db, uid, api_key, "product.template", "fields_get", [], {"attributes": ["type"]})
    product_type_field = "detailed_type" if "detailed_type" in fields_get else "type"
    fields = [
        "id",
        "default_code",
        "name",
        "list_price",
        "sale_ok",
        "active",
        "categ_id",
        "public_categ_ids",
        "is_published",
        "website_published",
        product_type_field,
    ]
    fields = [field for field in fields if field in fields_get]
    product_ids = execute(models, db, uid, api_key, "product.template", "search", [[("active", "=", True), ("sale_ok", "=", True)]], {"context": {"active_test": False}})

    counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    review_rows: list[dict[str, Any]] = []
    for offset in range(0, len(product_ids), 500):
        products = execute(models, db, uid, api_key, "product.template", "read", [product_ids[offset : offset + 500]], {"fields": fields, "context": {"active_test": False}})
        for product in products:
            row = row_from_product(product, product_type_field)
            hidden, _ = is_service_or_hidden(row)
            website_category, action, notes = map_category(row)
            price = float(product.get("list_price") or 0)
            published = bool(product.get("is_published")) or bool(product.get("website_published"))
            has_public_category = bool(product.get("public_categ_ids"))
            counts["active_saleable"] += 1
            if price > 1.0:
                counts["priced_over_1"] += 1
            else:
                counts["placeholder_price"] += 1
            if published:
                counts["published"] += 1
            if has_public_category:
                counts["has_public_category"] += 1
            if hidden:
                counts["hidden_service_like"] += 1
            elif action == "Review":
                counts["taxonomy_review"] += 1
                review_rows.append(
                    {
                        "Product ID": product["id"],
                        "Internal Reference": row["Internal Reference"],
                        "Product Name": row["Product Name"],
                        "Internal Category": row["Product Category"],
                        "Sales Price": price,
                        "Published": "Yes" if published else "No",
                        "Notes": notes,
                    }
                )
            elif price > 1.0 and has_public_category and published:
                counts["published_priced_with_category"] += 1
            if website_category:
                category_counts[website_category] += 1

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    review_path = REPORT_DIR / f"odoo_ecommerce_taxonomy_review_remaining_{stamp}.csv"
    with review_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=["Product ID", "Internal Reference", "Product Name", "Internal Category", "Sales Price", "Published", "Notes"])
        writer.writeheader()
        writer.writerows(review_rows)

    print(dict(counts))
    print("top_website_taxonomy_categories:")
    for category, count in category_counts.most_common(20):
        print(f"{count}: {category}")
    print(f"review_report: {review_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
