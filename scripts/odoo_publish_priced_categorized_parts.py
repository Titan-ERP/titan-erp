"""Publish active saleable products that are already priced and categorized.

This script only writes Odoo website publish flags. It does not change prices,
names, internal categories, or public categories.
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

from odoo_website_taxonomy_agent import clean_text, is_service_or_hidden, rel_name


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
REPORT_DIR = ROOT / "odoo_imports/product_master/review_reports"


def load_env() -> None:
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ[key.strip()] = value.strip().strip('"').strip("'")


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
    for attempt in range(1, 4):
        try:
            return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})
        except (OSError, TimeoutError, xmlrpc.client.ProtocolError):
            if attempt == 3:
                raise
            time.sleep(2 * attempt)


def chunks(values: list[int], size: int) -> list[list[int]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish priced categorized parts.")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--allow-missing-images",
        action="store_true",
        help="Allow publication when a priced, categorized, customer-ready product has no image.",
    )
    parser.add_argument(
        "--allow-missing-descriptions",
        action="store_true",
        help="Allow publication when a priced, categorized product has no customer description yet.",
    )
    args = parser.parse_args()

    db, uid, api_key, models = connect()
    fields_get = execute(models, db, uid, api_key, "product.template", "fields_get", [], {"attributes": ["type"]})
    product_type_field = "detailed_type" if "detailed_type" in fields_get else "type"
    publish_values: dict[str, Any] = {}
    if "is_published" in fields_get:
        publish_values["is_published"] = True
    if "website_published" in fields_get:
        publish_values["website_published"] = True
    description_fields = [
        field
        for field in ("description_ecommerce", "website_description", "description_sale")
        if field in fields_get
    ]
    fields = [
        "id",
        "default_code",
        "name",
        "list_price",
        "categ_id",
        "public_categ_ids",
        "image_1920",
        "is_published",
        "website_published",
        product_type_field,
        *description_fields,
    ]
    fields = [field for field in fields if field in fields_get]
    domain = [
        ("active", "=", True),
        ("sale_ok", "=", True),
        ("list_price", ">", 1.0),
        ("public_categ_ids", "!=", False),
    ]
    product_ids = execute(models, db, uid, api_key, "product.template", "search", [domain], {"limit": args.limit or 0, "context": {"active_test": False}})
    rows: list[dict[str, Any]] = []
    publish_ids: list[int] = []
    skipped_hidden = 0
    skipped_published = 0
    skipped_no_image = 0
    skipped_missing_description = 0
    skipped_internal_copy = 0
    for chunk in chunks(product_ids, 500):
        products = execute(
            models,
            db,
            uid,
            api_key,
            "product.template",
            "read",
            [chunk],
            {"fields": fields, "context": {"active_test": False, "bin_size": True}},
        )
        for product in products:
            row = {
                "Internal Reference": clean_text(product.get("default_code")),
                "Product Name": clean_text(product.get("name")),
                "Product Category": rel_name(product.get("categ_id")),
                "Product Type": clean_text(product.get(product_type_field)),
            }
            hidden, hidden_reason = is_service_or_hidden(row)
            already_published = bool(product.get("is_published")) or bool(product.get("website_published"))
            has_image = bool(product.get("image_1920"))
            has_description = any(str(product.get(field) or "").strip() for field in description_fields)
            internal_copy = any(
                marker.lower() in str(product.get(field) or "").lower()
                for field in description_fields
                for marker in (
                    "Detail enrichment pending",
                    "Pricing requires separate review",
                    "Public Blumaq page harvested",
                    "Source URL",
                    "harvested",
                )
            )
            status = "Ready To Publish"
            if hidden:
                skipped_hidden += 1
                status = "Skipped Hidden"
            elif already_published:
                skipped_published += 1
                status = "Already Published"
            elif not has_image and not args.allow_missing_images:
                skipped_no_image += 1
                status = "Skipped No Image"
            elif internal_copy:
                skipped_internal_copy += 1
                status = "Skipped Internal Copy"
            elif not has_description and not args.allow_missing_descriptions:
                skipped_missing_description += 1
                status = "Skipped Missing Customer Description"
            else:
                publish_ids.append(product["id"])
            rows.append(
                {
                    "Product ID": product["id"],
                    "Internal Reference": row["Internal Reference"],
                    "Product Name": row["Product Name"],
                    "Sales Price": product.get("list_price") or 0,
                    "Public Category Count": len(product.get("public_categ_ids") or []),
                    "Status": status,
                    "Notes": hidden_reason,
                }
            )

    if args.apply:
        for chunk in chunks(publish_ids, 500):
            execute(models, db, uid, api_key, "product.template", "write", [chunk, publish_values])

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"odoo_publish_priced_categorized_parts_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=["Product ID", "Internal Reference", "Product Name", "Sales Price", "Public Category Count", "Status", "Notes"])
        writer.writeheader()
        writer.writerows(rows)

    print(
        {
            "mode": "apply" if args.apply else "dry_run",
            "priced_categorized_checked": len(product_ids),
            "ready_to_publish": len(publish_ids),
            "already_published": skipped_published,
            "hidden_skipped": skipped_hidden,
            "skipped_no_image": skipped_no_image,
            "skipped_internal_copy": skipped_internal_copy,
            "skipped_missing_description": skipped_missing_description,
            "published_now": len(publish_ids) if args.apply else 0,
            "report": str(path),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

