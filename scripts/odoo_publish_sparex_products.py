from __future__ import annotations

import argparse
import csv
import os
import sys
import xmlrpc.client
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
REPORT_DIR = ROOT / "odoo_imports" / "product_master" / "sparex" / "run_reports"


def load_env() -> None:
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def execute(models, db, uid, api_key, model: str, method: str, args: list[Any], kwargs: dict[str, Any] | None = None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def chunks(values: list[Any], size: int) -> list[list[Any]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def has_binary(value: Any) -> bool:
    if value in (False, None, ""):
        return False
    if isinstance(value, str):
        return value not in {"0", "False", "false"}
    return bool(value)


def main() -> None:
    parser = argparse.ArgumentParser(description="Safely publish active Sparex products to the Odoo website.")
    parser.add_argument("--apply", action="store_true", help="Publish eligible products. Default is a dry run.")
    parser.add_argument("--all-active-sale-products", action="store_true", help="Publish all active sale_ok products, not only Sparex S.* SKUs.")
    parser.add_argument("--require-image", action="store_true", help="Only publish products that already have an image.")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    load_env()
    url = os.environ["ODOO_URL"].rstrip("/")
    db = os.environ["ODOO_DB"]
    username = os.environ["ODOO_USERNAME"]
    api_key = os.environ["ODOO_API_KEY"]
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Authentication failed.")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

    fields = execute(models, db, uid, api_key, "product.template", "fields_get", [], {"attributes": ["type"]})
    publish_values: dict[str, Any] = {}
    if "is_published" in fields:
        publish_values["is_published"] = True
    if "website_published" in fields:
        publish_values["website_published"] = True
    if not publish_values:
        raise SystemExit("No writable publish field found on product.template.")

    domain: list[Any] = [("active", "=", True), ("sale_ok", "=", True)]
    if not args.all_active_sale_products:
        domain.append(("default_code", "=ilike", "S.%"))

    product_ids = execute(
        models,
        db,
        uid,
        api_key,
        "product.template",
        "search",
        [domain],
        {"context": {"active_test": False}, "limit": args.limit or 0},
    )
    rows = []
    published = 0
    eligible = 0
    skipped_already = 0
    skipped_no_image = 0
    skipped_placeholder_price = 0
    skipped_uncategorized = 0
    skipped_internal_copy = 0
    skipped_missing_description = 0
    description_fields = [
        name
        for name in (
            "description_ecommerce",
            "website_description",
            "description_sale",
        )
        if name in fields
    ]
    for id_chunk in chunks(product_ids, 500):
        products = execute(
            models,
            db,
            uid,
            api_key,
            "product.template",
            "read",
            [id_chunk],
            {
                "fields": [
                    "id",
                    "default_code",
                    "name",
                    "list_price",
                    "public_categ_ids",
                    "image_1920",
                    "is_published",
                    "website_published",
                    *description_fields,
                ],
                "context": {"active_test": False, "bin_size": True},
            },
        )
        publish_ids = []
        for product in products:
            has_image = has_binary(product.get("image_1920"))
            has_category = bool(product.get("public_categ_ids"))
            price = float(product.get("list_price") or 0)
            already_published = bool(product.get("is_published")) or bool(product.get("website_published"))
            internal_copy = any(
                marker.lower() in str(product.get(field) or "").lower()
                for field in description_fields
                for marker in (
                    "Detail enrichment pending",
                    "Pricing requires separate review",
                    "Public Blumaq page harvested",
                )
            )
            has_description = any(
                str(product.get(field) or "").strip()
                for field in description_fields
            )
            status = "Published" if args.apply else "Would Publish"
            if price <= 1.0:
                skipped_placeholder_price += 1
                status = "Skipped Placeholder Price"
            elif not has_category:
                skipped_uncategorized += 1
                status = "Skipped No Website Category"
            elif internal_copy:
                skipped_internal_copy += 1
                status = "Skipped Internal Enrichment Copy"
            elif not has_description:
                skipped_missing_description += 1
                status = "Skipped Missing Customer Description"
            elif args.require_image and not has_image:
                skipped_no_image += 1
                status = "Skipped No Image"
            elif already_published:
                skipped_already += 1
                status = "Already Published"
            else:
                eligible += 1
                if args.apply:
                    publish_ids.append(product["id"])
            rows.append(
                {
                    "Product ID": product["id"],
                    "Internal Reference": product.get("default_code") or "",
                    "Name": product.get("name") or "",
                    "Sales Price": price,
                    "Has Website Category": "Yes" if has_category else "No",
                    "Has Image": "Yes" if has_image else "No",
                    "Status": status,
                }
            )
        if publish_ids:
            execute(models, db, uid, api_key, "product.template", "write", [publish_ids, publish_values])
            published += len(publish_ids)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    result_path = REPORT_DIR / f"sparex_publish_products_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    with result_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "Product ID",
                "Internal Reference",
                "Name",
                "Sales Price",
                "Has Website Category",
                "Has Image",
                "Status",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"Mode: {'APPLY' if args.apply else 'DRY RUN'}")
    print(f"Candidates: {len(product_ids)}")
    print(f"Eligible: {eligible}")
    print(f"Published now: {published}")
    print(f"Already published: {skipped_already}")
    print(f"Skipped no image: {skipped_no_image}")
    print(f"Skipped placeholder price: {skipped_placeholder_price}")
    print(f"Skipped no website category: {skipped_uncategorized}")
    print(f"Skipped internal enrichment copy: {skipped_internal_copy}")
    print(f"Skipped missing customer description: {skipped_missing_description}")
    print(f"Results: {result_path}")


if __name__ == "__main__":
    try:
        main()
    except xmlrpc.client.Fault as exc:
        print(f"Odoo XML-RPC fault: {exc}", file=sys.stderr)
        raise SystemExit(1)
