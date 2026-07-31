"""Apply Southern website taxonomy and publish eligible priced parts.

Default mode is dry-run. With --apply, this script:
- creates/verifies public website categories from taxonomy rules,
- adds the recommended public category to eligible products,
- and, only with the additional --publish flag, publishes products that are
  active, saleable, non-service, customer-ready, priced
  above $1, and not flagged as service/membership/rental.

It does not change internal product categories, costs, names, or prices.
"""

from __future__ import annotations

import argparse
import csv
import os
import socket
import time
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


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required setting: {name}")
    return value


def connect():
    socket.setdefaulttimeout(90)
    load_env()
    url = required("ODOO_URL").rstrip("/")
    db = required("ODOO_DB")
    username = required("ODOO_USERNAME")
    api_key = required("ODOO_API_KEY")
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Odoo authentication failed.")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    return db, uid, api_key, models


def execute(models, db, uid, api_key, model: str, method: str, args: list[Any], kwargs: dict[str, Any] | None = None):
    last_exc: Exception | None = None
    for attempt in range(1, 4):
        try:
            return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})
        except (OSError, TimeoutError, xmlrpc.client.ProtocolError) as exc:
            last_exc = exc
            if attempt == 3:
                raise
            time.sleep(2 * attempt)
    raise last_exc or RuntimeError("Unknown Odoo XML-RPC failure")


def chunks(values: list[Any], size: int) -> list[list[Any]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def select_website(models, db, uid, api_key, requested: str | None) -> dict[str, Any]:
    websites = execute(models, db, uid, api_key, "website", "search_read", [[]], {"fields": ["id", "name"], "limit": 100})
    if not websites:
        raise SystemExit("No websites found.")
    if requested:
        matches = [website for website in websites if requested.lower() in website["name"].lower()]
        if matches:
            return matches[0]
    southern = [website for website in websites if "southern" in website["name"].lower()]
    return southern[0] if southern else websites[0]


def find_public_category(models, db, uid, api_key, name: str, parent_id: int | None, website_id: int) -> int | None:
    domains = [
        [("name", "=", name), ("parent_id", "=", parent_id or False), ("website_id", "=", website_id)],
        [("name", "=", name), ("parent_id", "=", parent_id or False), ("website_id", "=", False)],
    ]
    for domain in domains:
        ids = execute(models, db, uid, api_key, "product.public.category", "search", [domain], {"limit": 1})
        if ids:
            return ids[0]
    return None


def ensure_public_category_path(
    models,
    db,
    uid,
    api_key,
    complete_name: str,
    website_id: int,
    apply: bool,
    cache: dict[str, int | None],
) -> int | None:
    if complete_name in cache:
        return cache[complete_name]
    parent_id = None
    parts = [clean_text(part) for part in complete_name.split("/") if clean_text(part)]
    for part in parts:
        category_id = find_public_category(models, db, uid, api_key, part, parent_id, website_id)
        if not category_id:
            if not apply:
                cache[complete_name] = None
                return None
            values: dict[str, Any] = {"name": part, "website_id": website_id}
            if parent_id:
                values["parent_id"] = parent_id
            category_id = execute(models, db, uid, api_key, "product.public.category", "create", [values])
        parent_id = category_id
    cache[complete_name] = parent_id
    return parent_id


def product_row(product: dict[str, Any], product_type_field: str) -> dict[str, str]:
    return {
        "Internal Reference": clean_text(product.get("default_code")),
        "Product Name": clean_text(product.get("name")),
        "Product Family": "",
        "Product Category": rel_name(product.get("categ_id")),
        "Product Type": clean_text(product.get(product_type_field)),
        "Sales Price": str(product.get("list_price") or 0),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply website taxonomy and optionally publish eligible priced parts.")
    parser.add_argument("--apply", action="store_true", help="Write category assignments.")
    parser.add_argument("--publish", action="store_true", help="Publish customer-ready products too. Requires --apply.")
    parser.add_argument("--website-name", default="Southern")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--only-unpublished", action="store_true", help="Only check currently unpublished products.")
    parser.add_argument("--replace-categories", action="store_true", help="Replace public categories instead of adding one.")
    args = parser.parse_args()
    if args.publish and not args.apply:
        raise SystemExit("--publish requires --apply.")

    db, uid, api_key, models = connect()
    website = select_website(models, db, uid, api_key, args.website_name)
    fields_get = execute(models, db, uid, api_key, "product.template", "fields_get", [], {"attributes": ["type"]})
    product_type_field = "detailed_type" if "detailed_type" in fields_get else "type"
    publish_values: dict[str, Any] = {}
    if "is_published" in fields_get:
        publish_values["is_published"] = True
    if "website_published" in fields_get:
        publish_values["website_published"] = True
    if not publish_values:
        raise SystemExit("No publish field found on product.template.")

    wanted = [
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
    description_fields = [
        name
        for name in (
            "description_ecommerce",
            "website_description",
            "description_sale",
        )
        if name in fields_get
    ]
    wanted.extend(description_fields)
    wanted = [field for field in wanted if field in fields_get]
    domain = [("active", "=", True), ("sale_ok", "=", True)]
    if args.only_unpublished:
        if "is_published" in fields_get:
            domain.append(("is_published", "=", False))
        elif "website_published" in fields_get:
            domain.append(("website_published", "=", False))
    product_ids = execute(
        models,
        db,
        uid,
        api_key,
        "product.template",
        "search",
        [domain],
        {"limit": args.limit or 0, "context": {"active_test": False}, "order": "default_code asc,id asc"},
    )

    rows: list[dict[str, Any]] = []
    category_cache: dict[str, int | None] = {}
    to_publish: list[int] = []
    category_write_groups: dict[int, list[int]] = {}
    skipped = 0

    for id_chunk in chunks(product_ids, 250):
        products = execute(models, db, uid, api_key, "product.template", "read", [id_chunk], {"fields": wanted, "context": {"active_test": False}})
        for product in products:
            row = product_row(product, product_type_field)
            hidden, hidden_reason = is_service_or_hidden(row)
            website_category, action, notes = map_category(row)
            price = float(product.get("list_price") or 0)
            already_published = bool(product.get("is_published")) or bool(product.get("website_published"))
            current_public_ids = product.get("public_categ_ids") or []
            internal_copy = any(
                marker.lower() in str(product.get(field) or "").lower()
                for field in description_fields
                for marker in (
                    "Detail enrichment pending",
                    "Pricing requires separate review",
                    "Public Blumaq page harvested",
                )
            )
            category_id = None
            status = "Ready"
            if hidden:
                skipped += 1
                status = "Skipped Hidden"
                notes = hidden_reason
            elif action == "Review" or not website_category:
                skipped += 1
                status = "Skipped Review"
            elif price <= 1.0:
                skipped += 1
                status = "Skipped Placeholder Price"
            else:
                category_id = ensure_public_category_path(models, db, uid, api_key, website_category, website["id"], args.apply, category_cache)
                needs_category = bool(category_id) and (args.replace_categories or category_id not in current_public_ids)
                if needs_category and category_id:
                    category_write_groups.setdefault(category_id, []).append(product["id"])
                if args.publish and not already_published and not internal_copy:
                    to_publish.append(product["id"])
                if internal_copy:
                    status = "Category Only - Copy Not Ready"
                elif args.publish and not already_published:
                    status = "Publish + Category"
                else:
                    status = "Category Only/Already Published"

            rows.append(
                {
                    "Product ID": product["id"],
                    "Internal Reference": row["Internal Reference"],
                    "Product Name": row["Product Name"],
                    "Product Type": row["Product Type"],
                    "Internal Category": row["Product Category"],
                    "Website Category": website_category,
                    "Sales Price": price,
                    "Already Published": "Yes" if already_published else "No",
                    "Current Public Category Count": len(current_public_ids),
                    "Status": status,
                    "Notes": notes,
                }
            )

    category_write_count = sum(len(ids) for ids in category_write_groups.values())
    if args.apply and category_write_groups:
        for category_id, product_ids_for_category in category_write_groups.items():
            command = [(6, 0, [category_id])] if args.replace_categories else [(4, category_id)]
            for id_chunk in chunks(product_ids_for_category, 500):
                execute(models, db, uid, api_key, "product.template", "write", [id_chunk, {"public_categ_ids": command}])

    if args.apply and args.publish and to_publish:
        for id_chunk in chunks(to_publish, 500):
            execute(models, db, uid, api_key, "product.template", "write", [id_chunk, publish_values])

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / f"odoo_apply_website_taxonomy_and_publish_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    with report_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "Product ID",
                "Internal Reference",
                "Product Name",
                "Product Type",
                "Internal Category",
                "Website Category",
                "Sales Price",
                "Already Published",
                "Current Public Category Count",
                "Status",
                "Notes",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    ready = sum(1 for row in rows if row["Status"] in {"Publish + Category", "Category Only/Already Published"})
    print(
        {
            "mode": "apply" if args.apply else "dry_run",
            "website": f"{website['name']} ({website['id']})",
            "products_checked": len(product_ids),
            "ready_priced_parts": ready,
            "skipped": skipped,
            "published_now": len(to_publish) if args.apply and args.publish else 0,
            "category_writes": category_write_count if args.apply else 0,
            "report": str(report_path),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
